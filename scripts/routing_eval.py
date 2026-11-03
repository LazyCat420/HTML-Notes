#!/usr/bin/env python3
"""Routing A/B eval: what does html-notes decide for real asks?

Rows: tests/routing_eval.json (real asks mined from the deployed chat history,
labelled with the expected widget type, music kind and subject).

Modes
  router  — call the real route_with_llm(message, "") for every row (live local
            vLLM, no canvas context). Measures the LLM's own understanding and
            its latency.
  system  — drive the real POST /session/message in-process (TestClient) and
            read the FIRST debug frame (which lane fired) plus, for music, the
            data-hn-config the server stamped on the widget. Measures what the
            user actually gets. Stops reading as soon as it has both, so slow
            builders (news research, YouTube search) don't run to completion.

    ../../.venv/bin/python scripts/routing_eval.py --mode router --out /tmp/r.json
    ../../.venv/bin/python scripts/routing_eval.py --mode system --out /tmp/s.json

Prints per-row PASS/FAIL and a summary (accuracy overall and per expected
type, p50/p95 latency). Exit code is always 0: this is a measuring tool; the
gate lives in tests/test_routing_eval_set.py.
"""
import argparse
import asyncio
import html as _html
import json
import os
import pathlib
import re
import statistics
import sys
import tempfile
import time
import uuid

ROOT = pathlib.Path(os.environ.get("HN_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
EVAL_FILE = pathlib.Path(__file__).resolve().parent.parent / "tests" / "routing_eval.json"
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("DATABASE_URL", str(pathlib.Path(tempfile.gettempdir()) / "routing_eval.db"))

EVAL = json.loads(EVAL_FILE.read_text())["rows"]

# Lane id_prefix (fast-path debug frame) → router widget type.
PREFIX_TO_TYPE = {
    "music": "music", "video": "video", "youtube": "video", "live": "video",
    "news": "news", "stock-news": "stock_news", "stock-report": "stock_news",
}


def score(row, got_type, got_kind, got_query, got_live=None):
    ok_type = got_type == row["type"]
    ok_kind = True
    if row["type"] == "music" and row.get("kind"):
        # The widget treats kind "" exactly like "genre" (genre mix first,
        # artist on a miss — widgets.js startStream), so "" is right for a
        # genre ask and wrong for an artist ask.
        got = got_kind or ""
        ok_kind = got == row["kind"] or (row["kind"] == "genre" and got == "")
    ok_subj = True
    if row.get("subject"):
        ok_subj = row["subject"].lower() in (got_query or "").lower()
    ok_live = True
    if row.get("live") is not None and got_live is not None:
        ok_live = bool(got_live) == bool(row["live"])
    return ok_type and ok_kind and ok_subj and ok_live, {
        "type": ok_type, "kind": ok_kind, "subject": ok_subj, "live": ok_live}


async def run_router(rows):
    from app.llm import route_with_llm
    out = []
    for row in rows:
        t0 = time.perf_counter()
        plan = await route_with_llm(row["m"], "")
        dt = time.perf_counter() - t0
        w = ((plan or {}).get("widgets") or [{}])[0] if plan else {}
        mods = w.get("modifiers") or {}
        got_type = w.get("type") or ("reply" if (plan or {}).get("reply") else
                                     "defer" if (plan or {}).get("defer") else None)
        got_kind = mods.get("kind", "")
        got_query = w.get("query") or ""
        ok, parts = score(row, got_type, got_kind, got_query, mods.get("live"))
        out.append({"m": row["m"], "ok": ok, "parts": parts, "type": got_type,
                    "kind": got_kind, "query": got_query, "secs": round(dt, 2),
                    "subject_type": ((plan or {}).get("checks") or {}).get("subject_type"),
                    "expect": row})
    return out


def run_system(rows):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    canvas = '<div id="dashboard-grid" class="dashboard-grid"></div>'
    out = []
    for row in rows:
        sid = f"eval-{uuid.uuid4().hex[:8]}"
        t0 = time.perf_counter()
        debug, cfg, first_debug_at, refined = None, None, None, False
        with client.stream("POST", "/session/message", json={
                "session_id": sid, "message": row["m"], "current_canvas": canvas}) as r:
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                if ev.get("type") == "debug" and debug is None:
                    debug, first_debug_at = ev, time.perf_counter() - t0
                    want_music = (ev.get("widget_type") == "mini_music_player"
                                  or "music" in (ev.get("widgets") or []))
                    if not want_music:
                        break
                if ev.get("type") == "component":
                    body = ev.get("html") or ev.get("content") or ""
                    m = re.search(r'data-hn-config=(?:"([^"]*)"|\'([^\']*)\')', body)
                    if m:
                        cfg = json.loads(_html.unescape(m.group(1) or m.group(2)))
                    else:
                        # Builds before data-hn-config: read the x-data literal.
                        x = re.search(r'musicPlayerWidget\(\{([^)]*)\}\)', _html.unescape(body))
                        if x:
                            g = re.search(r'genre:\s*"([^"]*)"', x.group(1))
                            k = re.search(r'kind:\s*"([^"]*)"', x.group(1))
                            cfg = {"genre": g.group(1) if g else "", "kind": k.group(1) if k else ""}
                    # Keep reading: a late router answer sends a SECOND
                    # component (path router-refine) that rebuilds the player.
                    continue
                if ev.get("type") == "debug" and ev.get("path") == "router-refine":
                    refined = True
                if ev.get("type") == "done":
                    break
        debug = debug or {}
        if debug.get("path") == "fast-path":
            got_type = ("open_app" if debug.get("widget_type") == "open_app" else
                        PREFIX_TO_TYPE.get(debug.get("id_prefix"), debug.get("id_prefix")))
            got_query = debug.get("query") or ""
            got_live = None
        elif debug.get("path") == "router":
            got_type = (debug.get("widgets") or [None])[0]
            got_query = (debug.get("queries") or [""])[0] or ""
            got_live = ((debug.get("modifiers") or [{}])[0] or {}).get("live")
        else:
            got_type, got_query, got_live = debug.get("path") or "agent", "", None
        got_kind = (cfg or {}).get("kind", "")
        if cfg and cfg.get("genre"):
            got_query = cfg["genre"]
        ok, parts = score(row, got_type, got_kind, got_query, got_live)
        out.append({"m": row["m"], "ok": ok, "parts": parts,
                    "path": debug.get("path") + ("+refine" if refined else "") if debug.get("path") else None,
                    "type": got_type, "kind": got_kind, "query": got_query,
                    "secs": round(first_debug_at or (time.perf_counter() - t0), 2),
                    "expect": row})
    return out


def summarize(results, label):
    n = len(results)
    ok = sum(r["ok"] for r in results)
    secs = sorted(r["secs"] for r in results)
    p95 = secs[min(n - 1, int(round(0.95 * (n - 1))))] if secs else 0
    by_type = {}
    for r in results:
        t = r["expect"]["type"]
        a, b = by_type.get(t, (0, 0))
        by_type[t] = (a + r["ok"], b + 1)
    print(f"\n== {label}: {ok}/{n} = {100 * ok / max(n, 1):.1f}%  "
          f"latency p50={statistics.median(secs) if secs else 0:.2f}s p95={p95:.2f}s")
    for t, (a, b) in sorted(by_type.items()):
        print(f"   {t:<11} {a}/{b}")
    return {"label": label, "ok": ok, "n": n, "p50": statistics.median(secs) if secs else 0,
            "p95": p95, "by_type": by_type}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["router", "system"], required=True)
    ap.add_argument("--only", default="", help="substring filter on the message")
    ap.add_argument("--type", default="", help="only rows expecting this widget type")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = [r for r in EVAL if args.only.lower() in r["m"].lower()
            and (not args.type or r["type"] == args.type)]
    results = asyncio.run(run_router(rows)) if args.mode == "router" else run_system(rows)
    for r in results:
        flag = "PASS" if r["ok"] else "FAIL"
        bad = ",".join(k for k, v in r["parts"].items() if not v)
        print(f"{flag} {r['secs']:>6.2f}s  {r['m'][:60]:<60} → {r.get('path', 'router')}/"
              f"{r['type']} kind={r['kind']!r} q={r['query'][:40]!r}" + (f"  [{bad}]" if bad else ""))
    summary = summarize(results, args.mode)
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps({"summary": summary, "rows": results}, indent=1))


if __name__ == "__main__":
    main()
