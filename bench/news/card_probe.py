"""What the DEPLOYED card actually shows for a general news ask, scored by
`editorial_ref.front_page` — the before/after instrument for the top-stories work.

Drives the real /session/message SSE stream against the deployed container and
reads the headlines out of the rendered widget, so it measures what the user
sees rather than what a builder returns. Run it before a change and after:

    .venv/bin/python -m bench.news.card_probe bench/news/results/<date>_<label>.json

Two self-checks, because both halves of this probe have failed before:
  * the metric prints its positive/negative controls every run;
  * extracting 0 items from a non-empty card exits 2 rather than recording a
    0.00 that looks like a verdict about the card (the first draft selected
    `a[href^=http]` and scored every ask 0.00 — the parser, not the news).
"""

import asyncio, json, re, sys, time, uuid, httpx
sys.path.insert(0, "/home/lazycat/github/projects/sun/HTML-Notes")
import os; os.environ.setdefault("DATABASE_URL", "data/bench_news.db")
from bench.news import editorial_ref as E
from bs4 import BeautifulSoup

HOST = "http://10.0.0.16:8035"
ASKS = ["top stories", "top news", "world news", "us news today", "business news", "tech news"]

async def turn(ask):
    sid = f"baseline-{uuid.uuid4().hex[:8]}"; t0=time.time(); html=""; dbg={}
    async with httpx.AsyncClient(timeout=180.0) as c:
        async with c.stream("POST", f"{HOST}/session/message", json={
            "session_id": sid, "message": ask,
            "current_canvas": '<div id="dashboard-grid"></div>', "canvas_version": 0}) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data: "): continue
                try: ev = json.loads(line[6:])
                except Exception: continue
                if ev.get("type") == "debug": dbg = ev
                if ev.get("type") == "component": html = ev.get("html") or ev.get("content") or ""
    soup = BeautifulSoup(html, "html.parser")
    card = soup.select_one(".widget-container")
    heads = [x.get_text(" ", strip=True)
             for x in (card.select("h4, .font-semibold, strong") if card else [])]
    seen=set(); out=[]
    for t in heads:
        if not (12 < len(t) < 200): continue
        k=t.lower()
        if k in seen: continue
        seen.add(k); out.append({"title": t})
    return dbg, out, time.time()-t0, len(html)

async def main():
    ref = await E.fetch_reference(); cal = await E.calibrate(ref)
    print(f"controls positive={cal['positive']:.2f} negative={cal['negative']:.2f} ok={cal['ok']}\n")
    rows=[]
    for ask in ASKS:
        dbg, items, el, hlen = await turn(ask)
        s = E.score(items, ref)
        flag = "  <-- EXTRACTED NOTHING from %d bytes of HTML; probe, not card" % hlen if (hlen and not items) else ""
        print(f"{ask!r:20s} id_prefix={dbg.get('id_prefix','?'):12s} n={len(items):2d} "
              f"front_page={s['front_page']:.2f} {el:5.1f}s{flag}")
        for it, h in zip(items, s["hits"]):
            print(f"    [{len(h) or ' '}] {it['title'][:88]}")
        rows.append({"ask": ask, "id_prefix": dbg.get("id_prefix"), "n": len(items),
                     "front_page": s["front_page"], "front_page2": s["front_page2"],
                     "secs": round(el,1), "titles": [i["title"] for i in items]})
    if all(r["n"] == 0 for r in rows):
        print("\n!! EVERY ask extracted 0 items. That is an extraction failure, not a")
        print("   verdict about the cards. Fix the parser before recording a baseline.")
        sys.exit(2)
    fp=[r["front_page"] for r in rows]
    print(f"\nMEAN front_page over {len(rows)} general asks: {sum(fp)/len(fp):.2f}")
    json.dump({"controls": cal, "rows": rows}, open(sys.argv[1], "w"), indent=1)
asyncio.run(main())
