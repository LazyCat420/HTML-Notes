import difflib
import urllib.parse
import xml.etree.ElementTree as ET
import asyncio
import datetime
import httpx
import logging
import re
from typing import Optional, Dict, List, Any

# Inherit from youtube_search
from app.youtube_search import (
    fetch_videos as _yt_fetch_videos,
    score_videos as _yt_score_videos,
    detect_language as _yt_detect_language,
    clean_query as _yt_clean_query,
    Intent as _YtIntent,
    _unescape as _yt_unescape,
    _token_overlap as _yt_token_overlap,
    Freshness,
    parse_freshness,
    filter_by_age,
    parse_video_form,
    filter_by_form,
    NEWEST_PATTERN as _YT_NEWEST_PATTERN,
    RECENCY_PATTERN as _YT_RECENCY_PATTERN,
)

# We will need fast_llm_json, _scrape, and __import__('app.main', fromlist=['SCRAPER_SERVICE_URL']).SCRAPER_SERVICE_URL
# To avoid circular imports, we will import them locally inside the functions
# or at the end of the module.
logger = logging.getLogger(__name__)

def _diversify_by_channel(videos: list, per_channel: int = 2) -> list:
    """Cap how many hits from one channel survive, preserving score order. A broad
    ask ("cooking videos") otherwise returns five clips from the same channel — the
    literal 'same videos over and over' complaint. Keeps the best `per_channel` per
    channel, then appends the rest so the pool never shrinks below what was asked."""
    kept, overflow, counts = [], [], {}
    for v in videos:
        ch = (v.get("channel") or "").lower()
        if ch and counts.get(ch, 0) >= per_channel:
            overflow.append(v)
            continue
        counts[ch] = counts.get(ch, 0) + 1
        kept.append(v)
    return kept + overflow


# Queries where the heuristic scorer structurally misses and a one-shot LLM rerank
# earns its ~1s (per the bench): intent-ambiguous format words + explicit-language
# asks. "nba highlights" heuristically picks a recent awards clip; the LLM picks
# actual game highlights.
_HARD_VIDEO_RE = re.compile(
    r'\b(highlights?|news|best|top\s*\d*|vs\.?|versus|review|tutorial|how\s?to|'
    r'recap|explained|comparison|ranked)\b', re.IGNORECASE)


async def _llm_rerank_videos(query: str, videos: list,
                             freshness: Optional["Freshness"] = None) -> list:
    """One-shot LLM rerank of the top candidates. Returns them reordered
    best-first with clearly-irrelevant candidates dropped; on ANY failure (or a
    drop-list that would empty the set) returns the input unchanged, so the
    heuristic order always stands as a floor."""
    top = videos[:8]
    if len(top) < 2:
        return videos
    lines = []
    for i, v in enumerate(top):
        meta = []
        if v.get("channel"):
            meta.append(v["channel"])
        if v.get("duration_sec"):
            meta.append(f"{v['duration_sec'] // 60}m")
        if v.get("age_days") is not None:
            meta.append(f"{int(v['age_days'])}d old")
        if v.get("is_live"):
            meta.append("LIVE")
        lines.append(f'[{i}] {v.get("title","")} ({", ".join(meta)})')
    today = datetime.date.today()
    fresh_line = ""
    if freshness:
        want = (f"content from the last ~{freshness.window_days:g} days"
                if freshness.window_days else "the most RECENT on-topic upload")
        fresh_line = (f"The request asks for {want} — candidate ages are listed; "
                      "weigh recency accordingly.\n")
        from app.main import fast_llm_json
    data = await fast_llm_json(
        'You pick the best YouTube results for a request. Return ONLY JSON:\n'
        '{"order": [<candidate indices, best first>], '
        '"drop": [<indices clearly NOT answering the request>]}\n'
        f'TODAY: {today.isoformat()} ({today:%A}).\n'
        f'REQUEST: "{query}"\n\nCANDIDATES:\n' + "\n".join(lines) + '\n\n'
        + fresh_line +
        "Judge by how well each title matches the REQUEST's real intent (a "
        '"highlights" ask wants game highlights not an awards show; a language '
        "request wants that language; a review wants a review). Prefer real, "
        "watchable videos over clickbait. List every index once across the two "
        "lists, order best first; drop ONLY candidates that plainly do not "
        "answer the request.",
        max_tokens=250,
    )
    order = (data or {}).get("order")
    if isinstance(order, list):
        idxs = [i for i in order if isinstance(i, int) and 0 <= i < len(top)]
        drops = {i for i in ((data or {}).get("drop") or [])
                 if isinstance(i, int) and 0 <= i < len(top)}
        kept_idxs = [i for i in idxs if i not in drops]
        # Fail-open floor: a drop-list that empties the ranking is ignored.
        idxs = kept_idxs or idxs
        if idxs:
            picked = [top[i] for i in idxs]
            rest = [v for j, v in enumerate(top)
                    if j not in idxs and j not in drops] + videos[8:]
            if drops and kept_idxs:
                logger.info(f"[YOUTUBE] rerank dropped {len(drops)} off-intent "
                            f"candidate(s) for {query!r}")
            return picked + rest
    return videos


async def search_youtube_videos(query: str, limit: int = 5, order: str = "relevance",
                                rerank: bool = False, strict_recency: bool = False,
                                freshness: Optional[Freshness] = None,
                                form: Optional[str] = None) -> list:
    """Enriched, scored YouTube search. Returns dicts with the SAME keys the old
    scraper did (video_id, id, title, channel) PLUS the parsed signals (views,
    duration_sec, age_days, verified, is_live, is_short, score), best-first.

    Over the old title-only scrape it: parses each result's real signals, ranks on
    intent/authority/freshness/watchability (app/youtube_search.py), blends in
    date-sorted results for a recency ask, and caps per-channel so a broad query
    stops returning the same handful of clips. order="date"/"live" pass through.

    `strict_recency=True` returns results ordered purely by publish time (newest
    first), bypassing the relevance/views scorer, the channel-diversity cap and the
    LLM rerank — for "give me THE newest" asks the blended score would otherwise
    surface an older, more-watched video (its freshness axis is year-scaled and
    can't separate 42 minutes from 3 days).

    `rerank=True` adds a one-shot LLM rerank, but ONLY on 'hard' queries (ambiguous
    format words / explicit language) where the bench showed it helps — clear
    queries keep the zero-latency heuristic order.

    `form` is the Short-vs-video axis: "short" returns Shorts only, "any" keeps
    both, and the default (None, or "long") drops Shorts. Parsed from the query
    when the caller doesn't pass one, so legacy call sites stop serving a
    60-second clip to "a video about X".
    """
    q = (query or "").strip()
    if not q:
        return []
    form = form or parse_video_form(q)
    ranked = await _search_youtube_scrape(q, limit, order, rerank, strict_recency,
                                          freshness=freshness, form=form)
    if ranked:
        return ranked
    # The direct httpx scrape came back empty (markup shift, a consent wall on this
    # IP, or a transient block). Tier 2: re-fetch youtube.com/results THROUGH the
    # scraper-service's real-browser 'auto' engine — it gets past the consent
    # interstitial that blocks plain httpx and returns the full results HTML, which
    # parses into a real POOL (so variety/dedup work, not a single repeated video).
    pool = await _youtube_results_via_scraper(q, limit, strict_recency=strict_recency,
                                              freshness=freshness, form=form)
    if pool:
        logger.info(f"[YOUTUBE] direct scrape empty for {q!r}; served scraper-service pool ({len(pool)})")
        return pool
    # Tier 3 (last resort): the music-player proxy — a single best match, enough to
    # keep a video ask from dead-ending on "No videos found".
    fb = await _youtube_proxy_fallback(q)
    if fb:
        logger.info(f"[YOUTUBE] scraper pool empty for {q!r}; served single proxy fallback")
    return fb


async def _youtube_results_via_scraper(query: str, limit: int = 5,
                                       strict_recency: bool = False,
                                       freshness: Optional[Freshness] = None,
                                       form: Optional[str] = None) -> list:
    """Fallback pool: scrape youtube.com/results via scraper-service (real browser,
    gets past the consent wall) and parse it with the same parser as the direct
    path. Returns a scored+diversified list (a POOL, unlike the single-video proxy).
    Freshness/strict fetches under YouTube's date sort (+ upload-date facet for a
    bounded ask) and returns newest-first with NO diversity reshuffle — the
    recency guarantee must survive the fallback tier, or a consent-walled primary
    silently downgrades 'newest' to 'popular'."""
    try:
        from app.youtube_search import build_search_url, parse_search_html
        fresh = freshness or parse_freshness(query)
        strict = bool(strict_recency or fresh)
        window = fresh.window_days if fresh else None
        url, _ = build_search_url((query or "").strip(),
                                  order="date" if strict else "relevance",
                                  lang="en", window_days=window)
        from app.main import _scrape
        html = await _scrape(url, engine="auto", timeout=20.0)
        if not html:
            return []
        vids = parse_search_html(html, limit=max(limit * 3, 15))
        if not vids:
            return []
        seen, out = set(), []
        for v in vids:
            d = v.to_dict()
            if d.get("video_id") and d["video_id"] not in seen:
                seen.add(d["video_id"])
                out.append(d)
        # Format filter must survive the fallback tier too, or a consent-walled
        # primary silently downgrades "newest video" back to "newest Short".
        out = filter_by_form(out, form)
        stale_fallback = False
        if window:
            kept = filter_by_age(out, window)
            if out and not kept:
                stale_fallback = True
            else:
                out = kept
        if strict:
            # Same title-relevance floor as the primary tier — the strict
            # guarantee AND the junk guard must both survive the fallback.
            strong = [h for h in out
                      if _yt_token_overlap(query, h.get("title") or "") >= 0.45]
            weak = [h for h in out
                    if _yt_token_overlap(query, h.get("title") or "") > 0]
            out = strong or weak or out
            out.sort(key=lambda h: h["age_days"] if h.get("age_days") is not None else 1e9)
            if stale_fallback:
                for h in out:
                    h["stale_fallback"] = True
            return out[:max(limit, 1)]
        return _diversify_by_channel(out, per_channel=2)[:max(limit, 1)]
    except Exception as e:
        logger.warning(f"youtube scraper-service fallback failed for {query!r}: {e}")
        return []


async def _youtube_proxy_fallback(query: str) -> list:
    """Single-best-match YouTube result via the music-player proxy (a different
    upstream path than our direct scrape). Normalised to the same keys."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{__import__('app.main', fromlist=['MUSIC_PLAYER_URL']).MUSIC_PLAYER_URL}/api/youtube/search",
                                    params={"query": query})
        if resp.status_code != 200:
            return []
        j = resp.json()
        vid = j.get("id") or ""
        if not vid:
            return []
        thumbs = j.get("thumbnails") or []
        return [{
            "video_id": vid, "id": vid,
            "title": j.get("title") or query,
            "channel": j.get("uploader") or "",
            "thumbnail": (thumbs[0].get("url") if thumbs and isinstance(thumbs[0], dict) else ""),
            "score": 0,
            # The proxy returns no publish date, so a freshness ask can't be
            # verified on this tier — flagged rather than silently trusted.
            "age_unknown": True,
        }]
    except Exception as e:
        logger.warning(f"youtube proxy fallback failed for {query!r}: {e}")
        return []


async def _search_youtube_scrape(query: str, limit: int = 5, order: str = "relevance",
                                 rerank: bool = False, strict_recency: bool = False,
                                 freshness: Optional[Freshness] = None,
                                 form: Optional[str] = None) -> list:
    """Direct youtube.com scrape + scoring (primary path). Returns [] on any
    failure so search_youtube_videos can fall through to the proxy.

    `freshness` carries the user's time constraint (parsed from the ORIGINAL
    message when the caller has it; self-parsed from the query otherwise, so
    legacy callers stay time-aware). Any freshness ⇒ strict newest-first among
    on-topic hits; a bounded one ("this week") additionally applies YouTube's
    upload-date facet at the source plus a hard age post-filter — with a
    newest-available fallback tagged `stale_fallback` so a window that turns up
    nothing degrades visibly instead of silently serving something old."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        lang, explicit = _yt_detect_language(q)
        cleaned = _yt_clean_query(q, explicit_lang=explicit) or q
        fresh = freshness or parse_freshness(q)
        if strict_recency and not fresh:
            fresh = Freshness(matched="strict_recency")
        # A live ask never goes strict: the LIVE filter already means "now", and
        # date-sorting VODs would beat the actual stream ("cnn live news").
        strict = bool(strict_recency or fresh) and order != "live"
        window = fresh.window_days if (fresh and order != "live") else None
        if strict:
            order = "date"       # newest-first straight from YouTube's own sort
        intent = _YtIntent(query=cleaned, lang=lang, want_fresh=bool(fresh),
                           want_live=(order == "live"), explicit_lang=explicit,
                           window_days=window)
        # Fetch a deeper pool than requested so scoring + channel-diversity have
        # room to work. A windowed ask also blends in a relevance-ordered batch
        # under the same upload-date facet — date order alone starves the
        # relevance floor of strong candidates on broad topics.
        pool = await _yt_fetch_videos(cleaned, limit=max(limit * 3, 15),
                                      order=order, lang=lang, window_days=window)
        if window and order == "date":
            pool += await _yt_fetch_videos(cleaned, limit=10, order="relevance",
                                           lang=lang, window_days=window)
        elif strict and not window:
            # An unbounded "new"/"newest" ask still needs a SOURCE-side bound.
            # Date sort alone only reorders whatever the pool contains, and for
            # an evergreen topic that pool is all old: "a new cookie recipe
            # video" returned 270- and 365-day-old uploads because nothing
            # constrained the fetch. Probe the month facet (and the week facet
            # for the best candidates) and prepend — the age sort below then has
            # genuinely fresh material to choose from. Additive: if the topic
            # has no recent uploads, the original pool still stands.
            fresh_pool = []
            for probe in (7.0, 31.0):
                try:
                    fresh_pool += await _yt_fetch_videos(
                        cleaned, limit=10, order="date", lang=lang, window_days=probe)
                except Exception:
                    pass
            if fresh_pool:
                pool = fresh_pool + pool
        seen, deduped = set(), []
        for v in pool:
            if v.video_id and v.video_id not in seen:
                seen.add(v.video_id)
                deduped.append(v)
        # Short vs video, BEFORE the age sort — otherwise a Short posted an hour
        # ago wins "newest X" over the real upload from this morning. Search-side
        # classification is the ≤60s duration tell (the channel-feed path has the
        # exact answer); fail-open, so a topic with nothing but Shorts still
        # returns something.
        pre_form = len(deduped)
        deduped = filter_by_form(deduped, form)
        if len(deduped) < pre_form:
            logger.info(f"[YOUTUBE] form={form or 'long'} filter kept "
                        f"{len(deduped)}/{pre_form} hits for {cleaned!r}")
        stale_fallback = False
        if window:
            # Hard window: the sp facet already bounded the fetch, but fallback
            # paths and facet drift can leak old hits — enforce it ourselves.
            kept = filter_by_age(deduped, window)
            if deduped and not kept:
                stale_fallback = True
                logger.info(f"[YOUTUBE] no uploads within {window:g}d for "
                            f"{cleaned!r}; serving newest available")
            else:
                deduped = kept
        # STRICT recency: sort purely by publish time (newest first) and return —
        # NO relevance re-score, NO channel-diversity reshuffle, NO variety pick.
        # An unknown age sorts last so a hit missing publishedTimeText can't jump
        # ahead of a real fresh upload.
        if strict:
            # Title-relevance floor BEFORE the date sort. Pure date order is
            # blind to subject: one fresh-but-unrelated upload used to win
            # "newest X" outright (live failure: a 40-view off-topic clip beat
            # every real match). Keep hits sharing ~half the query's content
            # words; relax to any-overlap, then to all, so the floor can only
            # steer toward relevance, never empty the pool.
            strong = [v for v in deduped
                      if _yt_token_overlap(cleaned, v.title or "") >= 0.45]
            weak = [v for v in deduped
                    if _yt_token_overlap(cleaned, v.title or "") > 0]
            pool2 = strong or weak or deduped
            if len(pool2) < len(deduped):
                logger.info(f"[YOUTUBE] strict-recency floor kept "
                            f"{len(pool2)}/{len(deduped)} hits for {cleaned!r}")
            ranked = [v.to_dict() for v in pool2]
            # The LLM pass judges intent with ages+today in the prompt and can
            # DROP off-topic hits the token floor can't see through — run it
            # BEFORE the age sort so fresh-but-wrong can't win on date alone.
            if rerank and len(ranked) > 1:
                ranked = await _llm_rerank_videos(q, ranked, freshness=fresh)
            ranked.sort(key=lambda v: v["age_days"] if v.get("age_days") is not None else 1e9)
            if stale_fallback:
                for v in ranked:
                    v["stale_fallback"] = True
            return ranked[:max(limit, 1)]
        scored = _yt_score_videos(deduped, intent)
        # Live asks keep YouTube's own order (the LIVE filter already did the work).
        ranked = [v.to_dict() for v in scored]
        if order != "live":
            ranked = _diversify_by_channel(ranked, per_channel=2)
        # Escalate to the LLM only where it pays off — a hard query with real choice.
        if rerank and order != "live" and (explicit or _HARD_VIDEO_RE.search(q)):
            ranked = await _llm_rerank_videos(q, ranked)
        return ranked[:max(limit, 1)]
    except Exception as e:
        logger.error(f"search_youtube_videos error: {e}")
        return []


# ── Named-channel recency: resolve the channel, read its uploads feed ─────────
# A keyword search for "Paul Barron Network" returns that channel's clips MIXED
# with everyone else's, in popularity order — so "newest <creator> video" can't
# be answered by search alone. YouTube publishes a keyless, strictly
# reverse-chronological uploads RSS per channel; resolve the name to a channel_id
# once, read the feed, and entry[0] IS the latest upload, guaranteed.
_YT_CHANNEL_SP = "EgIQAg%3D%3D"      # results-page filter: channels only
_YT_ATOM_NS = {"a": "http://www.w3.org/2005/Atom",
               "yt": "http://www.youtube.com/xml/schemas/2015",
               "media": "http://search.yahoo.com/mrss/"}


def _yt_name_tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 1}


def _yt_squash(s: str) -> str:
    """Lowercase alphanumerics only: 'The PrimeTime' -> 'theprimetime'. Channel
    names glue and unglue words freely ('ThePrimeagen' vs 'the primeagen'), so
    every identity comparison happens in this space, never on tokens alone."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Words that carry no identity — a channel called "<X> TV" or "The <X> Show" is
# still X. Stripped from BOTH sides before the containment test so "primeagen"
# reaches "ThePrimeagen" and "paul barron" reaches "Paul Barron Network".
_YT_CHANNEL_AFFIXES = ("the", "official", "channel", "tv", "network", "show",
                       "media", "news", "live", "clips", "highlights", "shorts",
                       "vods", "vod", "podcast", "archive", "fan", "daily")


def _yt_core_name(s: str) -> str:
    """Squashed name with leading/trailing affixes peeled: 'ThePrimeagen' ->
    'primeagen', 'Paul Barron Network' -> 'paulbarron'.

    Peels at most TWO affixes. Unlimited peeling turns a fan channel into the
    creator it imitates — 'Paul Barron Fan Clips Daily Show' would reduce all
    the way to 'paulbarron' and bind, which is exactly the impersonation the
    caller must reject. Real channels append one qualifier ('Network',
    'Clips'), not four."""
    core = _yt_squash(s)
    for _ in range(2):
        for aff in _YT_CHANNEL_AFFIXES:
            if len(core) > len(aff) + 2:
                if core.startswith(aff):
                    core = core[len(aff):]
                    break
                if core.endswith(aff):
                    core = core[:-len(aff)]
                    break
        else:
            break
    return core or _yt_squash(s)


def _yt_channel_match_score(subject: str, title: str, handle: str = "") -> float:
    """How strongly a channel IS the creator the user named, in [0, 1].

    Replaces a boolean token-overlap gate that could not see the answer at all:
    'primeagen' vs 'ThePrimeagen' tokenizes to {primeagen} vs {theprimeagen},
    an intersection of ZERO, so the real channel was unreachable at ANY
    threshold while a coincidental two-word title ('prime time') sailed through
    the loose path. Identity is therefore scored on the SQUASHED string, and the
    @handle — which is unique on YouTube, unlike a display name — is the
    strongest evidence available.

    Scores: 1.0 exact handle/name, 0.9 handle core, 0.8 name core, 0.6-0.7
    containment, 0.5 strong token overlap. Callers apply the threshold.
    """
    subj_sq, subj_core = _yt_squash(subject), _yt_core_name(subject)
    if not subj_sq:
        return 0.0
    title_sq, title_core = _yt_squash(title), _yt_core_name(title)
    hand_sq = _yt_squash((handle or "").lstrip("@"))
    hand_core = _yt_core_name(handle.lstrip("@")) if handle else ""

    # 1. The handle is an identity, not a label — exact or core-exact wins.
    #    A TITLE match is always scored below its handle equivalent: anyone can
    #    title a channel 'primeagen' (a squatter handled '@AgenW.' does exactly
    #    that and would otherwise outrank the real creator's sibling channels),
    #    while a handle is globally unique and cannot be duplicated.
    if hand_sq and subj_sq == hand_sq:
        return 1.0
    if hand_core and subj_core == hand_core:
        return 0.9
    if title_sq and subj_sq == title_sq:
        return 0.85 if not hand_sq else 0.8
    if title_core and subj_core == title_core:
        return 0.75

    # 2. Containment on the core name, both directions — gated on how much of
    #    the longer string the match covers. A subject that is merely a prefix
    #    of a longer name is a topic word, not an identity: 'bitcoin' must not
    #    bind to 'Bitcoin Magazine', 'top gun maverick' not to 'Top Gun'.
    #
    #    The @handle gets more latitude (0.65) than the display title (0.8): a
    #    handle is a deliberate, globally-unique identity claim, and creators
    #    build second channels by EXTENDING it — ThePrimeagen's other channel is
    #    titled 'The PrimeTime' (no title match at all) but handles itself
    #    '@ThePrimeTimeagen', which is the only evidence connecting the two.
    #    Titles stay strict because they collide by coincidence far more often.
    if len(subj_core) >= 4:
        for other in (title_core, hand_core):
            if not other:
                continue
            if subj_core in other or other in subj_core:
                ratio = min(len(subj_core), len(other)) / max(len(subj_core), len(other))
                if ratio >= 0.8:
                    return 0.7

    # 3. Handle SIMILARITY — the sibling-channel case. A creator's second
    #    channel often INFIXES words into the handle ('@ThePrimeagen' ->
    #    '@ThePrimeTimeagen'), which no substring test can see. Edit-distance
    #    similarity does, and it stays selective: primeagen/primetimeagen
    #    scores 0.82 while the topic-word traps this must keep rejecting sit far
    #    below (bitcoin/bitcoinmagazine 0.64, linus/linustechtips 0.56). Handle
    #    only — display titles collide by coincidence too often for fuzziness.
    if hand_core and len(subj_core) >= 5:
        sim = difflib.SequenceMatcher(None, subj_core, hand_core).ratio()
        if sim >= 0.78:
            return 0.65

    # 3. Multi-word fallback: the old token gate, kept for names that genuinely
    #    differ in wording ('paul barron' -> 'Paul Barron Network').
    a, b = _yt_name_tokens(subject), _yt_name_tokens(title)
    if a and b and len(a) > 1:
        fwd, bwd = len(a & b) / len(a), len(a & b) / len(b)
        if fwd >= 0.7 and bwd >= 0.5:
            return 0.5
    return 0.0


# Below this, a candidate is NOT the named creator. 0.6 admits containment
# ('primeagen' in 'theprimeagen') and rejects coincidental word sharing.
_YT_CHANNEL_MATCH_FLOOR = 0.6


def _yt_channel_name_match(subject: str, title: str, handle: str = "") -> bool:
    """Boolean form of _yt_channel_match_score, kept for existing call sites
    (notably the channel-verified search filter in _recency_video_pick)."""
    return _yt_channel_match_score(subject, title, handle) >= _YT_CHANNEL_MATCH_FLOOR


async def _yt_fetch_html(url: str, timeout: float = 12.0,
                         scraper_fallback: bool = True) -> str:
    """Plain httpx GET (a realistic UA clears the results/feed pages), falling
    back to the real-browser scraper only if httpx comes back empty.

    `scraper_fallback=False` for feeds whose absence is MEANINGFUL: the Shorts
    playlist 404s for a channel that has never posted one, and paying a 20s
    browser scrape to re-confirm that 404 would put the whole video ask over
    budget."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": __import__('app.main', fromlist=['_YAHOO_UA'])._YAHOO_UA})
            if resp.status_code == 200 and resp.text:
                return resp.text
            if resp.status_code == 404:
                return ""
    except Exception as e:
        logger.warning(f"[YOUTUBE] httpx fetch failed for {url[:80]!r}: {e}")
    if not scraper_fallback:
        return ""
    try:
        from app.main import _scrape
        return await _scrape(url, engine="auto", timeout=20.0) or ""
    except Exception:
        return ""


def _parse_channel_candidates(html: str, limit: int = 8) -> list:
    """[{channel_id, title, handle}] from a channels-filtered results page, in
    YouTube's own order. Splitting on the renderer boundary first keeps each
    block's fields together — a single flat regex across the whole document
    pairs one channel's id with a later channel's title."""
    out, seen = [], set()
    for block in (html or "").split('"channelRenderer":')[1:]:
        cid = re.search(r'"channelId":"([^"]+)"', block)
        title = re.search(r'"title":\{"simpleText":"([^"]+)"', block)
        if not cid or not title or cid.group(1) in seen:
            continue
        seen.add(cid.group(1))
        handle = re.search(r'"canonicalBaseUrl":"/(@[^"]+)"', block)
        out.append({"channel_id": cid.group(1),
                    "title": _yt_unescape(title.group(1)),
                    "handle": handle.group(1) if handle else "",
                    # YouTube's own relevance position — the single best proxy
                    # for "which of these same-named channels is the real one".
                    "rank": len(out),
                    "verified": ("BADGE_STYLE_TYPE_VERIFIED" in block
                                 or '"label":"Verified"' in block)})
        if len(out) >= limit:
            break
    return out


async def _resolve_youtube_channels(name: str, limit: int = 3,
                                    evidence: str = "plain") -> list:
    """Every channel that IS the named creator, best match first.

    Rebuilt after a live failure: "newest primeagen video" served a 5-day-old
    clip from an unrelated channel while ThePrimeagen's own upload sat there
    unseen. Two independent defects, both fixed here:
      1. Matching was token-set overlap, so 'primeagen' vs 'ThePrimeagen' had
         an intersection of ZERO — the correct channel was unreachable at any
         threshold. Identity is now scored on squashed/affix-peeled strings and
         on the @handle, which is unique where a display name is not.
      2. A single-word subject only ever examined the TOP candidate ([:1]), and
         YouTube ranks 'The PrimeTime' above 'ThePrimeagen' for that query — so
         the real channel was never even considered. All candidates are scored
         now and the best wins.
    Returns a LIST because creators run several channels (main / clips / VODs);
    the caller merges their feeds so "newest" means newest across all of them.
    """
    name = (name or "").strip()
    if not _yt_name_tokens(name) and not _yt_squash(name):
        return []
    url = (f"https://www.youtube.com/results?search_query="
           f"{urllib.parse.quote(name)}&sp={_YT_CHANNEL_SP}")
    html = await _yt_fetch_html(url)
    if not html:
        return []
    cands = _parse_channel_candidates(html)
    # A topic-shaped subject ('cookie recipe') needs corroboration before it may
    # hijack the whole ask into one creator's feed: a near-exact name AND the
    # verified badge. A named creator keeps the normal floor.
    floor = 0.85 if evidence == "weak" else _YT_CHANNEL_MATCH_FLOOR
    scored = []
    for c in cands:
        s = _yt_channel_match_score(name, c["title"], c.get("handle", ""))
        if evidence == "weak" and not c.get("verified"):
            continue
        if s >= floor:
            # Name match alone cannot separate a creator from an impostor —
            # '@F1X-MKBHD' matches 'mkbhd' as exactly as '@mkbhd' does. Break
            # those ties on authenticity signals: YouTube's own ranking (its
            # relevance model already knows which channel people mean) and the
            # verified badge. Match score still dominates, so a better-named
            # channel never loses to a worse-named verified one.
            # Verification is weighted heavily: an impersonator can copy a name
            # exactly (a channel titled 'primeagen', handle '@AgenW.', ranks
            # ahead of the creator's own sibling channels on name alone), but
            # cannot copy the badge. Position is a weaker nudge on top.
            authority = (0.25 if c.get("verified") else 0.0) + max(0.0, 0.08 - c["rank"] * 0.02)
            scored.append({**c, "match": s, "rank_score": s + authority})
    if not scored:
        logger.info(f"[YOUTUBE] no channel bound for {name!r} "
                    f"({len(cands)} candidate(s) checked)")
        return []
    scored.sort(key=lambda c: (-c["rank_score"], c["rank"]))
    logger.info(f"[YOUTUBE] channel {name!r} -> " +
                ", ".join(f"{c['title']!r}({c['match']:.2f}"
                          f"{'/verified' if c.get('verified') else ''})"
                          for c in scored[:limit]))
    return scored[:limit]


async def _resolve_youtube_channel(name: str) -> Optional[dict]:
    """Single best channel for a creator name, or None. Thin wrapper over
    _resolve_youtube_channels for callers that want exactly one."""
    chans = await _resolve_youtube_channels(name, limit=1)
    return chans[0] if chans else None


def _parse_uploads_feed(xml: str, source: str = "") -> list:
    """Atom uploads/playlist feed → hits in the same shape search_youtube_videos
    returns, newest first. Pure parse; the caller owns the fetch."""
    if not xml:
        return []
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except Exception as e:
        logger.warning(f"[YOUTUBE] uploads feed parse failed for {source}: {e}")
        return []
    now = datetime.datetime.now(datetime.timezone.utc)
    chan_title = (root.findtext("a:title", default="", namespaces=_YT_ATOM_NS) or "")
    # A playlist feed's <title> is the PLAYLIST name ("Videos", "Shorts"), not
    # the channel — take the author name there so hits stay attributable.
    if chan_title.strip().lower() in ("videos", "shorts", "short videos", "live",
                                      "live streams", "uploads", "popular videos"):
        chan_title = (root.findtext("a:author/a:name", default="",
                                    namespaces=_YT_ATOM_NS) or chan_title)
    out = []
    for e in root.findall("a:entry", _YT_ATOM_NS):
        vid = e.findtext("yt:videoId", default="", namespaces=_YT_ATOM_NS)
        if not vid:
            continue
        title = e.findtext("a:title", default="", namespaces=_YT_ATOM_NS) or ""
        pub = e.findtext("a:published", default="", namespaces=_YT_ATOM_NS) or ""
        age_days = None
        if pub:
            try:
                age_days = (now - datetime.datetime.fromisoformat(pub)).total_seconds() / 86400
            except Exception:
                pass
        thumb = e.find(".//media:thumbnail", _YT_ATOM_NS)
        out.append({
            "video_id": vid, "id": vid, "title": title, "channel": chan_title,
            "thumbnail": (thumb.get("url") if thumb is not None else ""),
            "age_days": age_days,
            "published": pub[:16].replace("T", " ") if len(pub) >= 16 else None,
            "score": 0,
            "is_short": False,
        })
    # The feed is already reverse-chronological; sort defensively anyway.
    out.sort(key=lambda h: h["age_days"] if h["age_days"] is not None else 1e9)
    return out


def _yt_auto_playlist(channel_id: str, kind: str) -> str:
    """YouTube's auto-generated per-channel playlist id. UC<x> → UU<x> (every
    upload), UUSH<x> (Shorts only), UULF<x> (long-form only), UULV<x> (live).
    These have their own RSS feeds, which is the ONLY keyless way to tell a
    Short from a video — the uploads feed carries no duration or type."""
    prefix = {"all": "UU", "short": "UUSH", "long": "UULF", "live": "UULV"}[kind]
    return prefix + (channel_id[2:] if channel_id.startswith("UC") else channel_id)


async def _youtube_channel_uploads(channel_id: str, limit: int = 8,
                                   form: Optional[str] = None) -> list:
    """Latest uploads for a channel_id from its RSS feeds, newest first, each hit
    tagged `is_short`.

    `form`: "short" → Shorts only; "any" → everything, untagged (one fetch);
    None/"long" → the uploads feed with Shorts identified and dropped.

    Why two feeds: the uploads feed interleaves Shorts with real uploads and
    creators post far more Shorts, so "newest <creator> video" was answered by a
    30-second Short nearly every time. Classification comes from the Shorts
    playlist feed (UUSH…) rather than duration — the uploads feed has no
    duration, and a duration guess would also misfile a 45-second real upload.
    Live VODs are deliberately NOT excluded: they are videos (the long-form
    playlist UULF… drops them, which would hide NASA's newest launch stream)."""
    if not channel_id:
        return []
    if form == "short":
        # Shorts-only feed. 404s for a channel that has never posted one, which
        # _yt_fetch_html returns as "" — an empty list, so the caller falls back.
        xml = await _yt_fetch_html(
            "https://www.youtube.com/feeds/videos.xml?playlist_id="
            + _yt_auto_playlist(channel_id, "short"), scraper_fallback=False)
        hits = _parse_uploads_feed(xml, f"{channel_id}/shorts")
        for h in hits:
            h["is_short"] = True
        return hits[:max(limit, 1)]

    uploads_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    if form == "any":
        return _parse_uploads_feed(await _yt_fetch_html(uploads_url),
                                   channel_id)[:max(limit, 1)]

    uploads_xml, shorts_xml = await asyncio.gather(
        _yt_fetch_html(uploads_url),
        _yt_fetch_html("https://www.youtube.com/feeds/videos.xml?playlist_id="
                       + _yt_auto_playlist(channel_id, "short"),
                       scraper_fallback=False),
        return_exceptions=True)
    hits = _parse_uploads_feed(
        uploads_xml if isinstance(uploads_xml, str) else "", channel_id)
    short_ids = {h["video_id"] for h in _parse_uploads_feed(
        shorts_xml if isinstance(shorts_xml, str) else "", f"{channel_id}/shorts")}
    for h in hits:
        h["is_short"] = h["video_id"] in short_ids
    kept = [h for h in hits if not h["is_short"]]
    if len(kept) < len(hits):
        logger.info(f"[YOUTUBE] {channel_id}: dropped {len(hits) - len(kept)} "
                    f"Short(s) from the uploads feed")
    # Fail-open: a channel that posts ONLY Shorts still gets an answer rather
    # than an empty feed that silently demotes the ask to keyword search.
    return (kept or hits)[:max(limit, 1)]


# crawl4ai renders Brave's result page as markdown with the outbound hrefs
# intact, which is the only engine/target pair that survives bot detection:
# DuckDuckGo serves a CAPTCHA to every engine, Bing and Mojeek fail outright,
# and the http engine gets challenged. Brave's own chrome (favicons, thumbnails,
# nav) also appears as links, hence the noise-host filter.
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\((https?://[^\s)]+)\)')
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')

