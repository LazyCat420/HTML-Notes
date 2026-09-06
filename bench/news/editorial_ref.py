"""Did this story actually make the front pages today? — a metric with no LLM in it.

The blind judge answers "is this article about the question". For a GENERAL ask
("top stories") that question is nearly vacuous: every real news article is
"about" the news. The complaint that started this work was not off-topic
articles, it was *unimportant* ones — a local events listing and a college
football recap served as the top stories of the day. `front_page` is the
number that separates those two failures.

**The reference feeds are deliberately DISJOINT from the feeds the production
source reads.** The editorial source in lazy-agent-service merges Google News
plus NYT / BBC / NPR / ABC. If those same feeds scored the result, an item
lifted from NYT would match NYT and the metric would be a tautology that
returns 1.0 by construction. Everything here is a different newsroom:
CBS, Guardian (US + World), PBS, Politico, The Hill, LA Times, NBC.

Read `front_page` next to `empty`: a strategy that returns nothing scores 0,
not "no data" — same rule the judge uses.
"""
from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET

import httpx

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Independent newsrooms, none of which the production source reads.
REFERENCE_FEEDS = {
    "cbs": "https://www.cbsnews.com/latest/rss/main",
    "guardian_us": "https://www.theguardian.com/us-news/rss",
    "guardian_world": "https://www.theguardian.com/world/rss",
    "pbs": "https://www.pbs.org/newshour/feeds/rss/headlines",
    "politico": "https://rss.politico.com/politics-news.xml",
    "thehill": "https://thehill.com/news/feed/",
    "latimes": "https://www.latimes.com/rss2.0.xml",
    "nbc": "https://feeds.nbcnews.com/nbcnews/public/news",
}

# Words that carry no story identity. Deliberately small: a headline is short,
# and over-stripping makes two unrelated headlines look alike.
_STOP = frozenset("""
the a an of to in on for and or with as at by from is are be been was were
after over into vs new news says say said this that these those it its his her
their our your what how why who when where will would could should may might
can has have had do does did not no more most than then there here about
""".split())


def tokens(title: str) -> set:
    """Content tokens of a headline, publisher suffix removed.

    Google News suffixes every title with " - Publisher"; left in, the publisher
    name is a token two headlines can agree on without being the same story.
    """
    t = re.sub(r"\s+[-|]\s+[^-|]{2,40}$", "", (title or "").strip())
    words = re.findall(r"[a-z0-9]+", t.lower())
    return {w for w in words if w not in _STOP and (len(w) > 2 or w.isdigit())}


# Calibrated 2026-09-05 by sweeping both controls (see `calibrate`), NOT chosen
# by eye. min_shared=2 / ratio=0.30 was the setting with the highest positive
# rate at ZERO false positives; ratio=0.25 scored 0.17 on the negative fixture
# and every setting above 0.30 only lost true matches.
MIN_SHARED = 2
RATIO = 0.30

# What the positive control actually scores. The ceiling is NOT 1.0 and must not
# be read as one: a genuine front-page feed carries exclusives ("Scoop:",
# "EXCLUSIVE:") that by definition no other newsroom has, so ~0.5-0.6 is what a
# good source looks like. Judge a change by the DIFFERENCE from this control
# measured in the same run, never against 1.0.
POSITIVE_CONTROL_FLOOR = 0.40


def same_story(a: set, b: set, *, min_shared: int = MIN_SHARED, ratio: float = RATIO) -> bool:
    """Do two headlines from two newsrooms describe the same story?

    Two independent desks write the same event with different words, so exact
    or near-exact matching finds nothing. What survives across outlets is the
    NAMED THINGS: `Witkoff`, `Putin`, `Iranian`, `tankers`. Hence an overlap
    ratio against the SHORTER headline rather than a Jaccard (which punishes a
    long headline for being long), plus an absolute floor of two shared tokens
    so a single common word cannot carry a match on its own.

    Thresholds are calibrated by `calibrate()`, not chosen by eye.
    """
    if not a or not b:
        return False
    shared = a & b
    if len(shared) < min_shared:
        return False
    return len(shared) / min(len(a), len(b)) >= ratio


async def _fetch_feed(client: httpx.AsyncClient, name: str, url: str) -> list:
    try:
        r = await client.get(url, headers={"User-Agent": _UA})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        if title:
            out.append(title)
    return out


async def fetch_reference(feeds: dict = None) -> dict:
    """{feed_name: [title, ...]} fetched concurrently, best-effort.

    A feed that fails contributes nothing rather than failing the run — but the
    caller must check `len(ref)`: scoring against one surviving feed is a
    different measurement from scoring against eight, and a metric that quietly
    loses its references drifts toward 0 and looks like a regression in the
    thing being measured.
    """
    feeds = feeds or REFERENCE_FEEDS
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_fetch_feed(client, n, u) for n, u in feeds.items()),
            return_exceptions=True)
    return {n: (r if isinstance(r, list) else [])
            for n, r in zip(feeds.keys(), results) if isinstance(r, list) and r}


def story_hits(title: str, ref: dict) -> list:
    """Which reference newsrooms are carrying this story."""
    t = tokens(title)
    return [name for name, titles in ref.items()
            if any(same_story(t, tokens(x)) for x in titles)]


def score(items: list, ref: dict) -> dict:
    """front_page  = share of items carried by >=1 independent newsroom
       front_page2 = share carried by >=2 (a story two desks both led with)

    An empty item list scores 0.0 on both, exactly as the judge scores it.
    """
    if not items:
        return {"front_page": 0.0, "front_page2": 0.0, "n": 0, "hits": []}
    hits = [story_hits(it.get("title") or "", ref) for it in items]
    return {
        "front_page": sum(1 for h in hits if len(h) >= 1) / len(items),
        "front_page2": sum(1 for h in hits if len(h) >= 2) / len(items),
        "n": len(items),
        "hits": hits,
    }


async def calibrate(ref: dict = None) -> dict:
    """Run both controls and return their scores. Print these with every result.

    A metric with no controls drifts silently: a reference feed changes its
    title format, or a threshold is nudged, and every strategy's number moves
    together in a way that reads as a real effect. The positive control is
    Google's own top feed (independent of the reference set, and known to carry
    the day's major stories); the negative is a fixture of real-but-unimportant
    headlines. If positive drops below POSITIVE_CONTROL_FLOOR or negative rises
    above 0, the run measured the METRIC, not the strategies.
    """
    import json
    import pathlib
    import xml.etree.ElementTree as _ET

    ref = ref if ref is not None else await fetch_reference()
    positive = []
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
                                 headers={"User-Agent": _UA})
            root = _ET.fromstring(r.content)
        positive = [(it.findtext("title") or "").strip()
                    for it in root.findall(".//item")][:12]
    except Exception:
        pass

    fx = pathlib.Path(__file__).parent / "fixtures" / "not_front_page.json"
    negative = json.loads(fx.read_text())["titles"]

    pos = score([{"title": t} for t in positive], ref)["front_page"] if positive else None
    neg = score([{"title": t} for t in negative], ref)["front_page"]
    ok = (pos is not None and pos >= POSITIVE_CONTROL_FLOOR and neg == 0.0)
    return {"positive": pos, "negative": neg, "feeds": len(ref), "ok": ok,
            "floor": POSITIVE_CONTROL_FLOOR}
