#!/usr/bin/env python3
"""Print every SSE event a turn produces, so "no widget appeared" stops being a
guess.

The failure this exists for: the agent streams text and tool spinners, the user
hears a spoken answer, and the canvas stays empty. From the browser you cannot
tell WHICH of these happened:

  * no `component` event was ever emitted (nothing was committed server-side)
  * a `component` fired but carried a version the client dropped
  * a `component` fired with HTML that has no `.widget-container`

This prints the event sequence, the widget ids and versions of every component
frame, and which tools were called — which distinguishes all three.

Usage:
    python scripts/sse_probe.py "how is the traffic"
    python scripts/sse_probe.py --host http://10.0.0.16:8035 "best sandals"
    python scripts/sse_probe.py --raw "sushi near me"        # full event dump
"""
import argparse
import json
import re
import sys
import time
import uuid

import httpx

WIDGET_ID_RE = re.compile(r'id="([a-z0-9_-]+)"', re.I)
WIDGET_EL_RE = re.compile(r'class="[^"]*widget-container', re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message")
    ap.add_argument("--host", default="http://localhost:8035")
    ap.add_argument("--session", default=None, help="reuse a session to test follow-ups")
    ap.add_argument("--canvas", default='<div id="dashboard-grid"></div>')
    ap.add_argument("--focus", default=None, help="focus_widget_id to send")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--raw", action="store_true", help="dump every event in full")
    args = ap.parse_args()

    session = args.session or f"sse-probe-{uuid.uuid4().hex[:8]}"
    body = {
        "session_id": session,
        "message": args.message,
        "current_canvas": args.canvas,
        "canvas_version": 0,
    }
    if args.focus:
        body["focus_widget_id"] = args.focus

    print(f"→ {args.host}/session/message")
    print(f"  session={session}  message={args.message!r}\n")

    counts: dict = {}
    tools: list = []
    components: list = []
    text_len = 0
    started = time.time()

    with httpx.Client(timeout=args.timeout) as c:
        with c.stream("POST", f"{args.host}/session/message", json=body) as r:
            if r.status_code != 200:
                print(f"HTTP {r.status_code}: {r.read()[:400]!r}")
                return 1
            buf = ""
            for chunk in r.iter_text():
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except Exception:
                        continue
                    et = ev.get("type", "?")
                    counts[et] = counts.get(et, 0) + 1
                    dt = time.time() - started
                    if args.raw:
                        print(f"[{dt:6.2f}s] {json.dumps(ev)[:600]}")
                    if et == "tool_call":
                        tools.append(ev.get("tool", "?"))
                        print(f"[{dt:6.2f}s] tool_call  {ev.get('tool')}")
                    elif et == "component":
                        html = ev.get("content", "") or ""
                        ids = WIDGET_ID_RE.findall(html)
                        n_widgets = len(WIDGET_EL_RE.findall(html))
                        components.append((ev.get("version"), ids, n_widgets))
                        print(f"[{dt:6.2f}s] component  version={ev.get('version')} "
                              f"widget-containers={n_widgets} ids={ids}")
                    elif et == "chunk":
                        text_len += len(ev.get("content", "") or "")
                    elif et in ("status", "debug", "error"):
                        msg = ev.get("message") or json.dumps(
                            {k: v for k, v in ev.items() if k != "type"})
                        print(f"[{dt:6.2f}s] {et:9} {str(msg)[:160]}")

    print("\n─── summary " + "─" * 50)
    print(f"  elapsed        {time.time() - started:.1f}s")
    print(f"  event counts   {counts}")
    print(f"  tools called   {tools or '(none)'}")
    print(f"  text streamed  {text_len} chars")
    if not components:
        print("  component      NONE  ← nothing was committed to the canvas.")
        if tools:
            print("                 Tools ran but none was a canvas tool: the agent")
            print("                 likely picked a prism core tool (create_artifact,")
            print("                 execute_python) over canvas_add_widget.")
        return 2
    for version, ids, n in components:
        if n == 0:
            print(f"  component      version={version} has NO .widget-container "
                  f"→ client renders nothing. ids={ids}")
    print(f"  component      {len(components)} frame(s), "
          f"last version={components[-1][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
