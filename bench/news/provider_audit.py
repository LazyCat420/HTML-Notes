"""What does each news provider actually serve when asked for "the top stories"?

The evidence behind the 2026-09-05 complaint. `news_search` with an empty topic
takes the FIRST usable provider that has a top-headlines endpoint, so which
newsroom answers depends on which keys are cooling down — and the providers
disagree profoundly about what "top" means. currentsapi's /latest-news is a
RECENCY feed (whatever was published in the last few minutes), gnews's
/top-headlines is an editorial selection, thenewsapi's free tier caps at 3.
Rotation treats them as interchangeable; they are not.

This prints, for one minute, every provider's answer side by side with the
keyless editorial feeds, each scored by `editorial_ref.front_page` — so the
difference is a number rather than an impression.

    .venv/bin/python -m bench.news.provider_audit
    .venv/bin/python -m bench.news.provider_audit --category world
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import xml.etree.ElementTree as ET

import httpx

from bench.news import editorial_ref as E

GATEWAY = "http://10.0.0.16:5591"

GOOGLE_SECTIONS = {
    "": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "world": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "us": "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en",
    "business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "technology": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
}


async def gateway_status() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{GATEWAY}/execute/news_search",
                         json={"topic": "", "limit": 1})
        return (r.json() or {}).get("providers", {})


async def gateway_call(limit: int, provider: str = "", category: str = "") -> tuple:
    """One /execute/news_search call. `_provider` pins a single keyed provider
    (a debug-only argument, absent from the tool schema, so no model can reach
    it); `_source` pins the mechanism."""
    body = {"topic": "", "limit": limit}
    if provider:
        body["_provider"] = provider
        body["_source"] = "keyed"
    if category:
        body["category"] = category
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=45.0) as c:
            r = await c.post(f"{GATEWAY}/execute/news_search", json=body)
            j = r.json()
        return j.get("items", []), int((time.time() - t0) * 1000), j.get("source", "?")
    except Exception as exc:
        return [], int((time.time() - t0) * 1000), f"ERROR {type(exc).__name__}"


async def google_feed(url: str, limit: int) -> tuple:
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": E._UA})
            root = ET.fromstring(r.content)
        items = [{"title": (it.findtext("title") or "").strip()}
                 for it in root.findall(".//item")][:limit]
        return items, int((time.time() - t0) * 1000), "google-rss"
    except Exception as exc:
        return [], int((time.time() - t0) * 1000), f"ERROR {type(exc).__name__}"


def show(label: str, items: list, ms: int, source: str, ref: dict) -> dict:
    s = E.score(items, ref)
    print(f"\n--- {label}  ({len(items)} items, {ms} ms, source={source})")
    print(f"    front_page={s['front_page']:.2f}  front_page2={s['front_page2']:.2f}")
    for it, hits in zip(items, s["hits"]):
        mark = f"[{len(hits)}]" if hits else "[ ]"
        src = it.get("source") or ""
        print(f"    {mark} {(it.get('title') or '')[:84]:86s} {src}")
    titles = [it.get("title") or "" for it in items]
    uniq = len({t.strip().lower() for t in titles if t.strip()})
    if items and uniq < len(items):
        print(f"    !! {len(items) - uniq} DUPLICATE rows — this provider path does not dedupe")
    return {"label": label, "n": len(items), "ms": ms, "source": source,
            "front_page": s["front_page"], "front_page2": s["front_page2"],
            "unique_titles": uniq, "titles": titles}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--category", default="")
    ap.add_argument("--out")
    args = ap.parse_args()

    ref = await E.fetch_reference()
    cal = await E.calibrate(ref)
    print(f"reference newsrooms: {len(ref)}  {sorted(ref)}")
    print(f"controls: positive={cal['positive']} (floor {cal['floor']}) "
          f"negative={cal['negative']}  ok={cal['ok']}")
    if not cal["ok"]:
        print("!! CONTROLS FAILED — the numbers below are about the metric, not the providers")

    status = await gateway_status()
    print(f"\ngateway providers: usable={status.get('usable')} "
          f"cooling={status.get('cooling')} usedToday={status.get('usedToday')}")

    rows = []
    items, ms, src = await gateway_call(args.limit, category=args.category)
    rows.append(show("gateway news_search (as html-notes calls it)", items, ms, src, ref))

    for p in status.get("configured", []):
        items, ms, src = await gateway_call(args.limit, provider=p, category=args.category)
        rows.append(show(f"keyed provider: {p}", items, ms, src, ref))

    url = GOOGLE_SECTIONS.get(args.category or "", GOOGLE_SECTIONS[""])
    items, ms, src = await google_feed(url, args.limit)
    rows.append(show(f"keyless Google News RSS ({args.category or 'top'})", items, ms, src, ref))

    # A pinned provider that returns the SAME titles as the unpinned call did not
    # pin anything. Six identical rows in the first run of this script were not
    # six providers agreeing — `_provider` was not implemented yet and every row
    # was the same default call. A probe whose number cannot move is a statement
    # about the probe, so say so instead of printing a table that looks like data.
    keyed = [r for r in rows if r["label"].startswith("keyed provider:")]
    sigs = {tuple(r["titles"]) for r in keyed}
    if len(keyed) > 1 and len(sigs) == 1:
        print("\n!! EVERY PINNED PROVIDER RETURNED IDENTICAL TITLES.")
        print("   `_provider` is not being honoured — these rows are one call repeated,")
        print("   not a per-provider comparison. Fix the pin before reading them.")

    print(f"\n\n{'source':46s} {'n':>3s} {'uniq':>5s} {'ms':>6s} {'front_page':>11s} {'fp>=2':>7s}")
    print("-" * 86)
    for r in sorted(rows, key=lambda r: -r["front_page"]):
        print(f"{r['label'][:46]:46s} {r['n']:3d} {r.get('unique_titles', 0):5d} {r['ms']:6d} "
              f"{r['front_page']:11.2f} {r['front_page2']:7.2f}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"controls": cal, "providers": status, "rows": rows}, fh, indent=1)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
