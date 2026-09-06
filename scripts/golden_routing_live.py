#!/usr/bin/env python3
"""Run the golden routing table against the DEPLOYED container.

This is the acceptance gate. It imports `GOLDEN` from tests/test_golden_routing.py
so the offline suite and the live check can never disagree about what "right"
is, POSTs each utterance to /session/message with a fresh session, reads the
first `debug` frame's path/id_prefix, counts widget containers in the last
`component` frame, and prints PASS/FAIL per row. Exit 1 on any FAIL.

    .venv/bin/python scripts/golden_routing_live.py
    .venv/bin/python scripts/golden_routing_live.py --only hello --canvas-finance
    .venv/bin/python scripts/golden_routing_live.py --include-agent

Reads the debug frame — the one instrument that says which builder ran. On
2026-09-05 a whole day of news fixes shipped against builders the owner's asks
never reached; this script exists so that cannot happen quietly again.
"""
import argparse
import json
import pathlib
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tests.test_golden_routing import GOLDEN, EMPTY_CANVAS, FINANCE_CANVAS  # noqa: E402


def run_row(host, row, canvas, timeout):
    sid = f"golden-{uuid.uuid4().hex[:8]}"
    t0 = time.time()
    debug, html, chunks = None, "", []
    with httpx.stream("POST", f"{host}/session/message", timeout=timeout, json={
            "session_id": sid, "message": row.message, "current_canvas": canvas}) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                t = ev.get("type")
                if t == "debug" and debug is None:
                    debug = ev
                elif t == "component":
                    html = ev.get("html") or ev.get("content") or ""
                elif t == "chunk":
                    chunks.append(ev.get("content") or "")
    elapsed = time.time() - t0
    # No component frame at all means the canvas was left exactly as it was —
    # a reply or a no-op. Report that as 0 NEW widgets rather than counting the
    # pre-populated canvas (which is what made the finance-canvas "hello" row
    # read as -2 and FAIL while the app had done the right thing).
    if not html:
        return ((debug or {}).get("path"), (debug or {}).get("id_prefix"), 0, elapsed,
                " ".join(chunks)[:80], False, (debug or {}).get("news_category"))
    widgets = html.count('class="widget-container') + html.count("class='widget-container")
    got_path = (debug or {}).get("path")
    got_prefix = (debug or {}).get("id_prefix")
    return (got_path, got_prefix, widgets, elapsed, " ".join(chunks)[:80], True,
            (debug or {}).get("news_category"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://10.0.0.16:8035")
    ap.add_argument("--only", help="substring filter on the utterance")
    ap.add_argument("--include-agent", action="store_true", help="also run agent rows (slow)")
    ap.add_argument("--canvas-finance", action="store_true",
                    help="pre-populate a finance canvas (the context that biased 'hello')")
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()

    canvas = FINANCE_CANVAS if args.canvas_finance else EMPTY_CANVAS
    rows = [r for r in GOLDEN if (args.include_agent or r.path != "agent")
            and (not args.only or args.only.lower() in r.message.lower())]
    fails = 0
    print(f"{'utterance':44s} {'expect':22s} {'got':22s} {'w':>2s} {'s':>6s}  verdict")
    print("-" * 110)
    for row in rows:
        try:
            got_path, got_prefix, widgets, el, reply, painted, got_category = run_row(args.host, row, canvas, args.timeout)
        except Exception as e:
            print(f"{row.message[:44]:44s} {row.path+'/'+str(row.id_prefix):22s} ERROR {type(e).__name__}")
            fails += 1
            continue
        expect = f"{row.path}/{row.id_prefix}"
        got = f"{got_path}/{got_prefix}"
        if row.category:
            expect += f"[{row.category}]"
            got += f"[{got_category or '-'}]"
        ok = (got_path == row.path) and (row.id_prefix is None or got_prefix == row.id_prefix)
        # The SECTION the builder was handed. "world news" and "top stories"
        # produce identical path/id_prefix frames while making completely
        # different requests, so without this the gate cannot see a section
        # regression at all.
        if row.category is not None:
            ok = ok and (got_category or "") == row.category
        if row.widgets is not None:
            # A populated canvas carries its own widgets: when a frame WAS
            # painted count only the delta; when none was, nothing changed.
            base = (2 if args.canvas_finance else 0) if painted else 0
            ok = ok and (widgets - base) == row.widgets
        fails += 0 if ok else 1
        tail = f"  reply={reply!r}" if got_prefix == "reply" else (f"  {row.note}" if not ok and row.note else "")
        print(f"{row.message[:44]:44s} {expect:22s} {got:22s} {widgets:2d} {el:6.1f}  {'PASS' if ok else 'FAIL'}{tail}")
    print("-" * 110)
    print(f"{len(rows) - fails}/{len(rows)} rows pass")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
