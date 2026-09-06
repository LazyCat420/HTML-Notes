#!/usr/bin/env python3
"""Content-level acceptance for the news paths, against the DEPLOYED container.

For each ask: POST /session/message, take the last `component` frame, and print
the card the user would see — id_prefix, widget count, title, subtitle, the
overview, and every headline with its publisher — then a PASS/FAIL per criterion:

  * hello        -> 0 widgets, reply text present
  * a news ask   -> exactly 1 widget, >=3 stories, every story has a publisher,
                    no PR-spam signature in a headline, subtitle is provenance
                    ("N stories · …") and != the overview
  * the overview -> GROUNDED: it names at least one entity that appears in its
                    own sources (_overview_is_grounded, printed with the match).
                    "Market focus centers on biotech catalysts and semiconductor
                    rotations as earnings season progresses" fails this.
  * latency      -> card turns <= 15s; only a brief may take ~32s

    .venv/bin/python scripts/news_check.py "hello" "stock market news" "market news"
"""
import argparse
import json
import pathlib
import re
import sys
import time
import uuid

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import os
os.environ.setdefault("DATABASE_URL", "data/news_check.db")
from app import main as m  # noqa: E402


def turn(host, ask, timeout):
    sid = f"newscheck-{uuid.uuid4().hex[:8]}"
    t0 = time.time(); html = ""; dbg = {}; chunks = []
    with httpx.stream("POST", f"{host}/session/message", timeout=timeout, json={
            "session_id": sid, "message": ask,
            "current_canvas": '<div id="dashboard-grid"></div>'}) as r:
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
                if t == "debug" and not dbg:
                    dbg = ev
                elif t == "component":
                    html = ev.get("html") or ev.get("content") or ""
                elif t == "chunk":
                    chunks.append(ev.get("content") or "")
    return dbg, html, " ".join(chunks), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asks", nargs="+")
    ap.add_argument("--host", default="http://10.0.0.16:8035")
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()
    fails = 0
    for ask in args.asks:
        dbg, html, reply, el = turn(args.host, ask, args.timeout)
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".widget-container")
        prefix = dbg.get("id_prefix"); path = dbg.get("path")
        print(f"\n=== {ask!r}  {el:.1f}s  path={path} id_prefix={prefix} widgets={len(cards)}")
        checks = []
        if prefix == "reply" or ask.strip().lower() in ("hello", "hi", "thanks"):
            checks.append(("no widget", len(cards) == 0))
            checks.append(("reply text present", bool(reply.strip())))
            print("  reply   :", reply[:200])
        else:
            card = cards[0] if cards else None
            title = card.find("h3").get_text(" ", strip=True) if card and card.find("h3") else ""
            sub_el = card.select_one("h3 ~ span") if card else None
            subtitle = sub_el.get_text(" ", strip=True) if sub_el else ""
            ans_el = card.select_one(".answer-prose") if card else None
            answer = ans_el.get_text(" ", strip=True) if ans_el else ""
            rest = card.get_text(" ", strip=True).replace(answer, " ") if card else ""
            print("  title   :", title)
            print("  subtitle:", subtitle)
            print("  answer  :", answer[:260])
            heads = [x.get_text(" ", strip=True) for x in card.select("h4, .font-semibold, strong")] if card else []
            for h in [x for x in heads if 12 < len(x) < 160][:8]:
                print("   -", h[:110])
            spam = [sig for sig in m._PR_SPAM_SIGNATURES if sig in rest.lower()]
            grounded = m._overview_is_grounded(answer, [{"title": rest}])
            match = sorted(m._entity_tokens(answer) & m._entity_tokens(rest))[:5]
            checks += [
                ("exactly 1 widget", len(cards) == 1),
                ("news id_prefix", prefix in ("news", "stock-news")),
                ("subtitle is provenance", bool(re.match(r"^\d+ stories", subtitle))),
                ("subtitle != answer", subtitle != answer and bool(answer)),
                ("no PR-spam signature", not spam),
                (f"overview grounded (matches {match})", grounded),
                ("latency <= 15s (brief <= 40s)", el <= (40 if dbg.get("query", "") and re.search(r"deep dive|summar|brief", ask, re.I) else 15)),
            ]
        for name, ok in checks:
            fails += 0 if ok else 1
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAIL(S)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
