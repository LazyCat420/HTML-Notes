#!/usr/bin/env python3
"""Run the same research question N times and report the RENDER RATE.

Research-turn failures are intermittent — a single green run proves nothing.
This is the number to judge the work on: how often does an agent research turn
actually put a real widget on the canvas, rather than falling back to a text
card or nothing at all.

    python scripts/render_rate.py --host http://10.0.0.16:8035 -n 5 "best sandals"

Distinguishes three outcomes per run:
  REAL     a canvas tool committed a widget (model-chosen or reused id)
  FALLBACK the safety net rendered a card because the turn committed nothing
  NONE     no component frame at all
"""
import argparse
import json
import re
import sys
import time
import uuid

import httpx

# The safety net mints these; a model-authored widget has a different shape.
FALLBACK_ID_RE = re.compile(r'id=\\?"(widget-[0-9a-f]{8}|answer-[0-9a-f]{8})\\?"')
ANY_ID_RE = re.compile(r'id=\\?"([a-z0-9_-]+)\\?"', re.I)


def one_run(host: str, question: str, timeout: float) -> dict:
    sid = f"rate-{uuid.uuid4().hex[:8]}"
    body = {"session_id": sid, "message": question,
            "current_canvas": '<div id="dashboard-grid"></div>',
            "canvas_version": 0}
    started = time.time()
    tools, components, text = [], [], 0
    with httpx.Client(timeout=timeout) as c:
        with c.stream("POST", f"{host}/session/message", json=body) as r:
            buf = ""
            for chunk in r.iter_text():
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if not line.strip().startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line.strip()[6:])
                    except Exception:
                        continue
                    if ev.get("type") == "tool_call":
                        tools.append(ev.get("tool", "?"))
                    elif ev.get("type") == "component":
                        components.append(ev.get("content", "") or "")
                    elif ev.get("type") == "chunk":
                        text += len(ev.get("content", "") or "")
    elapsed = time.time() - started
    if not components:
        outcome = "NONE"
    else:
        ids = [i for i in ANY_ID_RE.findall(components[-1]) if i != "dashboard-grid"]
        outcome = "FALLBACK" if FALLBACK_ID_RE.search(components[-1]) else "REAL"
        if not ids:
            outcome = "NONE"
    return {"session": sid, "outcome": outcome, "elapsed": elapsed,
            "tools": len(tools), "repeats": len(tools) - len(set(tools)),
            "text": text}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--host", default="http://localhost:8035")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args()

    rows = []
    for i in range(args.n):
        try:
            row = one_run(args.host, args.question, args.timeout)
        except Exception as e:
            row = {"session": "-", "outcome": "ERROR", "elapsed": 0,
                   "tools": 0, "repeats": 0, "text": 0, "err": str(e)[:80]}
        rows.append(row)
        print(f"  run {i+1}/{args.n}: {row['outcome']:8} "
              f"{row['elapsed']:6.1f}s  tools={row['tools']:2} "
              f"repeats={row['repeats']:2}  text={row['text']}"
              + (f"  {row.get('err','')}" if row["outcome"] == "ERROR" else ""),
              flush=True)

    real = sum(1 for r in rows if r["outcome"] == "REAL")
    print("\n─── render rate " + "─" * 46)
    print(f"  REAL widget : {real}/{len(rows)}")
    for name in ("FALLBACK", "NONE", "ERROR"):
        n = sum(1 for r in rows if r["outcome"] == name)
        if n:
            print(f"  {name:12}: {n}/{len(rows)}")
    ok = [r for r in rows if r["outcome"] != "ERROR"]
    if ok:
        print(f"  median time : {sorted(r['elapsed'] for r in ok)[len(ok)//2]:.1f}s")
        print(f"  max repeats : {max(r['repeats'] for r in ok)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
