"""Enriched YouTube search: scrape → parse signals → language-aware query → score.

This module is intentionally dependency-light (httpx, re, json, stdlib only) so the
benchmark harness under bench/ can import the SAME parser and scorer the live app
uses, without pulling in the FastAPI application. main.py re-exports
`search_youtube_videos` from here.

Why it exists: the old scrape discarded everything except title+channel+id, so no
ranker — heuristic or LLM — could see the signals that actually separate a good hit
from a bad one. YouTube's results page already carries view count, duration, publish
age, a verified-channel badge, and a live marker in each `videoRenderer` block. We
parse all of it, then rank on four axes: intent match, authority, freshness,
watchability.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx

# ── Search-order → YouTube `sp` filter param ────────────────────────────────
# These are YouTube's own URL-encoded search filters. "date" sorts newest-first;
# "live" applies the LIVE filter (the only reliable way to reach an actual stream —
# a plain search for "cnn live news" returns recorded clips).
_SP_BY_ORDER = {
    "date": "CAI%253D",
    "live": "EgJAAQ%253D%253D",
}


def _unescape(s: str) -> str:
    """Undo the \\uXXXX / \\" escaping YouTube uses inside the embedded JSON."""
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


# ── Signal parsers ──────────────────────────────────────────────────────────
def _parse_views(block: str) -> Optional[int]:
    """Absolute view count. Handles "1,234,567 views" and the live "N watching"
    form (returned as concurrent viewers). None when absent (rare)."""
    m = re.search(r'"viewCountText":\{"simpleText":"([\d,]+)\s*views?"', block)
    if m:
        return int(m.group(1).replace(",", ""))
    # Live streams carry viewers as runs: [{"442"},{" watching"}]
    m = re.search(r'"viewCountText":\{"runs":\[\{"text":"([\d,]+)"\},\{"text":"\s*watching', block)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _parse_duration_seconds(block: str) -> Optional[int]:
    """Video length in seconds from "6:51" / "1:02:03". None for live (no length)."""
    # `.*?` (not `[^}]*`) because lengthText nests an accessibility object with
    # its own braces before simpleText: {"accessibility":{...},"simpleText":"6:51"}.
    m = re.search(r'"lengthText":\{.*?"simpleText":"([\d:]+)"', block)
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(":")]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs


# "9 months ago" → approximate days. Coarse on purpose: freshness only needs to
# separate "today" from "last week" from "years old", not exact timestamps.
_AGE_UNIT_DAYS = {
    "second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24,
    "day": 1, "week": 7, "month": 30, "year": 365,
}


def _parse_age_days(block: str) -> Optional[float]:
    m = re.search(r'"publishedTimeText":\{"simpleText":"(\d+)\s+(\w+?)s?\s+ago"', block)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).rstrip("s")
    return n * _AGE_UNIT_DAYS.get(unit, 30)


def _parse_verified(block: str) -> bool:
    """Channel carries YouTube's Verified checkmark (or Artist badge)."""
    return ("BADGE_STYLE_TYPE_VERIFIED" in block
            or "BADGE_STYLE_TYPE_VERIFIED_ARTIST" in block
            or '"label":"Verified"' in block)


def _parse_is_live(block: str) -> bool:
    return "BADGE_STYLE_TYPE_LIVE_NOW" in block


@dataclass
class Video:
    """One enriched search hit. `video_id`/`id` are duplicated because different
    callers/widgets read one or the other."""
    video_id: str
    id: str
    title: str
    channel: Optional[str] = None
    views: Optional[int] = None
    duration_sec: Optional[int] = None
    age_days: Optional[float] = None
    verified: bool = False
    is_live: bool = False
    is_short: bool = False
    rank: int = 0            # 0-based position in YouTube's own ordering
    score: float = 0.0       # filled in by score_videos()
    score_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_search_html(html: str, limit: int = 10) -> list[Video]:
    """Extract enriched Video objects from a YouTube results page. Pure function —
    unit-testable against a saved fixture, no network."""
    out: list[Video] = []
    seen: set[str] = set()
    for i, block in enumerate(html.split('"videoRenderer":')[1:]):
        vid_m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', block)
        title_m = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]', block)
        if not vid_m or not title_m:
            continue
        vid = vid_m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        chan_m = re.search(r'"longBylineText":\{"runs":\[\{"text":"(.*?)"', block)
        dur = _parse_duration_seconds(block)
        is_live = _parse_is_live(block)
        out.append(Video(
            video_id=vid,
            id=vid,
            title=_unescape(title_m.group(1)),
            channel=_unescape(chan_m.group(1)) if chan_m else None,
            views=_parse_views(block),
            duration_sec=dur,
            age_days=_parse_age_days(block),
            verified=_parse_verified(block),
            is_live=is_live,
            # A Short is a <=60s vertical clip; length is the reliable tell here.
            is_short=bool(dur is not None and dur <= 60 and not is_live),
            rank=len(out),
        ))
        if len(out) >= limit:
            break
    return out


# ── Language detection + query construction ─────────────────────────────────
# Script-range detection covers non-Latin languages cheaply and unambiguously.
_SCRIPT_RANGES = [
    ("hi", r'[ऀ-ॿ]'),   # Devanagari (Hindi/Marathi)
    ("bn", r'[ঀ-৿]'),   # Bengali
    ("ta", r'[஀-௿]'),   # Tamil
    ("ar", r'[؀-ۿ]'),   # Arabic
    ("ru", r'[Ѐ-ӿ]'),   # Cyrillic
    ("ko", r'[가-힣]'),   # Hangul
    ("ja", r'[぀-ヿ]'),   # Hiragana/Katakana (kanji alone is ambiguous)
    ("zh", r'[一-鿿]'),   # CJK Han (checked after ja)
    ("el", r'[Ͱ-Ͽ]'),   # Greek
    ("he", r'[֐-׿]'),   # Hebrew
    ("th", r'[฀-๿]'),   # Thai
]

# Latin-script languages need stopword hints — the alphabet alone can't tell them
# apart. Small, high-signal function-word sets; ordering doesn't matter.
_LATIN_HINTS = {
    "es": {"cómo", "como", "hacer", "receta", "de", "para", "el", "la", "vídeo", "video", "en", "español", "mejor"},
    "fr": {"comment", "faire", "recette", "de", "pour", "le", "la", "vidéo", "les", "français", "meilleur"},
    "de": {"wie", "macht", "man", "rezept", "für", "das", "video", "auf", "deutsch", "beste"},
    "pt": {"como", "fazer", "receita", "de", "para", "vídeo", "melhor", "português", "em"},
    "it": {"come", "fare", "ricetta", "di", "per", "il", "video", "italiano", "migliore"},
}

# Explicit override: "... in Hindi", "en español", "auf Deutsch", "in French".
_LANG_NAME_TO_CODE = {
    "hindi": "hi", "spanish": "es", "español": "es", "espanol": "es",
    "french": "fr", "français": "fr", "francais": "fr", "german": "de", "deutsch": "de",
    "portuguese": "pt", "português": "pt", "italian": "it", "italiano": "it",
    "arabic": "ar", "russian": "ru", "korean": "ko", "japanese": "ja",
    "chinese": "zh", "mandarin": "zh", "bengali": "bn", "tamil": "ta",
    "greek": "el", "hebrew": "he", "thai": "th", "english": "en",
}
_OVERRIDE_RE = re.compile(
    r'\b(?:in|en|auf|em|su)\s+(' + "|".join(map(re.escape, _LANG_NAME_TO_CODE)) + r')\b',
    re.IGNORECASE)

# hl (interface) + gl (region) pairs. gl steers which regional catalog YouTube
# ranks from — searching Hindi with gl=IN surfaces the Indian results that a
# US-geolocated request buries.
_HL_GL = {
    "en": ("en", "US"), "hi": ("hi", "IN"), "es": ("es", "ES"), "fr": ("fr", "FR"),
    "de": ("de", "DE"), "pt": ("pt", "BR"), "it": ("it", "IT"), "ar": ("ar", "SA"),
    "ru": ("ru", "RU"), "ko": ("ko", "KR"), "ja": ("ja", "JP"), "zh": ("zh-CN", "CN"),
    "bn": ("bn", "IN"), "ta": ("ta", "IN"), "el": ("el", "GR"), "he": ("iw", "IL"),
    "th": ("th", "TH"),
}


@dataclass
class Intent:
    """What the user wants, distilled from their message. The benchmark annotates
    these by hand; the live app derives them from its existing regexes."""
    query: str                    # the cleaned search string (target language)
    lang: str = "en"              # detected/overridden content language code
    want_fresh: bool = False      # news / "latest" → freshness weighted up
    want_live: bool = False       # live stream
    want_short: bool = False      # explicitly wants a Short / quick clip
    explicit_lang: bool = False   # user named the language ("in Hindi")


def detect_language(text: str) -> tuple[str, bool]:
    """Return (lang_code, explicit). Explicit override ("in Spanish") wins; then
    non-Latin script; then Latin stopword hints; else English."""
    m = _OVERRIDE_RE.search(text or "")
    if m:
        return _LANG_NAME_TO_CODE[m.group(1).lower()], True
    for code, pat in _SCRIPT_RANGES:
        if re.search(pat, text or ""):
            return code, False
    words = set(re.findall(r"[a-zàâäéèêëîïôöùûüçñáíóúü]+", (text or "").lower()))
    best, best_hits = "en", 0
    for code, hints in _LATIN_HINTS.items():
        hits = len(words & hints)
        if hits > best_hits:
            best, best_hits = code, hits
    # Require ≥2 stopword hits before overriding English — one shared word like
    # "de" or "video" shouldn't flip an English query to Spanish.
    return (best, False) if best_hits >= 2 else ("en", False)


# Trigger/filler prefixes and video-type words to strip so the search string is
# the SUBJECT only. Mirrors main.clean_video_query but self-contained here.
_TRIGGER_RE = re.compile(
    r'^(?:add|show|open|play|create|get|find|search|pull\s*up|give\s*me|i\s*want|'
    r'can\s*you|please)\b[\s:,-]*', re.IGNORECASE)
_VIDEO_WORDS_RE = re.compile(
    r'\b(?:a|an|the|some|me|us|of|on|about|for|video|clip|youtube|yt|please|now)\b',
    re.IGNORECASE)


def clean_query(text: str, explicit_lang: bool = False) -> str:
    """Reduce a raw message to the search SUBJECT. When the language was named
    explicitly ("... in Hindi"), drop that phrase too — it's captured in `lang`,
    and leaving it in the query pollutes the search."""
    q = (text or "").strip()
    if explicit_lang:
        q = _OVERRIDE_RE.sub(" ", q)
    prev = None
    while prev != q:                       # peel stacked prefixes: "show me a video of"
        prev = q
        q = _TRIGGER_RE.sub("", q).strip()
    q = _VIDEO_WORDS_RE.sub(" ", q)
    return re.sub(r"\s{2,}", " ", q).strip() or (text or "").strip()


# Benchmark alias — kept distinct so a future live-app cleaner can diverge.
clean_for_bench = clean_query


# Consent-bypass cookies. From a datacenter/NAS IP, youtube.com/results serves a
# consent interstitial (no videoRenderer blocks) unless a "consent given" cookie is
# present — which made the parse return [] and the app fall back to a SINGLE proxy
# video (the "same video for every broad query" bug). SOCS=CAI + a CONSENT token are
# the values scrapers/yt-dlp use to get the real results page. A realistic UA helps too.
_YT_CONSENT_COOKIE = "SOCS=CAI; CONSENT=YES+cb.20210328-17-p0.en+FX+100"
_YT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def build_search_url(query: str, order: str = "relevance", lang: str = "en") -> tuple[str, dict]:
    """(url, headers) for a language-aware search. gl/hl bias the regional catalog;
    Accept-Language reinforces it for the HTML the scraper gets back."""
    hl, gl = _HL_GL.get(lang, ("en", "US"))
    params = {"search_query": query, "hl": hl, "gl": gl}
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode(params)
    sp = _SP_BY_ORDER.get(order)
    if sp:
        url += f"&sp={sp}"
    headers = {
        "Accept-Language": f"{hl},{hl[:2]};q=0.9,en;q=0.5",
        "User-Agent": _YT_UA,
        "Cookie": _YT_CONSENT_COOKIE,
    }
    return url, headers


async def fetch_videos(query: str, limit: int = 10, order: str = "relevance",
                       lang: str = "en") -> list[Video]:
    """Language-aware enriched search. The workhorse the strategies call."""
    url, headers = build_search_url(query, order=order, lang=lang)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            return parse_search_html(resp.text, limit=limit)
    except Exception:
        return []


# ── Heuristic scorer (Strategy A, and the pre-filter for B/C) ────────────────
@dataclass
class Weights:
    """Tunable so the benchmark can sweep them. Defaults reflect the priority the
    user picked: intent match first, then authority, then freshness/watchability."""
    intent: float = 1.0
    authority: float = 0.6
    freshness: float = 0.4
    watchability: float = 0.4


_CLICKBAIT_RE = re.compile(r'[!?]{2,}|[🔥😱‼️❗]{1,}|\bGONE WRONG\b|\bYOU WON\'?T BELIEVE\b', re.IGNORECASE)


def _token_overlap(query: str, title: str) -> float:
    """Fraction of query content-words present in the title. Crude but effective
    for 'does this video match the ask' before any LLM sees it."""
    q = set(re.findall(r"\w+", query.lower()))
    q = {w for w in q if len(w) > 2}
    if not q:
        return 0.5
    t = set(re.findall(r"\w+", title.lower()))
    return len(q & t) / len(q)


def score_video(v: Video, intent: Intent, w: Weights = Weights()) -> Video:
    """Fill v.score and v.score_breakdown on the four axes in [0,1] each."""
    b: dict[str, float] = {}

    # 1. INTENT MATCH — title overlap, plus a small bonus for YouTube's own rank
    #    (position 0 is a strong prior we shouldn't fully discard).
    overlap = _token_overlap(intent.query, v.title or "")
    rank_prior = max(0.0, 1.0 - v.rank * 0.08)
    b["intent"] = 0.7 * overlap + 0.3 * rank_prior

    # 2. AUTHORITY — verified channel + log-scaled views. log10 keeps a 10M-view
    #    video from swamping a perfectly good 200k one.
    import math
    view_score = min(1.0, math.log10(max(10, v.views or 10)) / 7.0)  # 10^7 → 1.0
    b["authority"] = 0.4 * (1.0 if v.verified else 0.0) + 0.6 * view_score

    # 3. FRESHNESS — only heavily rewarded when the ask wants it. Live is always
    #    "now". Otherwise a gentle decay so stale-but-relevant still ranks.
    if v.is_live:
        fresh = 1.0
    elif v.age_days is None:
        fresh = 0.5
    else:
        fresh = max(0.0, 1.0 - v.age_days / 365.0)  # 1yr old → 0
    b["freshness"] = fresh

    # 4. WATCHABILITY — duration fit + anti-clickbait. A Short is wrong for a
    #    depth ask and right for a quick one; a 3h stream is wrong for "quick
    #    recipe". Penalize ALL-CAPS / emoji-spam titles.
    if intent.want_live:
        dur_fit = 1.0 if v.is_live else 0.3
    elif intent.want_short:
        dur_fit = 1.0 if v.is_short else 0.5
    else:
        d = v.duration_sec
        if d is None:
            dur_fit = 0.6
        elif d < 60:
            dur_fit = 0.3   # a Short when they wanted a real video
        elif d <= 1800:
            dur_fit = 1.0   # 1–30 min sweet spot
        elif d <= 5400:
            dur_fit = 0.7
        else:
            dur_fit = 0.4   # >90 min
    title = v.title or ""
    caps_ratio = sum(c.isupper() for c in title) / max(1, sum(c.isalpha() for c in title))
    clickbait_pen = (0.3 if _CLICKBAIT_RE.search(title) else 0.0) + (0.2 if caps_ratio > 0.6 else 0.0)
    b["watchability"] = max(0.0, dur_fit - clickbait_pen)

    v.score_breakdown = {k: round(val, 3) for k, val in b.items()}
    v.score = round(
        w.intent * b["intent"] + w.authority * b["authority"]
        + w.freshness * b["freshness"] + w.watchability * b["watchability"], 4)
    return v


def score_videos(videos: list[Video], intent: Intent, w: Weights = Weights()) -> list[Video]:
    """Score and return sorted best-first."""
    for v in videos:
        score_video(v, intent, w)
    return sorted(videos, key=lambda x: x.score, reverse=True)


# ── Backwards-compatible shim for main.py's existing call sites ──────────────
async def search_youtube_videos(query: str, limit: int = 5, order: str = "relevance",
                                lang: Optional[str] = None) -> list:
    """Drop-in replacement for the old function. Returns plain dicts with the SAME
    keys the old one did (video_id, id, title, channel) PLUS the new signal fields,
    so existing callers keep working and new ones can rank. When `lang` is None the
    language is auto-detected from the query."""
    if lang is None:
        lang, _ = detect_language(query)
    vids = await fetch_videos(query, limit=limit, order=order, lang=lang)
    return [v.to_dict() for v in vids]
