import base64
from dataclasses import dataclass
import difflib
import html as html_lib
import httpx
import logging
import random
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import asynccontextmanager
from html import unescape as _html_unescape

# Enriched YouTube layer (views/duration/age/verified/live signals + language +
# 4-axis heuristic scorer). Shared with the bench harness; the live scraper below
# is a thin wrapper that fetches, scores, and diversifies on top of it.
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

# Crypto/on-chain layer: CoinGecko (price/chart/identity), Ethplorer (ETH holders
# + transfer edges) and Solana RPC (SPL top holders). Self-contained; the config
# builders below orchestrate it into card / report / holder-graph widgets.
from app import crypto as cryptolib

# Needed up here, not in the import block further down: the helper functions
# below are defined before it, and their annotations are evaluated at def time.
from typing import Any, Dict, List, Optional

from app.youtube_service import *

_SEARCH_NOISE_HOSTS = ()

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_SEARCH_ENGINES = (
    # Free, keyless engines: direct DuckDuckGo Lite first, scraper-service collector
    # (with headless Playwright anti-bot fallback) second.
    ("ddg-lite", lambda q, n: _search_duckduckgo(q, n)),
    ("ddg-collector", lambda q, n: _search_scraper_ddg(q, n)),
)






# ─────────────────────────── NEWS ───────────────────────────
# Real, current headlines with real photos. A bare "news" ask used to run
# web_search("top stories news"), and DuckDuckGo answers that with news-org
# HOMEPAGES — nbcnews.com / nytimes.com / news.google.com landing pages whose
# "snippet" describes the organization, never today's stories. That is exactly
# the static-looking card the user complained about.
#
# GDELT's keyless Doc API is built for this: it returns each story's REAL
# article URL, its social image (the article's og:image), the publisher domain,
# and a timestamp — so a card can show a photo and link to the actual story.
# Google News RSS is the fallback (real dated headlines, but its links are
# redirects that never resolve to the article, so no article photo is reachable
# — the publisher favicon stands in). The old web_search is the last resort.

_GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\']'
    r'[^>]+content=["\']([^"\']+)', re.I)








_GENERIC_NEWS_THUMB_HOSTS = ("lh3.googleusercontent.com", "lh4.googleusercontent.com",
                             "lh5.googleusercontent.com", "lh6.googleusercontent.com")










STOCK_RANGES = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "5y", "10y", "max")

# Yahoo picks the candle size; too fine over a long window returns tens of
# thousands of points and a chart nobody can read.
_STOCK_INTERVALS = {
    "1d": "5m", "5d": "30m", "1mo": "1d", "3mo": "1d", "6mo": "1d",
    "1y": "1d", "5y": "1wk", "10y": "1wk", "max": "1mo",
}

_YAHOO_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_yahoo_crumb: dict = {"crumb": None, "cookies": None}
















# ── Themes ───────────────────────────────────────────────────────────────────
# The palettes live in hud-theme.css as :root[data-theme="…"] blocks. This
# catalog is the SERVER's view: a name, a human label, the swatch colours the
# settings widget renders, and the keywords the matcher scores a request
# against. Keep the names in sync with the CSS. The settings widget applies the
# theme client-side by name; the server only ever RESOLVES a fuzzy request
# ("forest vibe", "dark mode") to the closest catalog name.
THEME_CATALOG = [
    {"name": "hud", "label": "HUD Cyan", "swatch": ["#050a12", "#0a1626", "#55d6ff"],
     "dark": True, "keywords": ["hud", "cyan", "default", "blue", "tech", "sci-fi",
                                 "scifi", "cyberpunk", "cockpit", "teal", "aqua"]},
    {"name": "midnight", "label": "Midnight", "swatch": ["#070b16", "#141a2e", "#818cf8"],
     "dark": True, "keywords": ["dark", "midnight", "night", "black", "indigo",
                                 "navy", "deep", "space", "moody"]},
    {"name": "forest", "label": "Forest", "swatch": ["#06120c", "#0e2218", "#4ade80"],
     "dark": True, "keywords": ["forest", "green", "nature", "woods", "emerald",
                                 "jungle", "moss", "pine", "earth", "outdoors"]},
    {"name": "ember", "label": "Ember", "swatch": ["#130b06", "#281a10", "#fb923c"],
     "dark": True, "keywords": ["ember", "sunset", "orange", "warm", "fire",
                                 "amber", "autumn", "rust", "copper", "flame", "lava"]},
    {"name": "grape", "label": "Grape", "swatch": ["#0f0616", "#221230", "#e879f9"],
     "dark": True, "keywords": ["grape", "purple", "magenta", "synthwave", "vaporwave",
                                 "violet", "neon", "plum", "berry", "retro", "pink"]},
    {"name": "mono", "label": "Slate", "swatch": ["#0e1116", "#1e242c", "#94a3b8"],
     "dark": True, "keywords": ["mono", "monochrome", "slate", "gray", "grey",
                                 "minimal", "neutral", "steel", "clean", "muted"]},
    # Pastel before egg: on a bare "light mode" both tie, and pastel is the
    # cleaner/cooler default most people mean by it; egg still wins on its own
    # warm words (cream, beige, latte) and its name.
    {"name": "pastel", "label": "Pastel", "swatch": ["#eef0f9", "#fbfaff", "#a78bfa"],
     "dark": False, "keywords": ["pastel", "soft", "light", "lavender", "mint",
                                 "gentle", "calm", "airy", "white", "bright",
                                 "day", "light mode"]},
    {"name": "egg", "label": "Eggshell", "swatch": ["#f1e8d5", "#fbf6ec", "#b5701f"],
     "dark": False, "keywords": ["egg", "eggshell", "cream", "beige", "tan",
                                 "sand", "latte", "warm", "paper", "vanilla",
                                 "coffee", "wheat"]},
]
_THEME_NAMES = {t["name"] for t in THEME_CATALOG}
_THEME_STOP = {"theme", "themed", "mode", "color", "colour", "colors", "colours",
               "palette", "make", "change", "switch", "set", "want", "the", "it",
               "to", "a", "please", "give", "me", "my", "look", "style", "into",
               "everything", "canvas", "use", "turn", "go"}




# ── Multi-ticker comparison ──────────────────────────────────────────────────
# "NVDA vs SPY vs TSM" used to fan out into one stock_card PER ticker (the
# router classified each as its own stock spec, and nothing merged them). A
# comparison is ONE question, so it renders as ONE chart: every series
# normalized to % change from the range start — the only scale on which a
# $900 stock and a $60 ETF are comparable.

_COMPARE_MAX_TICKERS = 8   # practical cap; the ask says "unlimited", Chart.js
                           # and the legend say otherwise past this
_COMPARE_COLORS = ["#4fc3f7", "#f472b6", "#a3e635", "#fbbf24",
                   "#c084fc", "#fb923c", "#34d399", "#f87171"]

_COMPARE_SPLIT_RE = re.compile(r"\bvs\.?\b|\bversus\b|\bagainst\b|\band\b|,|/", re.I)
# Uppercase words that LOOK like tickers but never are, in compare phrasing.
_TICKER_STOP = {"VS", "AND", "THE", "ETF", "YTD", "SHOW", "PLOT", "CHART",
                "STOCK", "PRICE", "GRAPH", "INDEX", "COMPARE", "OVER", "TO",
                "OF", "ME", "ON", "IN", "FOR", "WITH", "A", "I", "VERSUS"}






# ── Trending / gainers discovery ─────────────────────────────────────────────
# "compare the top trending stocks", "biggest gainers today" is a DISCOVERY
# ask: there is no ticker in the text to resolve, so the old path
# (_resolve_ticker on the whole phrase) got nothing back from Yahoo's symbol
# search, every build came up empty, and the turn degraded to a sourceless
# answer card. The candidate list has to come from a real discovery feed —
# Yahoo's keyless trending/screener endpoints — never from an LLM's memory of
# famous tickers (the agent, probed on this exact ask, ranked ten mega-caps it
# already knew and answered META/AAPL/MSFT while the actual trending list was
# AMC/IREN/ACHR).

# A superlative/discovery word within reach of a stock noun. "gainers"/"losers"
# also stand alone ("show me today's gainers") — they are finance-only words.
# "movers" deliberately is NOT standalone ("find me movers in Seattle").
TRENDING_STOCK_RE = re.compile(
    r'\b(trending|hottest|most (?:active|traded|popular)|top|best[\s-]?performing|'
    r'biggest|largest|worst[\s-]?performing)\b'
    r'[^.?!]{0,40}\b(stocks?|tickers?|equities|shares|movers?|gainers?|losers?)\b'
    r'|\b(gainers?|losers?)\b', re.I)

_TREND_KINDS = (
    (re.compile(r'\b(losers?|worst)\b', re.I), "day_losers"),
    (re.compile(r'\b(most (?:active|traded)|volume)\b', re.I), "most_actives"),
    (re.compile(r'\b(gainers?|best[\s-]?perform\w*|top[\s-]?perform\w*)\b', re.I),
     "day_gainers"),
)

# Ordered longest-window-first so "3 months" never half-matches "month".
_RANGE_WORDS = (
    (re.compile(r'\btoday\b|\b(?:1|one) ?day\b|\b24 ?h', re.I), "1d"),
    (re.compile(r'\bweek\b', re.I), "5d"),
    (re.compile(r'\bquarter\b|\b(?:3|three) ?months?\b', re.I), "3mo"),
    (re.compile(r'\b(?:6|six) ?months?\b|\bhalf a year\b', re.I), "6mo"),
    (re.compile(r'\byear\b|\bytd\b|\b12 ?months?\b', re.I), "1y"),
    (re.compile(r'\bmonth\b', re.I), "1mo"),
)








# ── Index universe (accuracy filter) ─────────────────────────────────────────
# "top 5 stocks in the s&p" used to ignore "s&p" entirely: the candidate list
# came from Yahoo's site-wide trending/US feed, which is most-VIEWED + momentum
# micro-caps (CPHI, VIVK, NBIS…), almost none of them S&P members. Naming an
# index has to actually SCOPE the pull — otherwise the answer is confidently
# wrong and a follow-up ("why are these trending?") finds no news because the
# tickers were never newsworthy, just volatile.
_INDEX_SOURCES = {
    # Keyless, community-maintained constituent lists (CSV, Symbol in col 1).
    "sp500": "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
             "main/data/constituents.csv",
}
# name -> (fetched_at, frozenset[symbols]); one day is well within membership churn.
_index_cache: Dict[str, tuple] = {}
_INDEX_TTL = 86400.0

# "s&p", "s and p", "s&p 500", "sp500", "spx" — the qualifiers that mean the
# S&P 500 universe. Kept deliberately tight so "top stocks" (no index) stays
# unscoped and behaves as before.
_UNIVERSE_RE = (
    (re.compile(r'\bs\s*(?:&|and)\s*p\s*(?:500)?\b|\bsp\s?500\b|\bspx\b', re.I),
     "sp500"),
)
_UNIVERSE_LABEL = {"sp500": "S&P 500"}














# ESPN's scoreboard API is keyless and covers every league we care about. Friendly
# names → ESPN paths, longest key first so "champions league" beats "league" and
# "college football" beats "football".
SPORTS_LEAGUES = {
    "fifa": "soccer/fifa.world", "world cup": "soccer/fifa.world",
    "premier league": "soccer/eng.1", "epl": "soccer/eng.1",
    "champions league": "soccer/uefa.champions", "ucl": "soccer/uefa.champions",
    "la liga": "soccer/esp.1", "serie a": "soccer/ita.1", "bundesliga": "soccer/ger.1",
    "mls": "soccer/usa.1", "soccer": "soccer/fifa.world",
    "ufc": "mma/ufc", "mma": "mma/ufc",
    "nba": "basketball/nba", "basketball": "basketball/nba",
    "wnba": "basketball/wnba",
    "college football": "football/college-football",
    "college basketball": "basketball/mens-college-basketball",
    "nfl": "football/nfl", "football": "football/nfl",
    "mlb": "baseball/mlb", "baseball": "baseball/mlb",
    "nhl": "hockey/nhl", "hockey": "hockey/nhl",
}
# Longest first so multi-word leagues win over the single word inside them.
_SPORTS_KEYS = sorted(SPORTS_LEAGUES, key=len, reverse=True)








# Results of the data tools, so a widget can reference data the model just
# fetched instead of re-typing it.
#
# stock_card's config IS the whole snapshot — labels, values, volumes, technicals,
# fundamentals — and the model was hand-typing all ~2000 tokens of it back into
# canvas_add_widget's arguments. That single re-emission was most of the turn: the
# data tool answered at ~10s and the widget call didn't land until ~65s. Now the
# model passes {"symbol": "AMZN"} and the server rehydrates the rest.
#
# Keyed by content (not session) so it stays correct with concurrent turns.
_TOOL_RESULT_TTL = 300.0
_tool_results: Dict[str, tuple] = {}






# Research tools whose finished result can be shown IMMEDIATELY as a
# provisional widget, before the agent composes its final answer. The value is
# (args -> tool-cache key, widget_type). Only tools whose cached result is
# already a renderable config belong here — html_notes_news caches a full
# data_card config; raw web-search hits do NOT qualify (wall-of-links).
_PROVISIONAL_TOOLS = {
    "mcp__lazy-tool-service__html_notes_news": (
        lambda a: f"news:{str(a.get('topic') or a.get('query') or '').strip()}",
        "data_card",
    ),
}






# Keys that carry only an unresolved QUERY/topic — not real content. A data-ish
# widget holding only these never rehydrated from its data source, so rendering it
# produces the raw key/value dump the user sees as a broken empty card
# ("NEWS_TOPIC | stock market").
_QUERY_ONLY_KEYS = {"news_topic", "topic", "search_query", "map_query", "query",
                    "symbol", "ticker", "location", "url",
                    "profile_query", "timeline_query"}
# Keys that DO carry real, renderable content.
_CONTENT_KEYS = ("items", "sources", "answer", "content", "values", "markers",
                 "events", "articles", "results", "price", "technicals", "image",
                 "rows", "series", "metrics", "stats", "entities", "facts",
                 "sections", "apps", "pending_id")
























# Near-miss type names the model plausibly emits → the real renderer key. The
# SYSTEM_PROMPT's routing line used to say "music, embedded app → … with that
# widget_type", and an unknown type silently degrades to an inert data_card
# (generate_widget_html's fallback) stamped with the bogus type — which then
# dodges the media-singleton swap and id reuse. Resolve the obvious aliases
# instead of letting them fall through.
_WIDGET_TYPE_ALIASES = {
    "music": "mini_music_player", "music_player": "mini_music_player",
    "radio": "mini_music_player",
    "embedded_app": "iframe_app", "embed": "iframe_app", "app": "iframe_app",
    "iframe": "iframe_app", "website": "iframe_app",
    "video": "youtube_player", "youtube": "youtube_player",
    "todo": "checklist", "todo_list": "checklist", "list": "checklist",
    "kpi": "kpi_row", "metrics": "kpi_row", "stat_row": "kpi_row",
    "versus": "versus_card", "comparison": "versus_card", "compare_card": "versus_card",
    "profile": "profile_card", "infobox": "profile_card",
    "data_table": "table",
    # Crypto / on-chain
    "crypto": "crypto_card", "token": "crypto_card", "coin": "crypto_card",
    "token_card": "crypto_card",
    "holder_graph": "wallet_graph", "wallet_network": "wallet_graph",
    "holder_network": "wallet_graph", "whale_graph": "wallet_graph",
    # App Hub launcher
    "app_hub": "app_grid", "launcher": "app_grid", "apps": "app_grid",
    "services": "app_grid", "service_grid": "app_grid",
}




# Below this, a "reply" is not an answer — it's the model trailing off ("...",
# "Sure!", "Let me look"). Rendering that as an answer card is worse than saying
# nothing went wrong, because it looks like a real result.
_MIN_ANSWER_CHARS = 40

# Runaway-tool thresholds. A healthy research turn measures 3 tool calls with 1
# repeat (5/5 runs, 2026-07-20), so these sit far above normal and should only
# ever fire when something is genuinely broken. Do NOT tune them down toward
# normal — a research turn legitimately repeats a search once.
_MAX_IDENTICAL_TOOL_CALLS = 4
_MAX_RESEARCH_CALLS = 12




# The in-flight progress vocabulary the browser renders while a turn runs. The
# client keys off `phase`, never off the prose in `message` — so a status line
# can be reworded without silently changing what the canvas shows, and a status
# we forget to tag simply leaves the phase where it was rather than misreporting.
_PHASE_ROUTING     = "routing"
_PHASE_RESEARCHING = "researching"
_PHASE_READING     = "reading"
_PHASE_COMPOSING   = "composing"

# Which phase a tool call puts the turn in, keyed on the BARE tool name (the
# mcp__lazy-tool-service__ prefix is stripped first). Unlisted means research:
# the only tools that aren't research are the canvas/notes mutators, and every
# one of those is named here, so the default is safe rather than merely likely.
_TOOL_PHASES = {
    "html_notes_read_page":  _PHASE_READING,
    "canvas_read_dom":       _PHASE_READING,
    "html_notes_list_services": _PHASE_READING,
    "html_notes_open_app":      _PHASE_COMPOSING,
    "html_notes_curate_app":    _PHASE_COMPOSING,
    "html_notes_list_actions":  _PHASE_READING,
    "html_notes_app_action":    _PHASE_COMPOSING,
    "canvas_add_widget":     _PHASE_COMPOSING,
    "canvas_modify_dom":     _PHASE_COMPOSING,
    "create_widget":         _PHASE_COMPOSING,
    "update_widget":         _PHASE_COMPOSING,
    "validate_widget_html":  _PHASE_COMPOSING,
    "plan_widget":           _PHASE_COMPOSING,
    "list_widget_types":     _PHASE_COMPOSING,
}

# The argument values that say what a call is DOING. Everything else in an args
# dict is plumbing (widget ids, html bodies, limits) and would only bloat a
# frame that now rides on EVERY tool call — a canvas_add_widget `config` alone
# runs to several KB, and this travels the same SSE stream as the canvas HTML.
_TOOL_DETAIL_KEYS = ("query", "q", "url", "topic", "ticker", "symbol",
                     "location", "league", "title")
_MAX_DETAIL_CHARS = 80






# The model narrating its plan instead of answering. Observed verbatim on a live
# research turn: it ran 18 research calls, then said "Now I have comprehensive
# data from three major review sources. Let me build a data_card with the best
# waterproof hiking sandals" — and ended the turn WITHOUT calling
# canvas_add_widget. Rendering that sentence as the answer produces a card whose
# body is a promise to make a card.
# Matched per SENTENCE, not against the whole reply: narration usually arrives as
# its own sentence appended to (or instead of) the answer, so an anchored
# whole-string pattern misses "…sources. Let me build a data_card." entirely.
_NARRATION_SENTENCE_RE = re.compile(
    r"^\s*(?:"
    r"(?:now|next|first|then|so|okay|ok|alright|great|perfect)[,!.]?\s+)?"
    r"(?:i\s*(?:'ll|'ve)?\s*(?:have|will|am|can|need|should|shall|"
    r"going\s+to|now\s+have)?|let\s+me|let's|i'll|i've)\b",
    re.I,
)
# Tool/mechanism talk: the model describing the machinery instead of the answer.
_TOOL_TALK_RE = re.compile(
    r"\b(?:data_card|canvas_add_widget|canvas_modify_dom|widget_type|"
    r"stock_card|scoreboard|a\s+widget|the\s+canvas)\b", re.I)






# Media widgets (video, audio) are players, not data: the canvas can only ever
# play one thing at a time, so a new one replaces whatever's already playing.
# Data widgets (stock_card, scoreboard, notes, ...) coexist — a new one adds
# alongside the rest unless it shares a widget_id with an existing one.
_MEDIA_WIDGET_MARKERS = {
    "youtube_player": "youtubePlayerWidget",
    "mini_music_player": "musicPlayerWidget",
}




# Junk a scraper returns with success=True but zero real article text: stealth-
# browser error interstitials and consent/bot walls. crawl4ai in particular hands
# back "Oops, something went wrong" nav-chrome for Yahoo Finance's /m/ syndication
# pages (4-5KB of skip-links, NOT the article) — non-empty, so the old crawl4ai→
# playwright fallback never escalated and fed that garbage straight to the editor.
_SCRAPE_JUNK_SIGNATURES = (
    "oops, something went wrong",
    "please enable javascript",
    "enable cookies and javascript",
    "verify you are human",
    "pardon our interruption",
    "access to this page has been denied",
)








# WMO weather-interpretation codes → (label, material-symbols icon, emoji).
# Open-Meteo returns this integer as `weather_code`. https://open-meteo.com/en/docs
_WMO_CODES = {
    0: ("Clear", "clear_day", "☀️"),
    1: ("Mainly clear", "partly_cloudy_day", "🌤️"),
    2: ("Partly cloudy", "partly_cloudy_day", "⛅"),
    3: ("Overcast", "cloud", "☁️"),
    45: ("Fog", "foggy", "🌫️"), 48: ("Rime fog", "foggy", "🌫️"),
    51: ("Light drizzle", "rainy", "🌦️"), 53: ("Drizzle", "rainy", "🌦️"),
    55: ("Heavy drizzle", "rainy", "🌦️"),
    56: ("Freezing drizzle", "rainy", "🌧️"), 57: ("Freezing drizzle", "rainy", "🌧️"),
    61: ("Light rain", "rainy", "🌧️"), 63: ("Rain", "rainy", "🌧️"),
    65: ("Heavy rain", "rainy", "🌧️"),
    66: ("Freezing rain", "rainy", "🌧️"), 67: ("Freezing rain", "rainy", "🌧️"),
    71: ("Light snow", "weather_snowy", "🌨️"), 73: ("Snow", "weather_snowy", "❄️"),
    75: ("Heavy snow", "weather_snowy", "❄️"), 77: ("Snow grains", "weather_snowy", "🌨️"),
    80: ("Light showers", "rainy", "🌦️"), 81: ("Showers", "rainy", "🌧️"),
    82: ("Violent showers", "thunderstorm", "⛈️"),
    85: ("Snow showers", "weather_snowy", "🌨️"), 86: ("Snow showers", "weather_snowy", "❄️"),
    95: ("Thunderstorm", "thunderstorm", "⛈️"),
    96: ("Thunderstorm, hail", "thunderstorm", "⛈️"), 99: ("Thunderstorm, hail", "thunderstorm", "⛈️"),
}











from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app import database
from app.config import PORT, PRISM_URL, LAZY_AGENT_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL, TTS_SERVICE_URL, MUSIC_PLAYER_URL, SCRAPER_SERVICE_URL, VAULT_SERVICE_URL, VAULT_SERVICE_TOKEN, OBSIDIAN_VAULT_DIR, PORTAL_SERVICE_URL
import asyncio
import contextvars
import datetime
import hashlib
import itertools
import json
import os

# Scope every agent turn is attributed to on prism. Sent BOTH as x-project /
# x-username HEADERS (what prism actually reads) and in the payload body. The
# username must be `admin` so html-notes turns show up under admin in
# prism-client — with no headers prism recorded them as "anonymous", which is
# why they looked like they were never arriving.
AGENT_PROJECT = os.getenv("AGENT_PROJECT", "html-notes-client")
AGENT_USERNAME = os.getenv("AGENT_USERNAME", "admin")

# Persona id per gateway. The :5591 fork ships HtmlNotesPersona as a built-in
# ("HTML_NOTES"); canonical prism has no such built-in, so the equivalent is
# registered there as a CUSTOM agent via POST /custom-agents — prism derives the
# id from the name, hence CUSTOM_HTML_NOTES_CANVAS. Without a persona the run is
# UNSCOPED: prism hands the model ~79 tools (execute_python, create_skill, ...)
# and ~25k tokens of tool schemas, so it wanders off mid-turn — a follow-up was
# observed calling execute_python + read_page for 219s and never touching the
# canvas. The persona scopes the run to the widget tool set.
PRISM_AGENT_ID = os.getenv("PRISM_AGENT_ID", "CUSTOM_HTML_NOTES_CANVAS")
# The MCP server name lazy-tool-service registers itself under in Prism. The
# tool prefix (mcp__lazy-tool-service__*) derives from it, so it is load-bearing
# — see lazy-tool-service/src/services/PrismRegistrationService.ts.
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "lazy-tool-service")

# Names that identify OUR MCP server in a gateway's registry, for health lookups
# ONLY. The registration was renamed to `lazy-agent-service` (measured on prism
# 2026-09-05: that row is connected with 91 tools, and no `lazy-tool-service` row
# exists), so a lookup on the single name reported
#     agent.ok=false "lazy-tool-service is not registered for this scope"
# on a stack whose agent path demonstrably works — a health endpoint saying the
# thing is broken while it serves tools is worse than no health endpoint.
#
# MCP_SERVER_NAME itself must NOT change: the tool-call prefix
# `mcp__lazy-tool-service__*` derives from it and is baked into enabledTools
# here, into the gateway's own catalog, and into personas across several repos.
# This tuple is only for "is our server present in that registry".
MCP_SERVER_NAMES = tuple(
    n.strip() for n in os.getenv(
        "MCP_SERVER_NAMES", f"{MCP_SERVER_NAME},lazy-agent-service").split(",")
    if n.strip())
FORK_AGENT_ID = os.getenv("FORK_AGENT_ID", "HTML_NOTES")
import pathlib
import time
import uuid
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html, _host_of, map_document_html, map_payload, _render_markdown, esc
import base64 as _base64


logging.basicConfig(level=logging.INFO)

import logging

# httpx logs every request URL at INFO — including query strings, which is how
# the TomTom key (a ?key= param on every proxied tile fetch) ended up in the
# logs in plaintext. Secrets travel in URLs in several integrations, so cap
# httpx at WARNING globally rather than redacting call sites one by one.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── Canvas state & turn concurrency ─────────────────────────────────────────
#
# Turns run CONCURRENTLY (the DGX Spark serves several generations at once), but
# the canvas is shared mutable state, so every write is a locked read-modify-
# write against the server's copy. Two things make that safe:
#
#   1. A turn never mutates its own stale snapshot. It re-reads the live canvas
#      inside the lock at the moment it commits, so a widget added by a turn
#      that finished in the meantime is still there.
#   2. Every commit bumps a version. The client applies a canvas only if it is
#      newer than the one already on screen, so a slow turn's SSE event arriving
#      late can't roll the canvas back over a faster turn's widget.
#
# The old design held the lock for a whole turn, which was correct but pinned
# throughput to one generation at a time.
_session_canvas: Dict[str, str] = {}
_session_locks: Dict[str, asyncio.Lock] = {}
_session_inflight: Dict[str, int] = {}

# Canvas versions must never go BACKWARDS across a restart. A plain per-session
# counter starting at 0 looked fine until you redeploy: the browser tab is still
# open holding canvasVersion=7, the fresh container starts emitting 1, 2, 3 — the
# client drops every one as stale and the canvas stops painting. The tool ran and
# the model truthfully says it added the widget, but nothing appears. Seeding from
# epoch-ms makes every new version beat anything a live tab could be holding.
_version_counter = itertools.count(int(time.time() * 1000))
_session_canvas_version: Dict[str, int] = {}

# Request ARRIVAL order. A media singleton (video/music) swap is a locked
# read-modify-write, so the last turn to COMMIT wins — but two video asks fired
# seconds apart can commit out of order (their YouTube scrapes finish at different
# speeds), and then the OLDER request's video overwrites the newer one ("the video
# doesn't change until I refresh"). Stamping each media widget with the arrival
# seq and refusing to replace a widget placed by a NEWER request fixes the order
# without touching the coexist path (which correctly relies on last-commit-wins).
# Epoch-ms seeded (like _version_counter) so a container restart still hands out
# seqs higher than any data-req-seq a live tab could be holding from before it.
_request_counter = itertools.count(int(time.time() * 1000))





# How many agent turns may generate at once. Beyond this, turns queue.
AGENT_CONCURRENCY = int(os.getenv("AGENT_CONCURRENCY", "4"))
_turn_semaphore = asyncio.Semaphore(AGENT_CONCURRENCY)

# canvas_read_dom / canvas_modify_dom arrive as separate HTTP calls from the
# gateway carrying no session id, so they resolve against the most recent one.
_last_active_session: str = ""











# Container 2nd-class → the widget_type the fast lane / router / add_widget uses.
# Keeps the summary's type names identical to canvas_add_widget's widget_type so a
# reuse decision ("there's already a map") maps straight to an id the caller can
# pass back. Without this, maps/weather/products/stock were all reported as
# "custom", so the router couldn't tell one was already open and spawned a second.
_CANVAS_CLASS_TYPE = {
    "map-widget": "map", "weather-widget": "weather", "data-card": "data_card",
    "image-widget": "image", "products-widget": "products", "chart-widget": "chart",
    "scoreboard": "scoreboard",
    "crypto-card": "crypto_card", "wallet-graph-widget": "wallet_graph",
    "app-grid-widget": "app_grid",
    "action-confirm-widget": "action_confirm",
}
_CANVAS_XDATA_TYPE = {
    "checklistWidget": "checklist", "clockWidget": "clock", "notesWidget": "notes",
    "musicPlayerWidget": "mini_music_player", "youtubePlayerWidget": "youtube_player",
    "stockCardWidget": "stock_card", "converterWidget": "converter",
    "cryptoCardWidget": "crypto_card",
    "reminderWidget": "reminder", "settingsWidget": "settings",
    "appGridWidget": "app_grid",
    "actionConfirmWidget": "action_confirm",
}










# Types that should exist at most once on the canvas: a new ask UPDATES the open
# one instead of adding a second. Media (video/music) already swap via
# _place_media_widget; these are the data widgets that were stacking duplicates.
SINGLETON_WIDGET_TYPES = {"map", "weather", "app_grid"}

# Roles that should exist at most once, identified by their widget id PREFIX
# rather than their widget_type. Needed because a role's rendered type is not
# stable: traffic renders as `map` or `iframe_app` depending on whether the place
# geocoded, so type-keyed reuse silently stacked a duplicate whenever consecutive
# asks landed on different sides of that fork. Deliberately narrow — a shared
# prefix means "same slot on the canvas", which is only true for these roles;
# widening it to e.g. "answer" would merge two unrelated cards into one.
SINGLETON_ROLE_PREFIXES = {"traffic", "map", "weather"}

# Answer-style cards where a *follow-up on the same thread* should refine the open
# card in place instead of stacking a new one — but a genuinely NEW subject still
# gets its own card. Unlike SINGLETON_WIDGET_TYPES (always one), reuse here is
# conditional: the ask must read as a follow-up (deictic phrasing) or share a
# subject with the open card. This is the deterministic half of the "stop making a
# fresh widget for every follow-up" fix; the model-driven target decision is P2.
TOPIC_SINGLETON_TYPES = {"data_card", "scoreboard", "stock_card",
                         "profile_card", "timeline", "crypto_card", "wallet_graph"}

# Every type a follow-up may UPDATE IN PLACE. The factory renders 24 types but
# only a subset is reuse-eligible, so "only the waterproof ones" against a
# products grid or "make it a bar chart" against a chart could NEVER edit the
# open widget — it stacked a duplicate, by construction rather than by bug.
# Deliberately excluded: mini_music_player / youtube_player (media swap already
# goes through _place_media_widget), clock / notes / iframe_app / converter
# (user-owned state — a mistargeted follow-up would destroy content the user
# typed or reset a running timer). reminder IS reusable: "actually make that 30
# minutes" must retarget the open countdown, not stack a second alarm.
REUSABLE_WIDGET_TYPES = (
    SINGLETON_WIDGET_TYPES | TOPIC_SINGLETON_TYPES
    | {"products", "chart", "multi_chart", "checklist", "image", "reminder",
       "table", "kpi_row", "versus_card", "progress"}
)

# Overlap at or above this counts as "this ask is about that widget". 0.5 was
# measured against real follow-ups: it clears "what about cheaper sandals?" vs
# "Best Waterproof Sandals" (0.50) while leaving a genuinely new subject at 0.
_REUSE_SCORE_THRESHOLD = 0.5

# Deictic follow-up phrasing: the ask points back at the current thread rather than
# opening a new subject. "tell me about X" is deliberately NOT here (it's a fresh
# topic); "what about X", "wait...", "tell me more", a leading pronoun, etc. are.
_FOLLOWUP_RE = re.compile(
    r"^\s*(?:wait|hold on|hmm+|ok(?:ay)?|so|but|actually|and|also|oh)\b"
    r"|(?:what|how)\s+about\b"
    r"|what\s+(?:happened|else|other)\b"
    r"|tell\s+me\s+more\b|(?:more|go\s+deeper|expand|elaborate|dig\s+in)\b"
    r"|^\s*(?:it|its|it's|that|this|those|these|they|them|their|he|she|his|her)\b"
    r"|^\s*(?:why|how\s+come|and\s+then|what\s+next)\b",
    re.I,
)

# Refinement openers _FOLLOWUP_RE misses. "only show waterproof ones" / "just the
# cheap ones" name no pronoun and open no new subject — they narrow what is
# already on screen. Measured: with a widget open, the agent answered these in
# prose and called NO tools, so the canvas never changed; phrasing the SAME ask
# explicitly ("update the existing widget to only show waterproof ones") made it
# call canvas_add_widget correctly. So detect them and make the ask explicit.
_REFINE_RE = re.compile(
    r"^\s*(?:only|just|filter|narrow|limit|sort|order|group|rank|"
    r"show\s+(?:only|just|me\s+only)|make\s+it|change\s+it|turn\s+it|"
    r"swap|replace|instead|drop\s+the|remove\s+the|add\s+the|without)\b",
    re.I,
)


# Refinement markers that are NOT sentence openers, so the ^-anchored _REFINE_RE
# misses them: "waterproof only please", "cheaper ones", "under $50". These carry
# no subject of their own — an anaphor ("ones"), a comparative, or a bare
# constraint — so they can only mean "narrow what is already on screen".
_REFINE_MARKERS_RE = re.compile(
    r"\b(?:ones?|instead|version|variant|option)\b"
    r"|\b(?:cheap|expensive|big|small|fast|slow|new|old|close|short|long|"
    r"high|low|good|bad|light|heavy|wide|narrow)(?:er|est)\b"
    r"|\b(?:under|over|below|above)\s*\$?\d"
    r"|\b(?:less|more|fewer|cheaper)\s+than\b"
    r"|\bonly\b|\bjust\b",
    re.I,
)


def _is_refining_followup(message: str) -> bool:
    """True when the ask reads as a refinement of what is already on screen —
    deictic ("what about the cheaper ones", "tell me more"), a narrowing opener
    ("only show waterproof ones"), or a non-opener narrowing marker ("cheaper
    ones", "under $50"). Callers must ALSO require that a focus widget actually
    exists before acting on this."""
    m = message or ""
    return bool(_FOLLOWUP_RE.search(m) or _REFINE_RE.search(m)
                or _REFINE_MARKERS_RE.search(m))


# Tokens that carry no subject signal — dropped before measuring topic overlap.
_SUBJECT_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "what", "whats", "how", "about", "me", "my", "i", "need",
    "you", "please", "tell", "show", "give", "with", "get", "want", "some",
    "latest", "news", "update", "updates", "now", "today", "current", "recent",
    # Filler that dilutes the overlap coefficient. "is teva any good?" against a
    # card listing "Teva, Chaco, Keen" scored 0.33 (1 of 3 query tokens) and
    # missed the 0.5 threshold purely because "any"/"good" carry no subject —
    # the distinctive proper-noun hit is the whole signal.
    "any", "all", "good", "bad", "best", "worst", "one", "ones", "thing",
    "things", "like", "know", "see", "look", "much", "many", "really", "very",
    "still", "even", "also", "just", "only", "more", "less", "other", "another",
    "same", "different", "kind", "sort", "type", "stuff", "there", "here",
    # Deictic narrative filler: "what happened next?" is a pure follow-up, but
    # "happened"/"next" counted as subject tokens and made the fresh-subject
    # guard read it as a new topic.
    "happened", "next", "then", "else", "again", "info", "information",
    # Vague-scope filler. Live: "tell me more about the deals at costco
    # anything hardware related?" scored 2/5 against the costco card — under
    # the 0.5 threshold purely because "anything"/"related" diluted the
    # overlap. The subject is {deals, costco, hardware}; these words scope it,
    # they don't name it.
    "anything", "something", "everything", "related", "specifically", "etc",
}


def _subject_tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _SUBJECT_STOP and len(w) > 2}


def _tok_edit_within(a: str, b: str, cap: int) -> bool:
    """Levenshtein(a, b) <= cap, with early exits. Tiny inputs only (tokens)."""
    if abs(len(a) - len(b)) > cap:
        return False
    if a == b:
        return True
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[-1])
        if best > cap:
            return False
        prev = cur
    return prev[-1] <= cap


def _fuzzy_hit(tok: str, toks: set) -> bool:
    """Does `tok` appear in `toks`, allowing a typo's worth of edit distance?

    Word-boundary EXACT matching exists because loose matching caused real
    wrong-widget edits ("john" ⊂ "Johnny"). Fuzziness here is bounded so it
    can only absorb misspellings, never substrings or different words:
      len < 4  → exact only ("map"/"mop" must not match)
      len 4-7  → edit distance 1 ("mikku"→"miku", "jass"→"jazz")
      len 8+   → edit distance 2 ("birkenstok"→"birkenstock")
    """
    if tok in toks:
        return True
    cap = 0 if len(tok) < 4 else (1 if len(tok) < 8 else 2)
    if not cap:
        return False
    return any(_tok_edit_within(tok, t, cap) for t in toks)


def _subject_overlap(a: str, b: str) -> float:
    """Overlap coefficient of the content words in two short strings (0..1).
    Robust to length differences (a 2-word query vs a longer widget title).
    Typo-tolerant via _fuzzy_hit — "birkenstok" still counts against a
    Birkenstock card."""
    ta, tb = _subject_tokens(a), _subject_tokens(b)
    if not ta or not tb:
        return 0.0
    hits = sum(1 for t in ta if _fuzzy_hit(t, tb))
    return hits / min(len(ta), len(tb))




def _ledger_details(session_id: str) -> Dict[str, str]:
    """widget_id -> its most recent content gist from the turn ledger. This is
    what _widget_detail was always recorded FOR ("what about the taco bell
    one?"); until now nothing consumed it for targeting."""
    out: Dict[str, str] = {}
    try:
        for entry in _session_turn_ledger.get(session_id, []):
            for w in entry.get("widgets", []):
                if w.get("id") and w.get("detail"):
                    out[w["id"]] = w["detail"]
    except Exception as e:
        logger.warning(f"_ledger_details failed: {e}")
    return out




# ── Stacking in-place updates ────────────────────────────────────────────────
# A follow-up that updates a widget used to hard-REPLACE its content, so two
# quick follow-ups on the same thread wiped data the user was still reading
# (live: a market-news card replaced itself twice in a row and the middle
# update was never seen). Same-widget data_card updates now STACK: newest
# content on top, previous content kept under an "Earlier" rule, until the
# card reaches a word budget — then the oldest text rolls off the bottom.
# In-memory per session, same lifetime as the turn ledger.
_session_widget_configs: Dict[str, Dict[str, dict]] = {}
_STACK_WORD_BUDGET = 800     # user-tuned: "500-1000 words" — split the range
_STACK_MIN_KEEP = 60         # don't bother stacking a stub smaller than this

_EARLIER_RULE = "\n\n---\n\n**Earlier**\n\n"




def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))




# ── Conversation self-awareness: the turn ledger ─────────────────────────────
# A compact per-session record of what each turn did — the message, the route,
# and the widgets it produced with a short gist of their content. This is the
# substrate that lets every routing tier reason about the thread ("3 turns ago I
# built a news card about military health; this follow-up refines it") instead of
# re-reading canvas HTML or a message log with widget contents stripped out.
# In-memory: same lifetime as the canvas version / media cursors — it only needs
# to survive within a conversation.
_session_turn_ledger: Dict[str, List[dict]] = {}
_LEDGER_MAX_TURNS = 10














def _followup_target_id(session_id: str, focus_id: Optional[str],
                        message: str) -> Optional[str]:
    """The widget a refining follow-up should edit.

    focus_id is pure RECENCY (the last widget built), and the follow-up
    directive/rewrite used to hard-target it. That's right for deictic asks
    ("tell me more", "make it a table") but wrong the moment the follow-up
    NAMES a subject: live, "tell me more about the deals at costco" arrived
    right after a Birkenstock card was built, tripped the "tell me more"
    trigger, and the directive ordered an in-place rewrite of the SANDALS
    widget. The topical scorer that would have picked the Costco card
    (find_reuse_target) never ran — the directive pre-empted it.

    So: score the message against EVERY canvas widget (title + ledger gist,
    the same two signals find_reuse_target uses — but across ALL types, since
    the directive can't know which widget_type the model will pick). A
    confident topical winner beats recency; a subject-free deictic ask scores
    0 everywhere and keeps the recency focus."""
    scored = []
    canvas_html = ""
    try:
        canvas_html = get_session_canvas(session_id)
        details = _ledger_details(session_id)
        for order, (wid, _wtype, title) in enumerate(_iter_canvas_widgets(canvas_html)):
            if not wid or wid == "unknown":
                continue
            scored.append((_score_widget_for_query(message, title or "",
                                                   details.get(wid, "")), order, wid))
    except Exception as e:
        logger.warning(f"_followup_target_id scoring failed: {e}")
    top = [c for c in scored if c[0] >= _REUSE_SCORE_THRESHOLD]
    if top:
        best = max(s for s, _, _ in top)
        # Contenders within epsilon of the best. A name can legitimately live
        # in several gists — live, "tell me more about jimothy" scored 1.00
        # against BOTH the jimothy card and a reddit-lawsuit card whose gist
        # mentioned him, and first-in-DOM-order won: the lawsuit card got
        # overwritten with meme-coin content. On a tie the recency focus wins
        # (it's the thread the user is in); otherwise the most recent match.
        contenders = [c for c in top if best - c[0] <= 0.1]
        if focus_id and any(wid == focus_id for _, _, wid in contenders):
            return focus_id
        _score, _order, best_id = max(contenders, key=lambda c: (c[0], c[1]))
        if best_id != focus_id:
            logger.info(f"[WIDGET TARGET] follow-up names a subject — topical "
                        f"#{best_id} (score {_score:.2f}, {len(contenders)} "
                        f"contender(s)) beats recency #{focus_id}")
        return best_id
    # Title+gist missed. Before falling back, look INSIDE the widgets: the
    # canvas holds every card's full rendered text, and the name a follow-up
    # references is usually in a card BODY the ≤200-char gist can't cover.
    # Live: a 2000-char sushi card listed "Miku" ~700 chars in — gist blind,
    # so "tell me more about Miku" targeted the newest widget (a video) and
    # became Hatsune Miku. Require EVERY subject token to appear in the body
    # (full coverage — the tightest signal available), and take a UNIQUE hit
    # only; two body matches mean the name is ambiguous on canvas, and
    # guessing between them is how wrong-widget edits happen.
    msg_toks = _subject_tokens(message)
    if msg_toks and canvas_html:
        try:
            soup = BeautifulSoup(canvas_html, "html.parser")
            hits = []
            for card in soup.select(".glass-card, .widget-container"):
                wid = card.get("id", "")
                if not wid or wid == "unknown":
                    continue
                body_toks = _subject_tokens(card.get_text(" ", strip=True))
                if all(_fuzzy_hit(t, body_toks) for t in msg_toks):
                    hits.append(wid)
            if len(hits) == 1:
                logger.info(f"[WIDGET TARGET] follow-up subject found in the BODY "
                            f"of #{hits[0]} — beats recency #{focus_id}")
                return hits[0]
        except Exception as e:
            logger.warning(f"_followup_target_id body scan failed: {e}")
    # No widget matches. If the message CARRIES a subject anyway, it's a new
    # topic that happened to trip the (loose) refinement regex — live, "find
    # me MORE info on birkenstock arizona" matched `more\b` right after the
    # costco card was built, and the recency fallback ordered birkenstock
    # content INTO the costco card. Two or more content words that match
    # nothing on canvas mean a fresh subject: no directive, let the agent
    # open a new widget. One or zero content words ("tell me more", "what
    # about the cheaper ones") is genuinely deictic — keep the recency focus.
    if len(_subject_tokens(message)) >= 2:
        logger.info(f"[WIDGET TARGET] follow-up phrasing but fresh subject "
                    f"(no canvas match) — not forcing an in-place update")
        return None
    return focus_id












@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _warn_if_research_is_down()
    watchdog = asyncio.create_task(_mcp_watchdog())
    try:
        yield
    finally:
        watchdog.cancel()


app = FastAPI(
    title="HTML-Notes Engine",
    description="Local-first AI knowledge journal with constrained HTML rendering",
    lifespan=_lifespan,
)


async def _try_reconnect_mcp() -> bool:
    """Ask prism to dial our MCP server, returning True if it came up.

    The outage behind the 2026-07-20 debug wave was exactly one un-dialled
    connection: registration present, `enabled: true`, the :5591/mcp/sse endpoint
    live and serving — prism had simply never connected, with `lastError: null`.
    It was invisible for hours and fixed by a single POST. So do that POST
    ourselves rather than waiting for someone to notice.

    GOTCHA worth keeping: `GET /mcp-servers` is SCOPED BY HEADERS. Without
    x-project/x-username it returns `[]` for ANY state, which reads as "nothing is
    registered" and sends you looking in the wrong place entirely."""
    headers = {"x-project": AGENT_PROJECT, "x-username": AGENT_USERNAME}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            servers = (await client.get(f"{PRISM_URL}/mcp-servers",
                                        headers=headers)).json()
            servers = servers if isinstance(servers, list) else servers.get("servers", [])
            mine = next((s for s in servers if s.get("name") in MCP_SERVER_NAMES), None)
            if not mine:
                logger.error(f"[MCP] {MCP_SERVER_NAME} is not registered for "
                             f"{AGENT_PROJECT}/{AGENT_USERNAME} — cannot reconnect; "
                             f"lazy-tool-service registers itself on ITS boot.")
                return False
            if mine.get("connected") and int(mine.get("toolCount") or 0) > 0:
                return True
            sid = mine.get("id") or mine.get("_id")
            logger.warning(f"[MCP] {MCP_SERVER_NAME} registered but not connected "
                           f"(toolCount={mine.get('toolCount')}) — asking prism to dial it")
            await client.post(f"{PRISM_URL}/mcp-servers/{sid}/connect", headers=headers)
            await asyncio.sleep(3)
            servers = (await client.get(f"{PRISM_URL}/mcp-servers",
                                        headers=headers)).json()
            servers = servers if isinstance(servers, list) else servers.get("servers", [])
            mine = next((s for s in servers if s.get("name") in MCP_SERVER_NAMES), None) or {}
            tools = int(mine.get("toolCount") or 0)
            if mine.get("connected") and tools > 0:
                logger.info(f"[MCP] reconnected — {tools} tools")
                return True
            logger.error(f"[MCP] reconnect did not take (connected="
                         f"{mine.get('connected')}, toolCount={tools})")
            return False
    except Exception as e:
        logger.warning(f"[MCP] reconnect attempt failed: {e}")
        return False


async def _mcp_watchdog() -> None:
    """Re-check the MCP link periodically and reconnect on a drop.

    Never retries hot: one attempt per interval, and every attempt is logged so a
    flapping link is visible rather than silently papered over."""
    while True:
        try:
            await asyncio.sleep(_MCP_WATCHDOG_INTERVAL)
            status = await _agent_dependency_status()
            if not status.get("ok"):
                logger.warning(f"[MCP] watchdog saw a degraded research path: "
                               f"{status.get('error')}")
                await _try_reconnect_mcp()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[MCP] watchdog iteration failed: {e}")


_MCP_WATCHDOG_INTERVAL = int(os.getenv("MCP_WATCHDOG_SECONDS", "300"))


async def _warn_if_research_is_down() -> None:
    """Shout at boot if tier-3 research can't work.

    The MCP registration lives in another service and can silently go empty (a
    prism restart drops it). Nothing noticed until a user asked a research
    question minutes or days later and got a text-only answer — the failure was
    indistinguishable from "the model chose not to make a widget". Check once at
    boot so the container log names the problem the moment it exists.

    Never raises: a boot check that can take the app down is worse than the bug
    it reports."""
    try:
        status = await _agent_dependency_status()
        if not status.get("ok") and await _try_reconnect_mcp():
            status = await _agent_dependency_status()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[BOOT] agent dependency check failed to run: {e}")
        return
    # MCP being up is not enough: with every search backend unreachable the agent
    # has tools that all return nothing, which is what "research path OK" claimed
    # for an unknown length of time while DuckDuckGo was unreachable.
    try:
        _hits, engines_down = await web_search_ex("test", 3)
    except Exception as e:
        _hits, engines_down = [], True
        logger.warning(f"[BOOT] search probe raised: {e}")
    if engines_down:
        logger.error(
            "[BOOT] WEB SEARCH IS DOWN — every search backend is unreachable. "
            "Research asks will have no data to work from. Check "
            "SCRAPER_SERVICE_URL and outbound network connectivity.")
    if status.get("ok") and not engines_down:
        logger.info(f"[BOOT] research path OK — {status.get('tool_count')} MCP tools "
                    f"via {status.get('prism')}, web search reachable")
        return
    if status.get("ok"):
        return
    logger.error(
        "[BOOT] RESEARCH PATH DOWN: %s. Tier-2 asks (weather/stock/sports/map) "
        "still work; tier-3 research asks will answer in text with no widget. "
        "Detail: %s",
        status.get("error", "unknown"), json.dumps(status)[:400])

# Request / Response Schemas

class MessageRequest(BaseModel):
    session_id: str
    message: str
    target_note_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    current_canvas: Optional[str] = None
    # The widget the question came FROM, when the client knows it (the last one
    # the user interacted with, or an explicit "ask about this"). Everything else
    # in follow-up targeting is inference from the message text; this is a fact,
    # so it outranks the model's guess AND the topical score. Optional — the
    # server still infers when it's absent.
    focus_widget_id: Optional[str] = None
    # Version of the canvas the client's current_canvas snapshot is based on
    # (the last `component` version it painted, or the seed from history load).
    # Lets _run_turn refuse a snapshot taken before another turn's commit.
    canvas_version: Optional[int] = None
    # False (default) = PRISM MODE: the agent runs on prism-service (:7777) with the
    # lazy-tool-service MCP research tools/harnesses, AND research/content asks
    # (products, general answers, images) are routed to that agent instead of being
    # short-circuited by local search-scrape builders. True = legacy: the :5591 fork
    # gateway + local fast-path builders (faster, but no real research).
    # Default flipped to the gateway (:5591) on 2026-08-16: prism mode is
    # currently unusable for the full-agent path — prism's /config-local
    # returns ZERO local providers, so model discovery falls into the
    # VLLM_URL fallback and prism rejects the turn with 'Unknown provider
    # "vllm-2"'. The gateway has all three vLLM instances registered and
    # verified working end-to-end (open_app flow, 2026-08-16). Flip back
    # only after prism's local instance registry is populated again.
    use_lazy_agent: bool = True

class CreateNoteRequest(BaseModel):
    title: str
    tags: List[str] = []
    links: List[str] = []
    canonical_blocks: List[Dict[str, Any]] = []
    rendered_html: str

class UpdateNoteRequest(BaseModel):
    note_id: str
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    links: Optional[List[str]] = None
    canonical_blocks: Optional[List[Dict[str, Any]]] = None
    rendered_html: Optional[str] = None

class LinkNotesRequest(BaseModel):
    source_note_id: str
    target_note_id: str

class TranscribeRequest(BaseModel):
    audio: str # Base64 audio payload

class TTSSynthesizeRequest(BaseModel):
    text: str

# API Endpoints





def is_query_vague(query_text: str) -> bool:
    """
    Checks if a query text contains meaningful content or is just conversational filler / general widget spawn commands.
    """
    if not query_text:
        return True
    
    # Lowercase & strip punctuation
    text = query_text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    
    # Hybrid Catalog of filler words/synonyms
    filler_words = {
        # Action verbs
        "add", "show", "open", "play", "create", "pull", "up", "get", "find", "search", "insert", "inject", "spawn", "display",
        # Articles & prepositions
        "a", "an", "the", "some", "any", "to", "on", "for", "with", "in", "at",
        # Conversational filler
        "please", "now", "here", "thanks", "thank", "you", "would", "like", "want", "need", "can", "could", "me", "us",
        # Widget type descriptors (predefined categories)
        "widget", "player", "video", "youtube", "yt", "channel", "clip", "stream", "online", "notes", "notepad", "scratchpad", 
        "clock", "time", "checklist", "todo", "todolist", "task", "list", "music", "radio", "song", "audio", "lofi", "beats"
    }
    
    words = text.split()
    meaningful_words = [w for w in words if w not in filler_words]

    # Returns True if no meaningful words are left
    return len(meaningful_words) == 0

# Words that never describe a genre/artist in a music ask. Unlike the
# is_low_content catalog, genre terms like "lofi" and "beats" are kept.
MUSIC_FILLER_WORDS = {
    # Action verbs
    "add", "show", "open", "play", "create", "pull", "up", "get", "find", "search", "insert", "inject", "spawn", "display", "put", "start", "give",
    # Articles & prepositions
    "a", "an", "the", "some", "any", "to", "on", "for", "with", "in", "at", "of", "by",
    # Conversational filler
    "please", "now", "here", "thanks", "thank", "you", "would", "like", "want", "need", "can", "could", "me", "us", "hey", "hi",
    # Music/widget descriptors that aren't genres
    "widget", "player", "music", "radio", "song", "songs", "audio", "track", "tracks", "tune", "tunes", "playlist", "station",
}

def extract_music_genre(query_text: str) -> str:
    """Pull the requested genre/artist out of a music ask.
    "play reggae music" -> "reggae"; "open the music player" -> ""."""
    text = re.sub(r'[^\w\s]', '', (query_text or '').lower().strip())
    return " ".join(w for w in text.split() if w not in MUSIC_FILLER_WORDS)

# Media/data intent guards for the heuristic fast-path. When one of these
# matches, the request goes to the agent instead of keyword-spawning a widget
# ("clock for video" is a video ask, not a clock ask).
VIDEO_ASK_RE = re.compile(r'\b(youtube|video|videos|yt|watch|clip|clips|trailer|movie|highlights?)\b')

# A news video must be sorted by DATE. "fifa news video" searched by relevance
# returns whatever is most-watched for those words — a years-old recap — which is
# never what "news" means. Recency words flip the search to order='date' (a
# freshness NUDGE — the blended scorer still runs).
RECENCY_RE = re.compile(r'\b(news|latest|newest|recent|recently|today|tonight|breaking|update|updates|current|new)\b')

# STRICT recency: the user wants THE most recently published video, not the most
# relevant/popular one ("newest Paul Barron Network video", "bitcoin video from an
# hour ago"). This is stronger than RECENCY_RE — it BYPASSES the relevance/views
# scorer and the variety picker entirely and sorts purely by publish time, because
# freshness is only a 0.4-weight, year-scaled axis that can't tell 42 minutes from
# 3 days, so a brand-new upload always loses to an older popular one on the blend.
NEWEST_RE = re.compile(
    r'\b(newest|most[\s-]?recent|latest|just (?:posted|uploaded|dropped|released|out)|'
    r'brand[\s-]?new|freshest|last upload|latest upload)\b'
    r'|\b(?:in|from|over|within) the (?:past|last) (?:hour|few hours|day|24\s?h(?:ours?)?)\b'
    r'|\b\d+\s+(?:minutes?|mins?|hours?|hrs?)\s+ago\b'
    r'|\b(?:an?|one)\s+hour\s+ago\b'
    r'|\bthis (?:morning|afternoon|evening)\b|\bmoments ago\b|\bjust now\b', re.I)

# Words that describe the medium, not the subject. "fifa news video" should search
# YouTube for "fifa news", not for the literal word "video".
VIDEO_FILLER = {
    "video", "videos", "yt", "youtube", "clip", "clips", "watch", "pull", "up", "show",
    "me", "a", "an", "the", "of", "some", "play", "find", "get", "please", "for", "on",
    "give", "want", "see", "put", "add", "open", "stream", "streams", "streaming",
    "live", "livestream", "livestreams",
    # Format words: the FORM axis (parse_video_form) carries them, so they must
    # not leak into the channel-name guess or the topic filter — "newest mkbhd
    # short" was searching channels for "mkbhd short" and filtering that
    # creator's titles for the literal word "short".
    "short", "shorts",
    # Conversational scaffolding. Without these "i want to watch primeagen"
    # produced the channel-name guess "i to primeagen", which bound nothing and
    # dropped the ask to keyword search.
    # "new" is NOT here: it belongs to real channel names (New Rockstars). The
    # leading-"new" peel in _split_video_subject_topic handles "a new X video".
    "i", "you", "we", "to", "can", "could", "would", "let", "us", "my", "from",
    "by", "something", "anything", "there", "is", "are", "any",
}








# ── Persistent video/channel dislikes ───────────────────────────────────────
# Backed by the agent_memory table so a block survives sessions and restarts.
# Loaded once into in-memory sets so the hot search path never touches the DB;
# both are updated together when a new block is added.
_BLOCKED_VIDEO_CAT = "blocked_video"
_BLOCKED_CHANNEL_CAT = "blocked_channel"
_blocked_video_ids: set = set()
_blocked_channels: set = set()   # stored lowercased for case-insensitive match










# The last video shown per session, so a follow-up ("this one sucks, find
# another", "this channel sucks") knows what to exclude and what query to
# re-run. In-memory is fine: it only needs to survive within a conversation,
# while the block itself is what persists in the DB.
_session_current_video: Dict[str, dict] = {}

# Freshness parsed from the ORIGINAL user message of the tier-3 agent turn now
# in flight. /internal/execute is a separate HTTP request from lazy-agent-service
# carrying only {tool, args} — no session correlation exists — so this module
# global is how the tool handler recovers a time constraint the agent's query
# rewrite dropped. TTL-bounded and consulted LAST (after the agent's `freshness`
# arg and the query's own words). Caveat: two CONCURRENT agent turns could
# cross-apply a freshness bias; acceptable for this single-user deployment, and
# it is only a bias — the arg/query paths win whenever they carry a signal.
_pending_turn_freshness: Optional[tuple] = None   # (time.monotonic(), Freshness)
_TURN_FRESHNESS_TTL = 180.0


# The format axis rides along in the same stash and for the same reason: the
# agent's query rewrite drops "short" ("newest mkbhd short" → "mkbhd latest
# video") just as readily as it drops "this week".
_pending_turn_form: Optional[tuple] = None       # (time.monotonic(), form)


def _stash_turn_freshness(message: str) -> None:
    global _pending_turn_freshness, _pending_turn_form
    f = parse_freshness(message)
    _pending_turn_freshness = (time.monotonic(), f) if f else None
    form = parse_video_form(message)
    _pending_turn_form = (time.monotonic(), form) if form else None


def _clear_turn_freshness() -> None:
    global _pending_turn_freshness, _pending_turn_form
    _pending_turn_freshness = None
    _pending_turn_form = None


def _stashed_turn_freshness() -> Optional[Freshness]:
    if _pending_turn_freshness:
        ts, f = _pending_turn_freshness
        if time.monotonic() - ts < _TURN_FRESHNESS_TTL:
            return f
    return None


def _stashed_turn_form() -> Optional[str]:
    if _pending_turn_form:
        ts, form = _pending_turn_form
        if time.monotonic() - ts < _TURN_FRESHNESS_TTL:
            return form
    return None

# A rolling window of video ids already shown this session, so a repeat ask lands
# on something new instead of recycling the same top hit. Bounded so it can't grow
# without limit; oldest ids age out and can reappear later (fine — variety, not a
# permanent block, which is what the DB-backed blocklist is for).
_session_shown_videos: Dict[str, deque] = {}
_SHOWN_HISTORY = 24





# ── Channel- and date-verified recency video selection ──────────────────────
# "fox news video newest about the stock market" once returned a 40-view clip
# from an unrelated channel: RECENCY_RE stripped "news" out of the channel-name
# guess (so FOX News could never resolve to its uploads feed), and the fallback
# date-sorted a polluted query and picked at random — no channel check, no
# relevance floor. Verify BEFORE pulling: bind the named channel and read its
# strictly newest-first uploads feed (channel-correct by construction); only
# fall back to search when no channel binds, and even then hold a strict
# "newest" ask to a title-relevance floor (see _search_youtube_scrape).

_VIDEO_TOPIC_SPLIT_RE = re.compile(
    r'\b(?:about|regarding|covering|on the topic of|talking about)\b', re.I)
# Soft recency words safe to strip from a channel-subject guess. Deliberately
# EXCLUDES "news" and "new" — they are part of real channel names (FOX News,
# Sky News, New Rockstars); stripping them is exactly the bug this fixes.
_SUBJECT_RECENCY_RE = re.compile(
    r'\b(latest|newest|recent|recently|today|tonight|breaking|current|'
    r'update|updates)\b', re.I)




# Words that make a subject a TOPIC, not a creator. A channel-name guess built
# only from these must never bind a channel: "cookie recipe" resolves to a real
# channel called "1,000 Cookie Recipes", which would then answer every cookie
# question from one creator's feed instead of searching YouTube properly.
# Deliberately generic/instructional vocabulary only — never proper nouns.
_YT_TOPIC_WORDS = {
    "recipe", "recipes", "tutorial", "tutorials", "guide", "guides", "review",
    "reviews", "highlights", "explained", "how", "what", "why", "best", "top",
    "vs", "versus", "comparison", "cooking", "workout", "exercise", "music",
    "song", "songs", "movie", "movies", "trailer", "game", "games", "gameplay",
    "documentary", "lecture", "course", "tricks", "hacks", "diy",
    "unboxing", "reaction", "compilation", "meditation", "interview",
    "market", "stock", "stocks", "crypto", "weather", "score", "scores",
    "price", "prices", "history", "science", "math", "coding", "programming",
    # NOT listed, though they look generic: "tips" (Linus Tech Tips), "news"
    # (Fox News), "podcast", "clips", "highlights" — all appear in real channel
    # names, and listing them made those creators unbindable.
}








# Sports fixtures/scores. Without a tool these fell to the agent, which
# web-searched (20-60s) and tried to squeeze a scoreboard into a text card.
SCORE_ASK_RE = re.compile(
    r'\b(scores?|fixtures?|results?|standings?|schedule|matchups?|'
    r'who\'?s playing|whos playing|playing today|next (game|match|fight)|'
    r'card|bouts?|fights?)\b')
DATA_ASK_RE = re.compile(r'\b(news|headlines|weather|forecast|stock|price|chart|graph|image|images|picture|pictures|photo|photos)\b')

# Fast-path triggers for the two data widgets that now have real sources+renderers.
WEATHER_ASK_RE = re.compile(r'\b(weather|forecast|temperature)\b')
NEWS_ASK_RE = re.compile(r'\b(news|headlines?)\b')
# Stock news has its own cleaner path (html_notes_stock_news); keep it off the
# general news fast-path.
STOCK_WORD_RE = re.compile(r'\b(stock|stocks|ticker|shares|share price|crypto|nasdaq|equities?)\b')

# ── Crypto fast-lane detection ──────────────────────────────────────────────
# Deliberately CONSERVATIVE: the deterministic fast lane fires only on strong,
# unambiguous crypto signals; everything softer ("price of X") falls through to
# the LLM router, which now knows the crypto/wallet widget types. A cashtag
# ($PEPE) or a 0x contract is unambiguous; bare "coin"/"token" are NOT included
# alone because they'd steal non-crypto asks.
CRYPTO_WORD_RE = re.compile(
    r'\b(crypto\w*|blockchain|on[\s-]?chain|de[\s-]?fi|web3|memecoin|shitcoin|'
    r'altcoin|stablecoin|erc[\s-]?20|spl token|bitcoin|btc|ethereum|'
    r'dogecoin|doge|solana|sol|shiba|shib|pepe|bonk|cardano|ada|ripple|xrp|'
    r'litecoin|polkadot|avalanche|avax|chainlink|link|uniswap|uni|tether|usdt|'
    r'usdc|dai|matic|polygon|tron|trx|monero|xmr|dogwifhat|wif|floki|mog|'
    r'wojak|turbo|brett|degen|toshi)\b', re.I)
# Soft signal: "<name> token/coin/memecoin" is how people name a microcap
# ("jimothy token", "the doge coin"). "coin"/"token" are generic, so this only
# counts on a SHORT query (a lookup, not a sentence like "explain oauth tokens")
# — see the length guard in the fast lane. The builder pre-resolves via
# DexScreener and falls through on a miss, so a stray hit just costs one lookup.
CRYPTO_SOFT_RE = re.compile(r'\b(tokens?|coins?|memecoins?|shitcoins?)\b', re.I)
# A $CASHTAG ($PEPE) or a 0x… contract address — strong crypto/token signal.
CASHTAG_RE = re.compile(r'\$[A-Za-z][A-Za-z0-9]{1,9}\b')
EVM_ADDR_RE_MAIN = re.compile(r'\b0x[a-fA-F0-9]{40}\b')
# Holder-graph / distribution intent — "who holds X", "X whales", "is X a fair
# launch", "pump and dump", "wallet graph". Routes to the holder-network graph.
WALLET_GRAPH_RE = re.compile(
    r'\b(holders?|whales?|distribution|holder graph|wallet graph|connected wallets?|'
    r'pump[\s-]?and[\s-]?dump|pump.{0,4}dump|fair[\s-]?launch|fairly distributed|'
    r'who holds|concentration|top wallets|sybil|rug\w*|insiders?)\b', re.I)
# Inspect ONE address's holdings — needs an address AND holdings framing.
WALLET_INSPECT_RE = re.compile(
    r'\b(hold(?:s|ing|ings)?|balance|portfolio|owns?|inside|contents? of|whats? in)\b',
    re.I)
# A deep-dive / scam-check framing that means "written crypto report".
CRYPTO_REPORT_RE = re.compile(
    r'\b(deep[\s-]?dive|full (?:report|analysis|breakdown)|due diligence|\bdd\b|'
    r'report on|analyz[e]|analysis (?:on|of)|is (?:it|this|\w+) (?:a )?(?:scam|rug|'
    r'legit|safe|good)|should i (?:buy|ape)|tell me everything about|'
    r'research)\b', re.I)
# "market news" without a stock word — still a finance-news ask, routed with them.
MARKET_WORD_RE = re.compile(r'\bmarkets?\b')
# A COMPREHENSIVE stock report ask ("full report on NVDA", "deep dive tesla stock",
# "analyze apple stock", "due diligence on nvidia") — synthesizes every category
# (quotes+news+finnews+transcripts), distinct from the price CARD or news card.
STOCK_REPORT_RE = re.compile(
    r'\b(deep[\s-]?dive|full (?:report|analysis|breakdown|rundown|picture)|'
    r'comprehensive (?:report|analysis|overview)|(?:research|analyst) report|'
    r'due diligence|\bdd\b|report on|deep research|analyz[e]|analysis (?:on|of)|'
    r'breakdown of|tell me everything about)\b', re.I)

# DEEP MARKET RESEARCH — a research-intent word ("deep dive", "research",
# "in-depth", "analyze") that, combined with a MARKET word and NO specific
# company/ticker, means "research the market broadly" rather than the single-
# ticker report (STOCK_REPORT_RE + a resolvable company) or the fast news card.
# Routed to the shared DEEP_RESEARCH agent (multi-source fan-out + synthesis).

# TRIP planning — "plan a trip to Japan", "3 days in Rome", "Kyoto itinerary",
# "things to do in Lisbon". High-precision: requires an explicit trip/itinerary word
# or an "N days in <place>" shape, so it never steals a plain "map of X".
TRIP_ASK_RE = re.compile(
    r'\bitinerary\b'
    r'|\b(?:plan|planning)\b[^.?!]{0,40}\b(?:trip|vacation|holiday|getaway)\b'
    r'|\b(?:trip|vacation|getaway)\s+(?:to|for|in)\s+\w'
    r'|\b\d+\s*(?:day|days|week|weeks)\s+(?:in|trip)\b'
    r'|\bthings\s+to\s+do\s+in\b',
    re.IGNORECASE)

# SHOPPING / product recommendations — "good outdoor shoes", "best budget laptop",
# "where to buy a tent", "gift for a hiker". High-precision: an explicit buy/shop
# phrase, or a quality adjective sitting near a product-category noun. Keeps
# "best restaurants in NYC" (a map/POI) and "stock price" out.
SHOP_ASK_RE = re.compile(
    r'\b(?:shop(?:ping)?\s+for|where\s+(?:can\s+i\s+|to\s+)buy|looking\s+to\s+buy'
    r'|want\s+to\s+buy|gift\s+(?:for|idea))\b'
    r'|\b(?:best|good|top|great|recommend(?:ed)?|budget|cheap|affordable|quality|decent)\b'
    r'[^.?!]{0,30}\b(?:shoes|boots|sneakers|sandals|laptops?|headphones|earbuds|'
    r'phones?|smartphones?|watch|smartwatch|backpacks?|jackets?|coats?|cameras?|tvs?|'
    r'monitors?|keyboards?|mouse|mattress|chairs?|desks?|bikes?|bicycles?|tents?|'
    r'sleeping\s+bags?|cookware|blender|vacuum|gifts?|gear|sunglasses|wallets?|'
    r'luggage|suitcases?|speakers?|drones?)\b',
    re.IGNORECASE)

# "cnn live news" is a thing to WATCH, but it contains "news", so DATA_ASK_RE
# claimed it and the model was told to build a data_card — a text list of
# headlines, when what was asked for was a stream. Live intent has to outrank the
# data classification, and it needs YouTube's LIVE filter to land on an actual
# stream: a plain search for "cnn live news" returns recorded clips, the filtered
# one returns "CNN Headlines: 24/7 Live News".
LIVE_ASK_RE = re.compile(r'\b(live|livestreams?|live ?streams?|streaming)\b')

# "this one sucks, find another" — swap the CURRENT video for a different one and
# remember not to show the disliked video again. Gated on a video actually being
# on screen (see _session_current_video), so a bare "another one" only means
# "another video" when a video is what's playing.
ANOTHER_VIDEO_RE = re.compile(
    r'\b(another|different|other|next)\b.*\b(one|video|clip)\b'
    r'|\b(this|that)\s+(one|video|clip)?\s*(sucks?|is bad|is boring|is terrible)'
    r'|\b(hate|don\'?t like|do not like|dislike)\s+(this|that)\b'
    r'|\bnot\s+(this|that)\s+(one|video|clip)?\b'
    r'|\b(something|anything)\s+else\b'
    r'|\b(skip|change)\s+(this|the)?\s*(one|video|clip)?\b'
    r'|\bfind\s+(me\s+)?another\b')
# Escalation: the user is rejecting the CHANNEL, not just this video. Blocks the
# whole channel forever. Checked first — if it matches, we block the channel;
# otherwise a generic "another one" blocks just the single video.
CHANNEL_DISLIKE_RE = re.compile(
    r'\bchannel\b.*\b(sucks?|bad|boring|terrible|stop|no more|enough)\b'
    r'|\b(not|hate|block|ban|stop|no more)\b.*\bchannel\b'
    r'|\bdifferent\s+channel\b')

# ── Widget intent routing ───────────────────────────────────────────────────
#
# A widget ask has two halves: WHICH widget, and WHAT CONTENT goes inside it.
# The old router only read the first half — it substring-matched a widget noun
# and spawned an empty shell. Two failures fell out of that:
#
#   "notes for grocery list for chicken soup" contains "notes", so it spawned a
#   blank notepad and stopped — the grocery list, which is the entire point of
#   the request, was never produced.
#
#   "give me a grocery list" matched nothing, so it fell through to the full
#   agentic loop: ~60s of tool-calling for content no tool can supply.
#
# So: decide the widget AND whether it needs content. List intent outranks notes
# intent ("notes for a grocery list" is a list, not a notepad). Content the model
# can simply write is filled by ONE direct completion (~2s) instead of the agent.
LIST_INTENT_RE = re.compile(
    r'\b(grocery|groceries|shopping|packing|checklist|to-?dos?|task list|'
    r'bucket list|reading list|watch ?list|wish ?list|ingredients?)\b|\blists?\b')
# "add greek salad TO THE grocery list", "also add milk", "put eggs on the list".
# An EDIT of an existing checklist, not a request for a new one — distinguished
# from "add a grocery list" (create) by the "to/onto/on the ... list" / "also"
# shape. Merged into the existing widget in place; see _extract_existing_checklist.
LIST_EDIT_RE = re.compile(
    r'\b(add|append|include|put|throw in|toss in)\b.*\b(to|onto|on)\b.*\blists?\b'
    r'|\balso\s+(add|include|put|need|get|grab)\b'
    r'|\b(add|put|append|include)\b.*\bto (the|my|our|this|that)\b'
    # Conversational follow-ups after a list already exists: "oh and add steak",
    # "and throw in some milk", "add eggs too/as well". The route only fires when
    # a checklist is actually on the canvas, so these can't misfire into a new list.
    r'|\b(oh )?and\s+(add|include|put|append|throw in|toss in)\b'
    r'|\b(add|include|put|append)\b[^.]*\b(as well|too)\b')
# "delete the veggies FROM the grocery list" / "cross milk off the list" — an
# in-place item edit, NOT removing the whole widget. Distinguished from "delete
# the list" by the "…from/off/out of the … list" container shape (an item named
# between the verb and the list). Must be intercepted before wants_removal funnels
# it to the agent (which would decompose the entire checklist).
LIST_ITEM_REMOVE_RE = re.compile(
    r'\b(delete|remove|drop|take|cross|clear|get rid of|scratch)\b.*'
    r'\b(from|off|out of)\b.*\blists?\b'
    r'|\b(uncheck|untick)\b')
# "bring back my grocery list", "restore the list", "reopen my list", "that list
# again" — restore a previously-saved list from persistent state instead of
# regenerating a fresh one. Guarded so an "add X to my list again" (an edit) and
# item-removal don't get swallowed here.
LIST_RESTORE_RE = re.compile(
    r'\b(bring|get|put|pull|give)\b[^.]*\bback\b'
    r'|\brestore\b|\breopen\b'
    r'|\blists?\b[^.]*\bagain\b|\bagain\b[^.]*\blists?\b'
    r'|\bback\b[^.]*\blists?\b')
# "close out everything", "clear the whole canvas", "get rid of all the widgets",
# "wipe it", "start over" — clear the ENTIRE canvas in one server call. The agent
# path can only remove one widget per iteration and stops after the first commit,
# so it could never close them all.
CLEAR_ALL_RE = re.compile(
    r'\b(wipe|nuke)\b|\bstart (over|fresh|again)\b|\bclean slate\b'
    r'|\b(close|clear|remove|delete|hide|dismiss|get rid of|kill)\b[^.]*'
    r'\b(everything|every widget|all (the |my )?(widgets?|cards?)|all of (it|them)|'
    r'the (whole |entire )?(canvas|dashboard|screen)|it all)\b')
NOTES_INTENT_RE = re.compile(r'\b(notes?|notepad|scratch ?pad|memo|jot)\b')
# Calculator / unit / currency conversion. A bare "X to Y" or "N% of M" or a
# plain arithmetic expression opens the converter directly. Kept off the
# stock-compare "vs" phrasing (that's the multi-ticker chart) and off anything
# with a research verb.
CONVERT_INTENT_RE = re.compile(
    r'\b(convert|calculate|calculator)\b'
    r'|\b\d[\d,.]*\s*(?:%|percent)\s+(?:of|off)\b'
    r'|\b\d[\d,.]*\s*(?:[a-z°]{1,5}2?|\$|€|£|¥|₹)\s+(?:to|in|into|=)\s+[a-z°$€£¥₹]{1,5}\b'
    r'|^[\s\d.,()+\-*/^%×÷]+$', re.I)
# An UNAMBIGUOUS request for arithmetic: an explicit verb, a percentage, the
# message IS an expression, or "what is <expression>". These short-circuit the
# question-veto below, so "convert my recipe from cups to grams" stays a
# conversion even though it says "recipe".
#
# The trailing "what is <expr>" arm matters on its own: CONVERT_INTENT_RE's
# arithmetic arm is anchored ^...$, so "what is 15*23" matches NOTHING there and
# a naive `CONVERT_INTENT_RE and not veto` predicate would have pushed a plain
# calculator ask into research.
CALC_IMPERATIVE_RE = re.compile(
    r'\b(convert|calculate|calculator)\b'
    r'|\b\d[\d,.]*\s*(?:%|percent)\s+(?:of|off)\b'
    r'|^[\s\d.,()+\-*/^%×÷]+$'
    r'|^\s*(?:what(?:\'?s| is)|how much is|whats)\s+[\d.,()+\-*/^%×÷\s]+\??$', re.I)
# A QUESTION that merely CONTAINS numbers and units is not a conversion. It
# wants a judgement that needs real-world knowledge — cooking times, doneness,
# food safety, dosages, travel time — and a calculator cannot answer any of
# them. This vetoes CONVERT_INTENT_RE's loose "<n> <unit> to/in <word>" arm,
# which matches on SHAPE alone: "how long should I cook 5 lb in the oven"
# satisfies it ("5" + "lb" + " in " + "the").
NUMERIC_QUESTION_RE = re.compile(
    r'\b(how long|how much longer|how many more|how do i|how does|how can|'
    r'should i|do i need|does it need|will it|would it|is it|is that|are they|'
    r'safe|safely|why is|why does|what temp\w*|what happens|recommend\w*|'
    r'recipe|cook|cooking|bake|baking|roast|grill|smoke|oven|thaw|defrost|'
    r'internal temp\w*|doneness|done yet|drive|driving|commute|flight)\b', re.I)


def is_conversion_ask(text: str) -> bool:
    """True only when the user is ASKING FOR arithmetic — not merely using
    numbers and units inside a question.

    The single predicate behind all three converter entry points (the always-on
    fast path, the tier-2 router builder and the agent's widget injector), so
    they cannot drift apart.

    Live failure 2026-07-31: "145F chicken breast with the carcass how long to
    get to 165 its been cooking for about 25 minutes in the oven at 400F"
    rendered a unit/currency CALCULATOR. Nothing in the stack distinguished
    "contains numbers and units" from "is a question about what those numbers
    mean", and build_converter_config's _UNIT_WORDS maps f/c/cup/tsp straight
    onto tabs, so cooking language reads as units.

    CONVERT_INTENT_RE itself is deliberately NOT tightened: it is load-bearing
    for build_converter_config's tab picker, it is already tuned, and it
    correctly rejected that message. The veto is layered on top instead.
    """
    t = (text or "").strip()
    if not t:
        return False
    # A stock comparison ("NVDA vs SPY") is a chart, never a conversion. Folded
    # in from the fast-path call site so every entry point inherits the guard.
    if re.search(r'\b[A-Za-z]{1,5}\s+vs\.?\s+[A-Za-z]{1,5}\b', t):
        return False
    if CALC_IMPERATIVE_RE.search(t):
        return True
    if not CONVERT_INTENT_RE.search(t):
        return False
    if NUMERIC_QUESTION_RE.search(t):
        return False
    # A real conversion is terse — "20 usd to eur", "5 miles in km". Prose long
    # enough to be a paragraph is a question that happens to contain a unit.
    return len(t.split()) <= 12


# Reminder / alarm at a time. "timer/stopwatch/countdown" stay with the clock
# widget; a reminder carries a THING to be reminded of, or an alarm time.
REMINDER_INTENT_RE = re.compile(
    r'\bremind me\b|\bset (a|an) (reminder|alarm)\b|\balarm (for|at)\b|\breminder to\b', re.I)
# Appearance/theme/settings — a UI-control verb (like CLEAR_ALL), handled by a
# deterministic fast-path so a bare "dark mode" flips instantly instead of
# spinning up the ~30s agent. Deliberately narrow: it must read as a look/skin
# change or an explicit settings request, not merely contain a color word.
THEME_INTENT_RE = re.compile(
    r'\b(theme|themes|appearance|palette|colou?r ?scheme|colou?rway|skin)\b'
    r'|\b(dark|light|day|night) ?mode\b'
    r'|\b(open|show|change|edit)\b[^.]*\bsettings\b'
    r'|\b(make|set|change|switch|turn)\b[^.]{0,30}\b('
    r'dark|light|forest|pastel|egg|eggshell|cream|midnight|ember|sunset|'
    r'grape|purple|synthwave|mono|monochrome|slate|green|orange)\b'
    r'|^\s*settings\s*$', re.I)
# A question/how-to/recipe that wants a SYNTHESISED answer (summary + sources),
# not a widget noun and not a data feed. Routed to build_answer_config so the
# user gets the actual recipe/steps/definition instead of the ~30-60s agent loop.
ANSWER_ASK_RE = re.compile(
    r'\b(recipe|recipes|how to|how do|how does|how can|tutorial|guide|'
    r'what is|what are|whats|what\'s|who is|who are|who was|when is|when was|'
    r'why is|why do|why does|explain|difference between|'
    r'vs\.?|versus|meaning of|definition of|instructions?|steps to)\b')
# A BROAD, rich informational ask that deserves a multi-modal COMPOSITION (an
# explanation + supporting image/video/news), not a single card. High-precision so
# it never steals a narrow single-intent ask (weather, a ticker, a timer, "how to
# boil an egg"): it wants the "give me the whole picture on <subject>" phrasings.
COMPOSE_ASK_RE = re.compile(
    r'\b(tell me (all |everything )?about|everything about|give me (a|the) '
    r'(rundown|overview|breakdown|primer|picture)\b|teach me about|walk me through|'
    r'introduce me to|overview of|(a|an) (overview|introduction|primer) (on|of|to)|'
    r'what should i know about|get me up to speed on|brief me on)\b', re.I)
# Informational framing on a NEWS ask that wants a SYNTHESIZED brief (a written
# answer + sources), not a wall of headline links. "tell me about the stock market
# news", "what's happening in the markets", "summarize today's news", "catch me up".
# Checked BEFORE the news link-list branches so the synthesis wins.
# A geo/location query that wants a MAP with markers, not a text answer. Checked
# before ANSWER so "where are the fires in California" pulls up a map. Kept to
# strong geo signals so a conceptual "why is X" never gets a map.
MAP_ASK_RE = re.compile(
    r'\b(map|maps|on a map|where are|where is|where\'?s|located|location of|'
    r'near me|nearby|fires? in|wildfires?|earthquakes?|flooding|floods?|'
    r'hurricanes?|tornado(es)?|show me where|whereabouts)\b')
# Travel-time / directions / traffic. There is no routing widget yet, so rather
# than a blank markers map (which looks broken) these resolve to a synthesised
# answer card with the actual guidance. Kept off MAP_ASK_RE — "how long to the
# airport" has no geo token and would otherwise fall through to a wall of links.
DIRECTIONS_ASK_RE = re.compile(
    r'\b(directions?|how (long|far)|travel time|drive time|commute|'
    r'traffic|route to|way to|get to|getting to|how do i get)\b')
# Wikipedia — "open a random wikipedia page", "wikipedia article about X".
WIKI_ASK_RE = re.compile(r'\bwiki(pedia)?\b')
# Framing words stripped so a Wikipedia ask reduces to a page title.
_WIKI_STRIP_RE = re.compile(
    r'\b(open|show|me|a|an|the|random|wikipedia|wiki|page|pages|article|'
    r'articles|about|on|of|for|please|pull up|look up|lookup|find|get)\b',
    re.IGNORECASE)

# Strip the words that name the widget or frame the request; whatever survives is
# the subject. "notes for grocery list for chicken soup" → "chicken soup".
TOPIC_STOPWORDS = MUSIC_FILLER_WORDS | {
    "note", "notes", "notepad", "scratchpad", "scratch", "pad", "memo", "jot", "down",
    "list", "lists", "checklist", "todo", "todos", "task", "tasks", "item", "items",
    "grocery", "groceries", "shopping", "packing", "ingredient", "ingredients",
    "widget", "new", "my", "make", "build", "and", "it", "about", "write", "keep",
    # Adjectives that describe the widget, not its subject — "quick notes" is a
    # blank notepad, not a note about the topic "quick".
    "quick", "simple", "blank", "empty", "little", "small", "basic", "plain", "just",
}


def extract_topic(text: str) -> str:
    """The subject of the ask, with widget/filler words removed. Empty means the
    user wants a blank widget ("notes") rather than a filled one ("notes on X")."""
    cleaned = re.sub(r'[^\w\s]', ' ', (text or '').lower())
    return " ".join(w for w in cleaned.split() if w not in TOPIC_STOPWORDS).strip()




async def _wiki_summary(topic: str) -> dict:
    """Raw Wikipedia REST summary for a named topic, or {} on any failure.
    Shared by the wikipedia card and the profile_card builder — the thumbnail it
    returns is API-sourced, never a model-typed URL."""
    slug = urllib.parse.quote((topic or "").strip().replace(" ", "_"))
    if not slug:
        return {}
    try:
        async with httpx.AsyncClient(
                timeout=8.0, follow_redirects=True,
                headers={"User-Agent": "html-notes/1.0 (canvas widget)"}) as client:
            r = await client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.info(f"[PROFILE] wiki summary miss for {topic!r}: {e}")
        return {}






_fast_model = {"name": None}

# Models that answer /v1/models but cannot serve /v1/chat/completions.
#
# The gateway's /config-local advertises embeddinggemma as
# `modelType: "conversation"` with `tools: ["Tool Calling"]` — measured live on
# 2026-09-05, and provably false: that endpoint 404s on /v1/chat/completions. A
# catalog's capability field is a CLAIM, and the agent selection loop was
# trusting it while taking the first entry in dict order. Treat a known
# embedding model as ineligible regardless of what the catalog says.
_NON_CHAT_MODEL_MARKERS = ("embed", "rerank", "bge-", "e5-", "gte-")


def _is_chat_capable_model(model_id: str) -> bool:
    """False for a model that cannot serve a chat completion, whatever the
    catalog claims about it."""
    name = (model_id or "").strip().lower()
    if not name:
        return False
    return not any(marker in name for marker in _NON_CHAT_MODEL_MARKERS)


# Which gateway provider to hand an agent turn to, in order. The Jetson's
# nemotron35 leads because it is the box that measurably works: native
# tool_calls parsed at 0.38-0.55s, versus Gold Spark head-of-line blocked at
# ~21 tok/s with requests sitting in "deferred". This is a PREFERENCE with a
# fallback, not a pin — an unavailable box is skipped and rejoins on its own.
PREFERRED_AGENT_PROVIDERS = ("vllm", "vllm-2")





# ── Intent grounding + relevance gate ────────────────────────────────────────
# The pipeline's quality problem was that a terse ask ("sandals") went straight to
# a generic search and whatever came back was rendered with NO check that it
# actually depicted the subject — so "sandals" returned Sandals-Resort promos,
# beach scenery and feet-on-a-dock stock photos. These helpers add the missing
# intent layer (turn the utterance into a disambiguated subject + an expanded
# retrieval query + the list of WRONG-match categories to reject) and a vision
# relevance gate that drops images that don't show the subject. Everything is
# best-effort and FAILS OPEN: any grading outage degrades to the old behaviour,
# never an empty canvas.

_GROUND_CACHE: dict = {}          # message(lower) -> grounded intent (FIFO-capped)
_GROUND_CACHE_MAX = 256






async def _fetch_image_data_url(client: httpx.AsyncClient, url: str,
                                max_bytes: int = 1_800_000) -> Optional[str]:
    """GET an image and return it as a base64 data: URL, or None. We pass images to
    the vision model as data URLs rather than remote URLs because the model fetches
    remote URLs server-side and many og:image CDNs block hotlinking (403/400) — a
    data URL is judged reliably. Skips non-images and anything over `max_bytes`."""
    try:
        resp = await client.get(url, headers={"User-Agent": _BROWSER_UA})
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            return None
        blob = resp.content
        if not blob or len(blob) > max_bytes:
            return None
        return f"data:{ctype};base64," + base64.b64encode(blob).decode("ascii")
    except Exception:
        return None


async def filter_images_by_relevance(subject: str, negatives: list, items: list,
                                     keep: int = 4, hyde: str = "",
                                     min_keep: int = 0) -> list:
    """Vision relevance gate. `items` are dicts carrying an 'image' (URL) and a
    'caption'/'title'. Fetches each image server-side, base64-encodes it, and asks
    the vision model in ONE batched call which ones genuinely depict `subject` (and
    match none of `negatives`). Returns the surviving items in ORIGINAL order.

    FAILS OPEN per-item: an image we can't fetch or the model can't judge is KEPT
    (the old behaviour) — the gate only ever DROPS an image it actively judged
    off-subject, so a grading/network outage never empties the grid. If the gate
    would leave fewer than `min_keep`, the original list is returned instead."""
    cands = [it for it in items if it.get("image")]
    if not cands:
        return items
    judged = cands[:8]                       # bound the vision call
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0),
                                 follow_redirects=True) as client:
        data_urls = await asyncio.gather(
            *[_fetch_image_data_url(client, it.get("image", "")) for it in judged])
    # Only images we successfully fetched can be judged; the rest fail open.
    fetched = [(i, du) for i, du in enumerate(data_urls) if du]
    if not fetched:
        return items                         # couldn't fetch any → keep all (fail open)
    neg_txt = ("; ".join(negatives)) if negatives else ""
    content = [{"type": "text", "text": (
        "You are a STRICT relevance filter for image search results. The user wants: "
        f"\"{subject}\". " + (f"An ideal result looks like: {hyde}. " if hyde else "") +
        (f"REJECT any image that instead shows: {neg_txt}. " if neg_txt else "") +
        "You are shown numbered images. Return ONLY JSON: "
        '{"keep": [<indices of images that CLEARLY and PRIMARILY depict what the '
        "user wants>]}. Reject off-topic images: a different meaning of the word, "
        "generic scenery, logos/brand art, ads, memes, or unrelated stock photos. "
        "Keep a plausible match; drop the clearly-wrong.")}]
    for i, du in fetched:
        cap = (judged[i].get("caption") or judged[i].get("title") or "")[:90]
        content.append({"type": "text", "text": f"Image [{i}] (caption: {cap}):"})
        content.append({"type": "image_url", "image_url": {"url": du}})
    data = await _fast_multimodal_json(content, max_tokens=120)
    keep_set = None
    if isinstance(data, dict) and isinstance(data.get("keep"), list):
        keep_set = {i for i in data["keep"] if isinstance(i, int)}
    if keep_set is None:
        return items                         # model failed → keep all (fail open)
    judged_idx = {i for i, _ in fetched}
    # Drop ONLY judged images the model rejected; unfetched/unjudged pass through.
    survivors = [it for j, it in enumerate(items)
                 if not (j < len(judged) and j in judged_idx and j not in keep_set)]
    if len(survivors) < max(min_keep, 1) and len(survivors) < len(items):
        logger.info(f"[IMG GATE] all-but-{len(survivors)} rejected for {subject!r}; "
                    f"keeping unfiltered to avoid an empty grid")
        return items
    if len(survivors) < len(items):
        logger.info(f"[IMG GATE] {subject!r}: kept {len(survivors)}/{len(items)} "
                    f"(dropped {len(items) - len(survivors)} off-subject)")
    return survivors[:keep] if keep else survivors


# The ONLY words safe to strip out of a news ask: question framing and the word
# "news" itself. Deliberately tiny, and deliberately NOT TOPIC_STOPWORDS — that
# set is MUSIC_FILLER_WORDS plus widget adjectives, and it eats "player",
# "track", "search", "small", "station" and "list", all of which carry the
# subject of a real news query. Note "us", "world" and "new" are absent here on
# purpose: "us china trade talks" and "new zealand" need them.
_NEWS_SCAFFOLDING = {
    "news", "headline", "headlines", "article", "articles", "story", "stories",
    "latest", "recent", "recently", "breaking", "update", "updates",
    "show", "showme", "give", "gimme", "tell", "get", "find", "pull", "fetch",
    "me", "please", "can", "you", "i", "want", "wanna", "id", "like",
    "whats", "what", "is", "are", "the", "a", "an", "about", "on", "of", "for",
    "regarding", "concerning", "any", "some", "more", "info", "information",
}


def _strip_news_scaffolding(message: str) -> str:
    """Conservative fallback topic for when grounding is unavailable.

    ground_query fails OPEN, returning retrieval_query = the raw message — which
    for "news about AI" would search the literal words "news about AI". This
    removes only the framing, so the fallback is a light clean rather than either
    extreme (the raw sentence, or the old music-stopword shredder).
    """
    words = re.findall(r"[a-z0-9]+", (message or "").lower())
    kept = [w for w in words if w not in _NEWS_SCAFFOLDING]
    return " ".join(kept).strip()


# Headline/publisher signatures for paid placement and stock-promo listicles.
# Kept next to the text relevance gate because they answer the same question from
# the other side: the gate asks "is this about the subject", this asks "is this an
# article at all".
_PR_SPAM_SIGNATURES = (
    "paid press release", "press release distributor", "globenewswire",
    "pr newswire", "prnewswire", "business wire", "businesswire",
    "accesswire", "stocks we like better", "motley fool", "zacks",
    "sponsored content", "advertorial", "partner content",
    # Stock-promo / listicle mills that the first list missed. Observed live
    # 2026-09-06 on "market news": a nasdaq.com-syndicated "162% upside target"
    # analyst piece rendered as a top source.
    "benzinga", "insider monkey", "simply wall st", "24/7 wall st",
    "invezz", "stocktwits", "marketbeat", "tipranks", "investorplace",
)
_PR_SPAM_TITLE_RE = re.compile(
    r"\b\d+\s+(?:best|top|stocks?|things|reasons|ways)\b.*\b(?:to\s+buy|you\s+should|right\s+now)\b"
    # "...Skepticism Amid 162% Upside Target", "price target raised to $92"
    r"|\b\d{2,3}%\s+(?:upside|downside)\b"
    r"|\bprice\s+target\b.*\b(?:raised|lifted|cut|lowered|to\s+\$\d)"
    r"|\b(?:strong\s+)?buy\s+rating\b|\bshould\s+you\s+buy\b",
    re.I)


# The general-ask vocabulary, formerly a local `_GENERAL` inside
# build_news_config and a local `_MARKETY` inside build_stock_news_config —
# two builders, two copies, and the classifier (step 4) needs the same sets.
# One copy here; every reader imports it.
_NEWS_GENERAL_WORDS = frozenset({
    "whats", "what", "s", "going", "on", "happening", "up", "new",
    "current", "events", "event", "anything", "something", "interesting",
    "any", "the", "is", "are", "in", "world", "lately", "now", "right",
    "hows", "how", "things", "there", "out", "cool", "hey", "so",
    "tell", "me", "show", "give", "whatsup", "sup", "good", "today",
    "todays", "day", "days", "daily", "for", "of", "all", "top", "global",
    "us", "usa", "america", "american", "morning", "evening", "tonight",
    "feed", "stories", "story", "summary", "brief", "please", "pls",
})
_MARKETY_WORDS = frozenset({
    "stock", "stocks", "market", "markets", "share", "shares", "price",
    "prices", "ticker", "tickers", "equities", "equity", "finance",
    "financial", "trading", "the", "wall", "street",
})


# ── the news classifier ──────────────────────────────────────────────────────
# One deterministic decision replaces five cascade branches that each had their
# own regex, their own precedence quirks and their own builder:
#   MARKET_RESEARCH_RE (matched "what's going on" unconditionally, so one extra
#   word sent an ask down a 60-90s grounded-research path), STOCK_REPORT_RE,
#   _NEWS_SYNTH_RE, the stock/market-news branch and the general news branch.
# Two of them also keyed on a raw `[A-Z]{2,5}` token, so the SAME intent routed
# differently depending on whether the user capitalised a word.
#
# The classifier answers four questions from the message alone — is this news
# at all, is it finance, does it name a subject, does it want depth — and hands
# ONE builder an explicit verdict. It never guesses from the raw text again
# downstream: build_news_card receives `general` as a bool, so "hello" can no
# longer be grounded into "hello (greeting)".

_FINANCE_NEWS_RE = re.compile(
    r"\b(wall street|s&p|dow jones|the fed|fomc|treasur(?:y|ies)|bond yields?|"
    r"earnings|ipo|nasdaq|nyse|premarket|after[- ]hours|"
    r"bitcoin|ethereum|solana|dogecoin|crypto(?:currency)?|altcoins?)\b", re.I)
_GENERAL_NEWS_RE = re.compile(
    r"\b(what'?s (?:going on|happening|new)|whats (?:going on|happening|new)|"
    r"catch me up|current events|top stories|fill me in|"
    r"get me up to speed|the rundown|the scoop)\b", re.I)
_DEPTH_RE = re.compile(
    r"\b(deep[\s-]?dive|in[\s-]?depth|deep research|comprehensive|thorough(?:ly)?|"
    r"full (?:report|analysis|breakdown|rundown)|brief me|write (?:me )?a brief|"
    r"summar(?:y|ize|ise))\b", re.I)
_PRICE_SHAPE_RE = re.compile(r"\b(chart|charts|price|prices|quote|ticker)\b", re.I)
_MARKET_SHAPE_RE = re.compile(r"\b(?:stock )?markets?\b|\bwall street\b", re.I)
_MARKET_FILLER_RE = re.compile(
    r"\b(deep|dive|research|depth|in|full|report|analysis|analyz\w*|comprehensive|"
    r"thorough\w*|breakdown|rundown|picture|overview|dig|into|what'?s?|going|"
    r"happening|moving|on|of|the|a|an|about|for|me|please|give|do|can|you|show|"
    r"get|tell|today\w*|now|current\w*|latest|recent\w*|stock\w*|market\w*|"
    r"share\w*|equit\w*|wall|street|econom\w*|trading|financ\w*|sector\w*|"
    r"news|update\w*|and|or|it|its|us|why|how|are|is|was|to|with|whats?|right|"
    r"day|days|week|this)\b", re.I)
_KNOWN_TICKER_WORDS = frozenset({
    "nvda", "tsla", "aapl", "msft", "googl", "goog", "amzn", "meta", "amd",
    "intc", "nflx", "spy", "qqq", "btc", "eth",
})


@dataclass
class NewsAsk:
    finance: bool
    general: bool
    depth: str            # "card" | "brief"
    kind: str             # "news" | "stock_report"
    subject_hint: str

    @property
    def id_prefix(self) -> str:
        if self.kind == "stock_report":
            return "stock-report"
        return "stock-news" if self.finance else "news"


def _has_ticker_token(raw: str) -> bool:
    """A specific ticker/company mention. An ALL-CAPS token counts only when the
    message is not itself all-caps and the token is not filler — the old
    branches keyed on a raw `[A-Z]{2,5}` and routed "STOCK MARKET NEWS" and
    "Deep dive on the US market" differently from their lowercase twins."""
    raw = raw or ""
    if CASHTAG_RE.search(raw):
        return True
    low = raw.lower()
    if any(w in _KNOWN_TICKER_WORDS for w in re.findall(r"[a-z]+", low)):
        return True
    if raw.isupper():
        return False
    filler = _NEWS_SCAFFOLDING | _NEWS_GENERAL_WORDS | _MARKETY_WORDS
    # Three to five letters. Two-letter caps are initialisms far more often
    # than tickers — "AI", "EU", "UK", "UN" — and "news about AI" is a general
    # subject, not a C3.ai ask. A real two-letter ticker still routes via a
    # cashtag ("$GM") or a stock word ("GM stock").
    for tok in re.findall(r"\b[A-Z]{3,5}\b", raw):
        if tok.lower() not in filler:
            return True
    return False


def classify_news_ask(raw: str, *, wants_removal: bool = False,
                      is_video_ask: bool = False, wants_music: bool = False,
                      league: Optional[str] = None) -> Optional["NewsAsk"]:
    """Deterministic: is this a news ask, and of what shape? None = not news.

    Exclusions reproduce the cascade's existing precedence — removal, video,
    live streams, music, sports scores, weather, wikipedia all own their word.
    """
    text = (raw or "").strip().lower()
    if not text or wants_removal or is_video_ask or wants_music:
        return None
    if LIVE_ASK_RE.search(text) or WEATHER_ASK_RE.search(text) or WIKI_ASK_RE.search(text):
        return None
    if league and SCORE_ASK_RE.search(text):
        return None

    ticker = _has_ticker_token(raw)
    finance = bool(ticker or STOCK_WORD_RE.search(text) or MARKET_WORD_RE.search(text)
                   or _FINANCE_NEWS_RE.search(text))
    # "deep dive on the stock market" names no subject once the depth and
    # research filler is gone — decide `general` on the residual, not the raw.
    for_general = _MARKET_FILLER_RE.sub(" ", _DEPTH_RE.sub(" ", text))
    general = (not ticker) and _is_general_news_ask(for_general, finance=finance)
    residual = " ".join(w for w in re.findall(r"[a-z0-9&]+", text)
                        if w not in _NEWS_SCAFFOLDING and w not in _NEWS_GENERAL_WORDS
                        and w not in _NEWSY and (not finance or w not in _MARKETY_WORDS))

    news_word = bool(NEWS_ASK_RE.search(text))
    general_phrase = general and bool(_GENERAL_NEWS_RE.search(text))
    # "stock market for the day please" — a general-market ask with no news
    # word, no price/chart word and no trending word. The router used to turn
    # this into a trending-stocks chart of five random tickers.
    market_shape = (finance and general and bool(_MARKET_SHAPE_RE.search(text))
                    and not _PRICE_SHAPE_RE.search(text)
                    and not TRENDING_STOCK_RE.search(text))
    # A report/deep-dive ask on a stock or the market is news-shaped even with
    # no "news" word: "nvda deep dive", "full report on tesla".
    report_shape = finance and bool(STOCK_REPORT_RE.search(text))
    if not (news_word or general_phrase or market_shape or report_shape):
        return None

    depth = "brief" if _DEPTH_RE.search(text) else "card"
    kind = "stock_report" if (STOCK_REPORT_RE.search(text) and finance and not general) else "news"
    return NewsAsk(finance=finance, general=general, depth=depth, kind=kind,
                   subject_hint=residual.strip())


def _is_general_news_ask(message: str, *, finance: bool = False) -> bool:
    """True when the message names no subject beyond "news" itself.

    "whats going on in the news" -> True. "stock market news" -> True (finance).
    "news about nvidia earnings" -> False. Decided from the MESSAGE, never from a
    grounding pass — ground_query will happily invent a subject for "hello".
    """
    words = re.findall(r"[a-z0-9]+", (message or "").lower())
    filler = _NEWS_SCAFFOLDING | _NEWS_GENERAL_WORDS | (_MARKETY_WORDS if finance else frozenset())
    residual = [w for w in words if w not in filler and w not in _NEWSY]
    # Two letters is a subject: "AI", "EU", "UK", "AMD". ("us" is filler and
    # already gone.) Requiring three silently turned "news about AI" into a
    # top-stories pull.
    return not any(len(w) >= 2 and not w.isdigit() for w in residual)


def _normalise_news_item(n: dict) -> dict:
    """One item shape for every provider: {title, url, image, meta, snippet, date,
    related_tickers}.

    The shared provider returns {meta, date, snippet}; finnews and Yahoo return
    {publisher, published, og_desc}; web search returns {url, snippet}. The old
    stock-news builder read only the finnews spelling, so on its PRIMARY tier
    every prompt header rendered as "(, )" and the card showed no publisher at
    all — the model was asked to summarise stories with no source and no date.
    """
    n = n or {}
    url = n.get("url") or n.get("link") or ""
    return {
        "title": (n.get("title") or "").strip(),
        "url": url,
        "image": n.get("image") or "",
        "meta": (n.get("meta") or n.get("publisher") or n.get("source") or _host_of(url) or "").strip(),
        "snippet": (n.get("snippet") or n.get("og_desc") or n.get("description") or "").strip(),
        "date": (n.get("date") or n.get("published") or n.get("published_at") or "").strip(),
        "related_tickers": list(n.get("related_tickers") or []),
    }


_OVERVIEW_GENERIC_WORDS = frozenset({
    "market", "markets", "stock", "stocks", "investor", "investors", "wall",
    "street", "today", "this", "the", "news", "analyst", "analysts", "earnings",
    "season", "week", "sector", "sectors", "catalyst", "catalysts", "rotation",
    "focus", "attention", "sentiment", "momentum", "volatility", "traders",
})


def _entity_tokens(text: str) -> set:
    """Proper-noun-ish tokens: Capitalised words (not sentence-initial unless
    also seen capitalised elsewhere), ALL-CAPS tickers, and numbers/figures."""
    out = set()
    for m in re.finditer(r"\$?\d[\d,.]*%?|\b[A-Z]{2,5}\b|\b[A-Z][a-z]{2,}\b", text or ""):
        tok = m.group(0)
        low = tok.lower().strip("$%,.")
        if low in _OVERVIEW_GENERIC_WORDS:
            continue
        out.add(low)
    return out


def _overview_is_grounded(overview: str, items: list) -> bool:
    """Does the overview name at least one specific thing that appears in its
    own sources? Deterministic, no LLM.

    "Market focus centers on biotech catalysts and semiconductor rotations as
    earnings season progresses" shares nothing concrete with a card whose
    stories are Viking Therapeutics and Snowflake — it could be printed on any
    day. "Snowflake (SNOW) reported Q2 revenue up 37%" shares "snowflake",
    "snow" and "37" with them. This is the acceptance check for the summariser,
    and the fallback trigger when it produces filler anyway.
    """
    if not overview or not items:
        return False
    pool = set()
    for it in items:
        pool |= _entity_tokens(it.get("title", ""))
        pool |= _entity_tokens(it.get("description", "") or it.get("snippet", ""))
    return bool(_entity_tokens(overview) & pool)


def _drop_pr_spam(items: list) -> list:
    """Remove paid-placement and stock-promo rows.

    Returns the FILTERED list even when that is empty. The previous version
    ended `filtered or raw_yahoo`, which reinstated every ad in exactly the case
    the filter existed for — an all-spam page came back in full. An empty news
    card is a truthful "nothing worth showing"; a card full of ads is not.
    """
    out = []
    for it in items or []:
        blob = " ".join(str(it.get(k) or "") for k in
                        ("title", "meta", "publisher", "source", "url")).lower()
        if any(sig in blob for sig in _PR_SPAM_SIGNATURES):
            continue
        if _PR_SPAM_TITLE_RE.search(str(it.get("title") or "")):
            continue
        out.append(it)
    return out


async def filter_items_by_relevance(subject: str, negatives: list, items: list,
                                    keep: int = 0, min_keep: int = 1,
                                    hyde: str = "") -> list:
    """Text relevance gate: drop articles that are not about `subject`.

    The text analogue of filter_images_by_relevance, and it exists because the
    news path had NO relevance check of any kind — news_search returns the first
    non-empty provider tier, the shared provider returns the first non-empty of
    five APIs, and the summarising pass was told to write one entry per story, so
    an off-topic article got a confident, well-written summary. That is precisely
    why the junk read as deliberate rather than as an error.

    Same two safety properties as the vision gate, for the same reasons:
      * FAILS OPEN — a model outage or an unparseable reply keeps every item, so
        grading can never be the reason a card is empty.
      * `min_keep` floor — with min_keep>=1, if the model rejects everything,
        return the original list (a blank card is a worse answer than a loose
        one). With min_keep=0 an all-rejected set returns [] and the CALLER
        escalates to another source — "every story is off-subject" is a verdict
        about the result set, not a grading failure.
    """
    rows = [it for it in (items or []) if (it.get("title") or "").strip()]
    if len(rows) <= 1:
        return items
    judged = rows[:10]
    listing = "\n".join(
        f'[{i}] {(it.get("title") or "")[:140]}\n'
        f'    {(it.get("snippet") or it.get("description") or "")[:200]}\n'
        f'    source: {it.get("meta") or it.get("source") or _host_of(it.get("url", ""))}'
        for i, it in enumerate(judged))
    neg_txt = "; ".join(n for n in (negatives or []) if n)
    data = await fast_llm_json(
        "You are a STRICT relevance filter for news search results. The user "
        f'asked about: "{subject}".\n'
        + (f"An ideal result: {hyde}.\n" if hyde else "")
        + (f"REJECT anything that is instead about: {neg_txt}.\n" if neg_txt else "")
        + "Also REJECT: press releases and paid placement, stock-promotion "
          "listicles ('5 stocks to buy now'), pure advertising, and articles that "
          "merely mention the subject in passing while being about something "
          "else.\n\nReturn ONLY JSON, no prose: "
          '{"keep": [<indices of the articles that are GENUINELY about what the '
          'user asked>]}\n\nARTICLES:\n' + listing,
        max_tokens=300)
    keep_set = None
    if isinstance(data, dict) and isinstance(data.get("keep"), list):
        keep_set = {i for i in data["keep"] if isinstance(i, int)}
    if keep_set is None:
        return items                      # model failed → keep all (fail open)
    survivors = [it for i, it in enumerate(judged) if i in keep_set]
    survivors += rows[len(judged):]       # anything past the judged window passes
    # `min_keep=0` means the CALLER owns the all-rejected case and wants the
    # honest empty list back so it can escalate to another source. This used to
    # read `max(min_keep, 1)`, which clamped 0 straight back to 1 — so the
    # escalation branch in build_news_config had never executed once, and the
    # user's own log showed "model rejected all 6 — keeping unfiltered" for a
    # query whose fix was supposed to be exactly that escalation. The test that
    # was meant to cover it used min_keep=1, so it was green while testing nothing.
    if len(survivors) < min_keep:
        logger.info(f"[NEWS GATE] {subject!r}: model rejected all "
                    f"{len(judged)} — keeping unfiltered rather than an empty card")
        return items
    if not survivors:
        logger.info(f"[NEWS GATE] {subject!r}: model rejected all {len(judged)} "
                    "— returning [] so the caller can escalate")
    if len(survivors) < len(rows):
        logger.info(f"[NEWS GATE] {subject!r}: kept {len(survivors)}/{len(rows)} "
                    f"(dropped {len(rows) - len(survivors)} off-subject)")
    return survivors[:keep] if keep else survivors


# Place name → IANA timezone, so "time in Tokyo" resolves server-side instead of
# depending on the LLM to emit "Asia/Tokyo" (it usually didn't, so the clock
# silently showed LOCAL time labelled as the city).
_TZ_BY_PLACE = {
    "new york": "America/New_York", "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "portland": "America/Los_Angeles",
    "chicago": "America/Chicago", "austin": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix",
    "toronto": "America/Toronto", "canada": "America/Toronto",
    "mexico city": "America/Mexico_City", "sao paulo": "America/Sao_Paulo",
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
    "paris": "Europe/Paris", "berlin": "Europe/Berlin", "madrid": "Europe/Madrid",
    "rome": "Europe/Rome", "amsterdam": "Europe/Amsterdam", "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai", "india": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "singapore": "Asia/Singapore",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul", "bangkok": "Asia/Bangkok",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "australia": "Australia/Sydney", "auckland": "Pacific/Auckland",
    "utc": "UTC", "gmt": "UTC",
}


def _resolve_timezone(text: str) -> str:
    """IANA timezone for a place named in the message, or "" if none is found."""
    t = (text or "").lower()
    for place in sorted(_TZ_BY_PLACE, key=len, reverse=True):  # "new york" before "york"
        if re.search(r'\b' + re.escape(place) + r'\b', t):
            return _TZ_BY_PLACE[place]
    return ""


def _parse_duration_seconds(text: str) -> int:
    """Total seconds from "5 minutes", "1 hour 30 min", "90 seconds", "2h". 0 if none."""
    total = 0
    for num, unit in re.findall(
            r'(\d+)\s*(h(?:ours?|rs?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)\b',
            (text or "").lower()):
        n = int(num)
        total += n * 3600 if unit.startswith("h") else n * 60 if unit.startswith("m") else n
    return total




def _extract_existing_checklist(session_id: str):
    """Find the most-recent checklist on the session canvas and return
    (widget_id, title, items[{text,done}]) or None. Items are baked into the
    widget's x-data="checklistWidget(<title>, <items>)" attribute; BeautifulSoup
    decodes the html-escaped JSON back to real JSON when it parses the canvas."""
    html_str = get_session_canvas(session_id)
    if not html_str or "checklistWidget(" not in html_str:
        return None
    soup = BeautifulSoup(html_str, "html.parser")
    found = None
    for div in soup.find_all("div", class_="widget-container"):
        if (div.get("x-data") or "").strip().startswith("checklistWidget("):
            found = div  # last one wins — the most recently added list
    if found is None:
        return None
    m = re.search(r'checklistWidget\(\s*(".*?"|\'.*?\')\s*,\s*(\[.*\])\s*\)\s*$',
                  found.get("x-data") or "", re.DOTALL)
    if not m:
        return None
    try:
        title = json.loads(m.group(1))
    except Exception:
        title = "Checklist"
    try:
        raw_items = json.loads(m.group(2))
    except Exception:
        return None
    items = []
    for it in raw_items:
        if isinstance(it, str) and it.strip():
            items.append({"text": it.strip(), "done": False})
        elif isinstance(it, dict) and str(it.get("text", "")).strip():
            items.append({"text": str(it["text"]).strip(), "done": bool(it.get("done"))})
    return found.get("id"), (title or "Checklist"), items






def _list_slug(title: str) -> str:
    """A stable key from a list title: 'Grocery List' -> 'grocery-list'."""
    s = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return s or "checklist"


def _persist_list_state(config: dict) -> None:
    """Save a checklist's items so it can be restored after it's closed. Keyed by
    the list's title slug (overwrite-in-place), plus a 'list:__last__' pointer to
    the most recent one so a bare 'bring my list back' resolves. Best-effort."""
    try:
        title = config.get("title") or "Checklist"
        items = config.get("items") or []
        if not items:
            return
        payload = json.dumps({"title": title, "items": items})
        database.set_widget_state(f"list:{_list_slug(title)}", payload)
        database.set_widget_state("list:__last__", payload)
    except Exception as e:
        logger.warning(f"[WIDGET STATE] persist failed: {e}")


def _resolve_restorable_list(message: str) -> Optional[dict]:
    """Find the stored checklist the user wants back. Prefers a stored list whose
    slug shares a meaningful word with the request ('grocery' -> list:grocery-list);
    falls back to the most recently saved list. Returns {title, items} or None."""
    try:
        states = database.list_widget_states("list:")
    except Exception as e:
        logger.warning(f"[WIDGET STATE] restore lookup failed: {e}")
        return None
    named = [s for s in states if s["key"] != "list:__last__"]
    stop = {"list", "lists", "checklist", "the", "my", "our", "that", "this",
            "back", "bring", "again", "get", "give", "show", "put", "pull",
            "restore", "reopen", "a", "an", "please", "me", "it", "up", "want"}
    words = {w for w in re.findall(r'[a-z]+', (message or "").lower()) if w not in stop}
    for s in named:
        slug_words = set(s["key"][len("list:"):].split("-"))
        if words & slug_words:
            try:
                return json.loads(s["value"])
            except Exception:
                pass
    last = database.get_widget_state("list:__last__")
    if last:
        try:
            return json.loads(last)
        except Exception:
            pass
    return None


# Currency codes the converter recognizes when classifying a seed.
_CURRENCY_CODES = {
    "usd", "eur", "gbp", "jpy", "cad", "aud", "chf", "cny", "inr", "mxn", "brl",
    "nzd", "sek", "nok", "dkk", "sgd", "hkd", "krw", "zar", "rub", "try", "pln",
}
_UNIT_WORDS = {
    "mm", "cm", "m", "km", "in", "inch", "inches", "ft", "foot", "feet", "yd",
    "yard", "yards", "mi", "mile", "miles", "nmi", "mg", "g", "gram", "grams",
    "kg", "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds", "st", "ml",
    "l", "liter", "litre", "liters", "litres", "tsp", "tbsp", "cup", "cups",
    "pt", "qt", "gal", "gallon", "gallons", "floz", "mph", "kph", "knot",
    "celsius", "fahrenheit", "kelvin", "c", "f", "k", "mb", "gb", "tb", "kb",
}




_TIME_AT_RE = re.compile(
    r'\b(?:at|for)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b|\b(?:at|for)\s+(noon|midnight)\b', re.I)








_LOCATION_STOPWORDS = {
    "weather", "forecast", "temperature", "temp", "climate", "the", "whats", "what",
    "is", "in", "for", "at", "near", "today", "tomorrow", "current", "currently",
    "right", "now", "like", "hows", "how", "s", "a", "me", "show", "get",
}


# ── Persistent user profile: capture "I'm from Seattle" / "my name is Alex" ──
_NAME_CAP_RE = re.compile(r"\bmy name(?:'?s| is)\s+([A-Za-z][\w .'-]{1,38})", re.I)
_CALLME_RE = re.compile(r"\bcall me\s+([A-Za-z][\w .'-]{1,38})", re.I)
_FROM_CAP_RE = re.compile(
    r"\bi(?:'?m| am)?\s+(?:from|based in|live in|living in|located in)\s+"
    r"([A-Za-z][\w .,'-]{1,38})", re.I)
_LIKE_CAP_RE = re.compile(
    r"\bremember (?:that )?i (?:really )?(?:like|love|enjoy|prefer|am a fan of)\s+"
    r"([\w][\w .,'&-]{1,48})", re.I)










# Strip stray inline citation markers ("[0]", "[1, 2]", "[0, 2, 3]") that a
# summariser sometimes leaves in the answer prose despite being told to list source
# indices separately. Only bracketed runs of digits/commas/spaces are removed, so
# real markdown like "[label](url)" and "[1] Do the thing" checklists survive.
_CITATION_RE = re.compile(r'\s*\[\s*\d+(?:\s*,\s*\d+)*\s*\]')




# Preposition/filler words to strip when pulling a destination out of a trip ask.
_TRIP_STOPWORDS = {
    "plan", "planning", "trip", "vacation", "holiday", "getaway", "itinerary",
    "travel", "traveling", "travelling", "visit", "visiting", "tour", "journey",
    "me", "my", "a", "an", "the", "to", "for", "in", "of", "please", "help",
    "week", "weekend", "day", "days", "long", "some", "go", "going",
}




# Words extract_topic keeps but that shouldn't survive into a news/market topic —
# "news about AI" → topic "ai", not a doubled "News: News Ai" title.
_NEWSY = {"news", "headline", "headlines", "latest", "recent", "breaking",
          "update", "updates", "today", "todays", "day", "days", "daily",
          "story", "stories", "about", "morning", "evening", "tonight", "top", "feed"}
















# ══════════════════════════════════════════════════════════════════════════════
# CRYPTO / ON-CHAIN BUILDERS
# The stock builders above are the template: resolve an identity, pull live data
# from keyless sources, degrade honestly. Four widgets:
#   crypto_card   — a token's price + chart + key stats + contract addresses
#   crypto_report — a written brief synthesizing price, distribution and news
#   wallet_graph  — THE feature: top-holder connection graph + concentration read
#   wallet_card   — inspect a single address's native + token holdings
# ══════════════════════════════════════════════════════════════════════════════

# CoinGecko range tab -> its `days` param. Mirrors the stock card's range tabs.
_CRYPTO_RANGES = {"1d": "1", "7d": "7", "30d": "30", "90d": "90",
                  "1y": "365", "max": "max"}




















# Map the LLM-chosen answer `format` to a header icon. The model picks the format;
# this is just the visual affordance for it. Unknown formats fall back to "article".
_ANSWER_ICONS = {
    "recipe": "restaurant", "howto": "checklist", "how-to": "checklist",
    "steps": "checklist", "definition": "menu_book", "fact": "lightbulb",
    "comparison": "compare_arrows", "list": "format_list_bulleted",
    "article": "article", "explainer": "article", "answer": "lightbulb",
}




async def _resolve_news_topic_config(config: dict) -> dict:
    """Fill a data_card the model tagged with `news_topic` (or `topic`).

    Normally that means it researched via html_notes_news, and the stories it
    already fetched are cached under "news:<topic>" — the server supplies them
    with their photos so the model never re-types them.

    But the model does not always mean it. Observed live on "best espresso
    machines under $500": it researched with html_notes_web_search and then
    labelled the config `news_topic` anyway. (The routing prompt used to promise
    photos ONLY on the news branch, so a model that wanted a picture-bearing
    card was steered there — that wording is fixed now, but a prompt is a
    suggestion and this needs to hold regardless.) There was no "news:" cache,
    so this silently no-opped and the card arrived sourceless.

    So: believe the tool the model RAN, not the key it typed. Which cache is
    populated is a fact about what actually happened; the key name is only what
    the model meant. When only the web_search cache exists, build the research
    card from it.

    Returns the config to use — unchanged when neither cache is available, so
    the downstream quality floor still gets its turn.
    """
    topic = str(config.get("news_topic", config.get("topic", ""))).strip()
    model_keys = {k: v for k, v in config.items()
                  if v and k not in ("news_topic", "topic")}

    cached = get_cached_tool_result(f"news:{topic}")
    if cached:
        logger.info(f"[WIDGET INJECTOR] Rehydrated news data_card for {topic!r}")
        return {**cached, **model_keys}

    searched = get_cached_tool_result(f"search:{topic}")
    if searched:
        logger.info(f"[WIDGET INJECTOR] news_topic {topic!r} has no news cache but a "
                    f"web_search one — treating as research")
        answer_cfg = await build_answer_config(topic, results=searched)
        return {**answer_cfg, **model_keys}

    # Neither cache hit. This branch used to give up here — making news_topic the
    # only injector key with no builder fallback, while its siblings
    # (stock_news_query -> build_stock_news_config, search_query ->
    # build_answer_config) call their builder unconditionally. A one-character
    # drift between the topic passed to html_notes_news and the topic passed to
    # canvas_add_widget was enough to miss the cache, and the card then fell
    # through to the quality floor, which stapled on generic hits: the user got
    # unread sources with no photos in place of the curated photo stories the
    # prompt promised. Build them properly instead.
    if topic:
        try:
            news_cfg = await asyncio.wait_for(build_news_config(topic), timeout=20.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[WIDGET INJECTOR] news rebuild failed for {topic!r}: {e}")
            return config
        if news_cfg and news_cfg.get("items"):
            logger.info(f"[WIDGET INJECTOR] news cache miss for {topic!r} — rebuilt "
                        f"{len(news_cfg['items'])} stories")
            return {**news_cfg, **model_keys}

    return config








# Runtime secret cache (vault-backed). Populated lazily by _fetch_secret.
_secret_cache: Dict[str, str] = {}
# A secret that RESOLVED to a value is cached for the process lifetime. A secret
# that came back EMPTY is cached only briefly, so a key added to the vault after
# boot (the "I added the TOMTOM key but traffic still fails" case) gets picked up
# on the next request instead of needing a container restart.
_secret_miss_at: Dict[str, float] = {}
_SECRET_MISS_TTL = 60.0

# A map ask that wants BUSINESS/POI pins ("coffee shops in Seattle", "pharmacies
# near me") rather than a hazard/event map ("where are the fires"). These resolve
# via Google Places (real listings with coordinates) instead of the LLM-guess +
# city geocoder, which can't find individual shops.
POI_MAP_RE = re.compile(
    r'\b(\w*shops?|\w*stores?|restaurants?|cafe?s?|coffee|bars?|pubs?|breweries|brewery|'
    r'pharmac(y|ies)|grocer(y|ies)|supermarkets?|gyms?|hotels?|motels?|banks?|atms?|'
    r'gas ?stations?|petrol|parks?|museums?|librar(y|ies)|hospitals?|clinics?|'
    r'dentists?|doctors?|salons?|barbers?|bakeries|bakery|malls?|markets?|'
    r'dispensar(y|ies)|clubs?|theat(er|re)s?|cinemas?|diners?|delis?|'
    r'places to (eat|drink|stay|shop|visit|go)|things to do|points? of interest)\b'
    r'|\bnear ?(me|by)\b|\bnearby\b')

# Hazard/event geo queries stay on the web+geocode path — Places would just search
# for businesses literally named after the event.
_NON_POI_GEO_RE = re.compile(
    r'\b(fires?|wildfires?|earthquakes?|floods?|flooding|hurricanes?|tornado(es)?|'
    r'storms?|outages?|protests?|war|conflict|border)\b')

# Hunger/meal intent that names NO POI noun (so POI_MAP_RE misses it) and NO geo
# token (so MAP_ASK_RE misses it), yet clearly wants nearby food places on a map:
# "where can I get food", "somewhere to eat", "grab lunch", "food bank near me".
# These used to fall through to the slow agent path, which web-searched "food
# assistance", couldn't geocode the national directories it found, and rendered a
# blank whole-US map. Routed to the Google Places pin path instead (see build_map_config).
EAT_MAP_RE = re.compile(
    r'\bwhere (can|could|should|do|to)\b[^?.!]*\b(eat|food|meal|lunch|dinner|breakfast|brunch|coffee|drinks?|takeout|bite|groceries)\b'
    r'|\bwhere to eat\b'
    r'|\b(somewhere|someplace|any\s?where|a place|any place|good places?|best places?|spots?) to (eat|drink|dine|grab)\b'
    r'|\b(get|grab|order|buy|want|need|find) (me )?(some ?)?(food|lunch|dinner|breakfast|brunch|coffee|takeout|a bite|groceries|a meal)\b'
    r'|\bfood ?(banks?|pantr(?:y|ies)|trucks?)\b'
    r'|\bsoup ?kitchens?\b'
    r'|\b(i\'?m |im |feeling |really )?hungry\b')

# "near me"/"nearby"/"close by" — a POI ask with no explicit anchor city.
_NEAR_ME_RE = re.compile(
    r'\bnear ?(me|by)\b|\bnearby\b|\baround (me|here)\b|\bclose by\b', re.I)
# A preposition followed by a real place token ("in Seattle", "near Chicago") —
# used to tell "coffee in Seattle" (already anchored) from "where can I get food"
# (needs the user's city appended). The negative lookahead skips filler followers
# ("in the airport", "near me") that aren't a place name.
_EXPLICIT_PLACE_RE = re.compile(
    r'\b(in|near|around|at|by|within|close to)\s+'
    r'(?!(?:me|by|here|my|the|a|an|you|us|home)\b)[a-z0-9]', re.I)








# "from A to B" (a route) vs a single place. The route form gets Google's
# traffic-aware directions; a single place gets a map of that area.
_DIR_FROM_TO_RE = re.compile(r'\bfrom\s+(.+?)\s+to\s+(.+?)(?:[.?!]|$)', re.I)
# Strip the framing words so what's left is the place. Keeps "in/near/at" OUT
# (they're stripped) so "traffic in Seattle" → "Seattle".
_DIR_STRIP_RE = re.compile(
    r'\b(directions?|routes?|traffic|navigate|navigation|drive|driving|way|ways|'
    r'hows?|long|far|travel|time|commute|to|from|in|on|for|around|at|near|by|'
    r'get|getting|the|my|our|is|it|whats|there|show|me|tell|give|please|a|an|s|'
    r'what\'?s?)\b', re.I)
# A directions ask that specifically wants the MAP (traffic/route/navigation),
# not just the travel-time number — the latter stays on the answer card.
TRAFFIC_MAP_RE = re.compile(
    r'\b(traffic|directions?|routes?|navigate|navigation)\b|\bfrom\s+.+\s+to\s+', re.I)


# Words that REFER to the user's location instead of naming a place. They must
# never reach a geocoder: every one of them is also a real, geocodable place name
# somewhere in the world, so the lookup "succeeds" and pins the map thousands of
# miles away with total confidence. Measured against the live geocoders:
#   "Current"  -> 25.408, -76.784   (a settlement on Eleuthera, Bahamas)
#   "here"     ->  8.660,  45.854   (Somalia)
#   "my location" -> -1.989, 30.086 (Rwanda)
# This is how "how is the traffic" produced a confident traffic map of the
# Bahamas: the LLM router fills `query` with a placeholder like "Current" when
# the user named no place, and we geocoded it literally.
_DEICTIC_PLACE_RE = re.compile(
    # NOTE: callers usually pass text that _DIR_STRIP_RE has already run over,
    # which removes leading "my"/"in"/"the" — so "traffic in my area" arrives as
    # the bare word "area". Both the full phrases and the bare heads must match.
    r"^\s*(?:the\s+)?(?:my\s+|our\s+)?"
    r"(?:current(?:\s+(?:location|position|area|place|spot|city))?"
    r"|here|nearby|near\s*(?:me|by|here)|around\s*(?:me|here)"
    r"|local(?:ly)?|my\s+(?:area|place|city|town|position|spot)"
    r"|location|position|area|vicinity|surroundings"
    r"|this\s+(?:area|place|city|town)|where\s+i\s+am|home)\s*$",
    re.I,
)










# Google Places primaryType → a category emoji for the map pin. Longest/most
# specific handled first via substring match in _emoji_for_place_type. Keeps the
# map readable at a glance instead of a field of identical dots.
_PLACE_TYPE_EMOJI = {
    "coffee": "☕", "cafe": "☕", "bakery": "🥐", "bar": "🍸", "pub": "🍺",
    "night_club": "🎶", "restaurant": "🍽️", "meal_takeaway": "🥡", "meal_delivery": "🥡",
    "food": "🍴", "supermarket": "🛒", "grocery": "🛒", "convenience": "🏪",
    "lodging": "🏨", "hotel": "🏨", "campground": "⛺",
    "tourist_attraction": "📸", "museum": "🏛️", "art_gallery": "🖼️",
    "park": "🌳", "zoo": "🦁", "aquarium": "🐠", "amusement": "🎢", "stadium": "🏟️",
    "beach": "🏖️", "church": "⛪", "hindu_temple": "🛕", "mosque": "🕌", "synagogue": "🕍",
    "place_of_worship": "⛩️", "school": "🏫", "university": "🎓", "library": "📚",
    "hospital": "🏥", "pharmacy": "💊", "doctor": "🩺", "dentist": "🦷",
    "gym": "🏋️", "spa": "💆", "beauty_salon": "💇", "hair_care": "💈",
    "shoe_store": "👟", "clothing_store": "👕", "jewelry_store": "💍", "book_store": "📖",
    "electronics_store": "🔌", "hardware_store": "🔧", "furniture_store": "🛋️",
    "shopping_mall": "🛍️", "store": "🛍️", "gas_station": "⛽", "car_repair": "🔧",
    "parking": "🅿️", "bank": "🏦", "atm": "🏧", "airport": "✈️", "train_station": "🚉",
    "bus_station": "🚌", "subway_station": "🚇", "movie_theater": "🎬", "casino": "🎰",
    "police": "🚓", "fire_station": "🚒", "post_office": "📮", "veterinary": "🐾",
}








def is_valid_tool_args(tool_name: str, args: dict) -> bool:
    if not args:
        return False
    if tool_name == "mcp__lazy-tool-service__canvas_add_widget":
        return bool(args.get("widget_type"))
    if tool_name == "mcp__lazy-tool-service__canvas_modify_dom":
        return bool(args.get("css_selector") and args.get("action"))
    if tool_name == "mcp__lazy-tool-service__create_widget":
        return bool(args.get("widgetType") and args.get("htmlContent"))
    if tool_name == "mcp__lazy-tool-service__update_widget":
        return bool(args.get("widgetId"))
    return False


# ── AGENTIC ROUTER ───────────────────────────────────────────────────────────
# The regex cascade in the handler is the fast lane: unambiguous, edge-case-
# hardened asks (timers, weather, video, the traffic route logic, list edits,
# media swaps) resolve there with zero LLM latency. Everything it doesn't catch
# used to drop into the full agentic loop, whose failure mode is a 30-60s
# reasoning spin that often lands a wall-of-links data_card.
#
# The router replaces that fall-through for the common case: ONE ~200-token
# classify pass picks the right server-side builder — the SAME builders the fast
# lane uses — or several of them for a composite ask ("plan my Saturday in
# Seattle" → weather + map + things-to-do). We then build and spawn directly, no
# agent loop. The full agent still owns what needs real tools (removals, DOM
# edits, note CRUD, custom hand-built widgets); the router returns
# {"defer": true} for those and for anything it isn't confident about, so nothing
# it can't do well is forced through it.

# APP HUB fast lane: the user asking for THEIR launcher/services grid. Kept
# deliberately possessive/deictic ("my apps", "app hub") so "best apps for
# photo editing" or "what services does AWS offer" never trips it — those are
# research asks. One-app opens ("open the trading client") go to the agent,
# which can disambiguate via html_notes_open_app.
# "dashboard" is deliberately ABSENT: the html-notes canvas itself is "the
# dashboard" ("modify my dashboard tasks"), so that word means the canvas,
# never the hub. Same for a bare "launcher" ("best launcher for android").
APP_HUB_INTENT_RE = re.compile(
    r'\b(app hub|apphub|'
    r'(?:my|our) (?:apps|services|containers|launcher|projects)|'
    r'(?:show|open|list|pull up|bring up) (?:me )?(?:the )?'
    r'(?:hub|launcher|app grid|apps? (?:hub|grid|launcher))|'
    # Discovery questions — the user who doesn't remember what they have.
    # "what projects do i have" fell through to a ~30s agent turn (measured
    # 2026-08-26) for want of the word "projects" here.
    r'what (?:apps?|projects?|services?|containers?) '
    r'(?:can (?:i|we) open|do (?:i|we) have|are (?:there|available))|'
    r'what can (?:i|we) open|'
    r'what(?:\'s| is) (?:currently )?running)\b', re.IGNORECASE)

# OPEN-APP fast lane: "open the trading client" used to cost a full agent
# turn (~15-40s of LLM) to fire a ~15ms tool. When a short open-imperative
# names EXACTLY one catalog app, the handler emits open_url directly with no
# LLM at all; anything ambiguous or widget-flavoured falls through unchanged.
OPEN_APP_VERB_RE = re.compile(
    r'^(?:open|launch|start|pull up|bring up)\s+(?:the\s+|my\s+|our\s+|container\s+)?(.+)$',
    re.IGNORECASE)

# Trailing markers that say "the APP, not a widget". Their presence overrides
# the widget-noun guard below ("open the music player app" → the app in a
# tab; "open the music player" → the mini player widget, as always).
_OPEN_APP_MARKER_RE = re.compile(
    r'\s+(?:app|application|website|site|page|container|(?:in\s+a\s+)?new\s+tab|tab|window)s?\s*[.!]?$',
    re.IGNORECASE)

# Words that mean a WIDGET ask in this app. A name containing one never
# fast-opens without an explicit marker — "open my notes" is the notepad
# widget even though the html-notes app's NAME contains "notes".
_OPEN_APP_WIDGET_NOUNS = frozenset({
    "music", "song", "radio", "playlist", "video", "clip", "stream",
    "note", "notes", "notepad", "list", "checklist", "todo",
    "clock", "timer", "stopwatch", "countdown", "reminder", "alarm",
    "map", "directions", "traffic", "weather", "forecast",
    "image", "picture", "photo", "chart", "graph", "table",
    "settings", "theme", "converter", "calculator",
    "scoreboard", "score", "scores", "news", "headlines",
    "wikipedia", "wiki", "widget", "card", "canvas", "dashboard",
})


def extract_open_app_target(text: str) -> Optional[tuple]:
    """`(name, explicit_app_intent, has_widget_noun)` when `text` is a short
    open-imperative, else None. Pure text analysis — the caller resolves
    `name` against the live catalog; this only decides the SHAPE is an
    app-open ask and reports the two signals the caller tiers on.

    It deliberately does NOT reject widget-noun names any more. The first
    version returned None for anything containing 'music'/'notes'/…, which
    meant "open the music player" — an EXACT app name — never reached the
    catalog and spawned the mini-player widget instead. Precedence now lives
    with the caller: an exact app/alias name always wins; a widget noun only
    blocks the fuzzy partial tier."""
    text = (text or "").strip()
    if len(text) > 70:          # long sentences are never a bare open-command
        return None
    m = OPEN_APP_VERB_RE.match(text)
    if not m:
        return None
    name = m.group(1).strip()
    explicit = False
    while True:                 # "music player app in a new tab" → strip both
        stripped = _OPEN_APP_MARKER_RE.sub("", name)
        if stripped == name:
            break
        name, explicit = stripped.strip(), True
    if not name:
        return None
    words = set(re.findall(r"[a-z0-9]+", name.lower()))
    return name, explicit, bool(words & _OPEN_APP_WIDGET_NOUNS)


# A BARE app name typed on its own — "music player", "trading bot". No verb,
# so extract_open_app_target never sees it, and the widget lanes downstream
# claimed it: "music player" spawned the mini player and "trading bot" spent
# 17s in the agent. Everything here is a shape guard; the caller still
# requires a WHOLE-NAME (exact_only) catalog match, so a phrase only ever
# opens a tab when the user typed an app's actual name or alias.
_BARE_NAME_STOP_RE = re.compile(
    r'\?|\b(play|show|add|make|create|remove|delete|close|find|search|'
    r'what|who|when|where|why|how|is|are|was|does|did|can|should|tell|'
    r'give|get|set|update|change|help|about|vs|versus)\b', re.IGNORECASE)


def extract_bare_app_name(text: str) -> Optional[str]:
    """The candidate app name in a bare, verb-less message, else None.

    Deliberately permissive about WHICH name (the catalog decides that) and
    strict about SHAPE: short, no question mark, no verb or interrogative.
    'music player' passes; 'play some lofi', 'is trading down?' and
    'what is the trading client' do not."""
    t = (text or "").strip().rstrip(".!")
    if not t or len(t) > 40:
        return None
    if _BARE_NAME_STOP_RE.search(t):
        return None
    t = re.sub(r'^(?:my|the|our|container)\s+', '', t, flags=re.IGNORECASE).strip()
    # Needs a letter, and at most four words — an app name, not a sentence.
    if not re.search(r'[a-z]', t, re.IGNORECASE) or len(t.split()) > 4:
        return None
    return t


# type → (id_prefix, one-line spec for the classify prompt). The prompt text is
# what the model sees; the id_prefix is the widget id stem we spawn with.
ROUTER_WIDGETS = {
    "weather":    ("weather",   'current conditions / forecast. query = the place ("Tokyo")'),
    "news":       ("news",      'general current headlines. query = the topic (empty for top stories)'),
    "stock_news": ("stock-news", 'stock / market / company NEWS. query = the ticker or company (e.g. "TSLA", "Apple"), or the market itself for a broad market-news ask. ONLY for a genuine finance/markets ask.'),
    "stock":      ("stock",     'a ticker\'s price + chart + technicals. query = company or symbol ("Apple", "TSLA")'),
    "stock_trending": ("stock-trending", 'the top TRENDING / most-active / biggest-gainer or -loser stocks — a DISCOVERY ask naming NO specific company ("top trending stocks this month", "biggest gainers today", "compare the top 5 hot stocks"). Renders one multi-series comparison chart from live market feeds. query = the whole request'),
    "stock_report": ("stock-report", 'a COMPREHENSIVE research report on ONE stock — synthesizes price, fundamentals, technicals, recent news AND analyst commentary into a written brief. Use for "full report on X", "deep dive / due diligence on X", "analyze X stock". query = company or symbol'),
    "crypto":     ("crypto",    'a CRYPTOCURRENCY / token\'s price + chart + stats + contract address. query = the coin name, symbol or contract address ("Bitcoin", "PEPE", "$SOL", "0x6982…"). Use for any "price of X coin/token", "X crypto chart" ask. NOT for stocks.'),
    "crypto_report": ("crypto-report", 'a COMPREHENSIVE written report on ONE crypto token — price, market context, ON-CHAIN holder distribution (whale concentration / rug risk) and news. Use for "full report / deep dive / due diligence / is X a scam / analyze X token". query = coin name, symbol or address'),
    "wallet_graph": ("wallet-graph", 'a HOLDER-NETWORK graph for a token: draws the top wallets holding it as nodes (sized by %) with transfer edges between them, and reports concentration — whether a few whales control supply (pump-and-dump / rug risk) or it is fairly distributed. Use for "who holds X", "X token holders / whales / distribution", "is X a fair launch", "wallet graph for X", "show me the whales in X". query = coin name, symbol or contract address'),
    "wallet":     ("wallet",    'inspect ONE wallet ADDRESS — its holdings, portfolio value and token list. Use when the message contains an on-chain address (0x… or a Solana base58 address) and asks what it holds / its balance. query = the whole message (the address is extracted from it)'),
    "sports":     ("scores",    'scores / fixtures / standings. query = the league or team ("nba", "arsenal")'),
    "map":        ("map",       'where something IS / a map of places. query = the subject ("fires in California")'),
    "traffic":    ("traffic",   'live traffic or directions. query = the place, or "from A to B"'),
    "video":      ("video",     'something to WATCH. query = the subject ("cookie recipe")'),
    "image":      ("image",     'a PICTURE of something. query = the subject ("golden retriever puppy")'),
    "music":      ("music",     'background music / radio. query = the genre OR artist/band name. Add modifiers {"kind": "genre"} for a style/mood ("jungle", "smooth jazz", "study") or {"kind": "artist"} for a named act ("Oasis", "the band Jungle"); omit kind if unsure'),
    "answer":     ("answer",    'a fact / recipe / how-to / definition / comparison / explanation — INCLUDING any question that contains numbers or units but wants a JUDGEMENT ("how long to get a 145F chicken to 165", "is 10 more minutes enough", "what temp is safe"). query = the question'),
    "products":   ("products",  'shopping / product recommendations to BUY or compare ("good outdoor shoes", "best budget laptop", "gift for a hiker"). query = the product ask. Renders a grid of picture cards linking to sources'),
    "trip":       ("trip",      'plan a TRIP / vacation / multi-day itinerary to a place ("plan a trip to Japan", "3 days in Rome"). query = the destination + any duration. Renders an itinerary card + a map of the spots'),
    "wikipedia":  ("wikipedia", 'an explicit Wikipedia-article request. query = the subject'),
    "app_grid":   ("app-hub",   'the user\'s APP HUB / launcher — their own running services and apps ("show my apps", "open my launcher", "what services are running", "my containers"). NOT a request to open one specific app. query = the whole request'),
    "list":       ("checklist", 'a checklist / to-do / shopping list to CREATE. query = the whole request'),
    "notes":      ("notes",     'a notepad, optionally pre-filled. query = the whole request'),
    "clock":      ("clock",     'a clock / world clock / timer / countdown / stopwatch. query = the whole request'),
    "converter":  ("converter", 'ONLY a bare calculation or unit/currency conversion — the ask IS the arithmetic ("40% of 1250", "what is 15*23", "5 miles in km", "20 usd to eur", "convert 10 kg to lb"). NEVER a question that merely contains numbers, temperatures or units ("how long until my 145F chicken hits 165", "is 10 more minutes enough", "how long to drive to SF") — those are "answer". query = the whole request'),
    "reminder":   ("reminder",  'a reminder / alarm at a time ("remind me in 20 minutes", "remind me at 3pm to call mom"). query = the whole request'),
}

# In PRISM MODE (use_lazy_agent=False), these router widget types are RESEARCH asks
# and get handed to the prism agent (which runs the lazy-tool-service MCP
# web_search/read_page harnesses → a synthesised, sourced answer) instead of a local
# search-scrape builder. Everything else (weather/stock/sports/map/traffic/music/
# clock/list/notes/trip) stays on the fast local path — deterministic, fast, and
# already high quality, so a 30-60s research loop would only make it worse.
# NOTE: "products" is deliberately NOT here. With it in this set, prism mode
# (the default) deferred every shopping ask to the agent, whose prompt renders a
# data_card — so render_products, its reuse plumbing and class map serviced a
# type nothing could emit, and the same ask produced a photo grid in lazy-agent
# mode but a text card in prism mode. The router's local products builder is
# deterministic and already high quality; let it run in both modes.
_AGENT_RESEARCH_TYPES = {"answer", "image", "wikipedia",
                         # News is research, not a lookup: the value is in
                         # corroborating across outlets and saying what they
                         # DISAGREE on, which only a read-then-synthesise pass
                         # can do. The local builder summarised each story in
                         # isolation, so the card was N disconnected blurbs with
                         # no through-line and no cross-checking.
                         "news", "stock_news"}

# The tool a research verdict actually resolves to, and the call that finishes
# it. Every string here is copied from the ROUTING section of SYSTEM_PROMPT, so
# the pre-flight block can never name a tool or a config key the prompt does not
# already teach — and every tool name is pinned by a test against the
# enabled_tools list. A hint naming a tool the agent does not have is worse than
# no hint: it burns iterations failing against a phantom (the same failure the
# enabled_tools comment describes for the tools-api data tools).
_PREFLIGHT_RECIPE = {
    "answer":     ("mcp__lazy-tool-service__html_notes_web_search",
                   "canvas_add_widget(widget_type='data_card', config={'search_query': <query>})"),
    "wikipedia":  ("mcp__lazy-tool-service__html_notes_web_search",
                   "canvas_add_widget(widget_type='data_card', config={'search_query': <query>})"),
    "news":       ("mcp__lazy-tool-service__html_notes_news",
                   "canvas_add_widget(widget_type='data_card', config={'news_topic': <query>})"),
    "stock_news": ("mcp__lazy-tool-service__html_notes_stock_news",
                   "canvas_add_widget(widget_type='data_card', config={'stock_news_query': <query>})"),
    # No tool: the agent has no image tool at all — the server searches and
    # vision-checks from image_query.
    "image":      ("",
                   "canvas_add_widget(widget_type='image', config={'image_query': <query>})"),
}

_PREFLIGHT_WANTS = {"answer", "watch", "listen", "see", "track", "compute",
                    "remember", "ui-control"}


def _clean_preflight_checks(raw) -> dict:
    """Validate the classifier's pre-flight answers. Best-effort by design: a
    missing, malformed or half-filled `checks` yields {} (or drops just the bad
    fields), so a model hiccup degrades to today's behaviour rather than putting
    a garbage assertion in front of the agent."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in ("is_arithmetic", "needs_fresh_data"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    wants = str(raw.get("wants", "") or "").strip().lower()
    if wants in _PREFLIGHT_WANTS:
        out["wants"] = wants
    subject = str(raw.get("subject", "") or "").strip()
    if subject:
        out["subject"] = subject[:160]
    unknowns = raw.get("unknowns")
    if isinstance(unknowns, list):
        clean = [str(u).strip()[:80] for u in unknowns[:3] if str(u).strip()]
        if clean:
            out["unknowns"] = clean
    return out


def _preflight_block(specs: list, checks: dict) -> str:
    """Tier 2's read of the ask, rendered as a prompt block for tier 3.

    The classifier already read the message AND the canvas and produced both a
    widget plan and the basic self-check answers. On the research-deferral path
    all of that was logged and thrown away, so the agent re-derived intent from
    raw text with no hint — the same caller-knows-the-answer-and-discards-it
    seam that made the traffic builder re-grep for a keyword the router had
    already consumed (fixed there with an explicit force_traffic flag).

    Deliberately a PRIOR, not a mandate. The classifier that gets this right
    most of the time is also what read a cooking-time question as a unit
    conversion on 2026-07-31; a mandate would make a wrong classification
    unrecoverable, and the agent still needs freedom to pick tools. So: state
    the checks, name the type, the cleaned query and the documented recipe, then
    explicitly yield to the user's own words and to the FOLLOW-UP directive that
    comes after this block.

    Returns "" for an empty plan so the caller can concatenate unconditionally —
    on defer/None the prompt stays byte-identical to before this existed.
    """
    if not specs:
        return ""
    lines = []
    if checks.get("is_arithmetic") is not None:
        lines.append("- Is this ask itself a calculation a converter can finish? "
                     + ("YES" if checks["is_arithmetic"] else "NO"))
    if checks.get("needs_fresh_data") is not None:
        lines.append("- Does answering need data you must look up? "
                     + ("YES — call a tool before you render" if checks["needs_fresh_data"]
                        else "NO — it is already in this conversation"))
    if checks.get("wants"):
        lines.append(f"- What the user wants out of this: {checks['wants']}")
    if checks.get("subject"):
        lines.append(f"- Subject: \"{checks['subject']}\"")
    if checks.get("unknowns"):
        lines.append("- Still unknown: " + "; ".join(checks["unknowns"]))

    plan = []
    for s in specs[:4]:
        wtype = str(s.get("type", ""))
        query = str(s.get("query") or "").strip()
        tool, render = _PREFLIGHT_RECIPE.get(wtype, ("", ""))
        step = (f"call {tool}, then {render}" if tool
                else render or f"use the {wtype} route in ROUTING above")
        tgt = s.get("target")
        plan.append(f'- {wtype} — query: "{query}" → {step}'
                    + (f", writing into the EXISTING widget #{tgt}" if tgt else ""))

    return (
        "\n\nBEFORE YOU PICK A TOOL\n"
        "A fast classifier read this ask and the canvas above before you did.\n"
        + ("\n".join(lines) + "\n" if lines else "")
        + "It concluded:\n" + "\n".join(plan) + "\n"
        "Start there and reuse that query verbatim — it is the cleaned subject, "
        "already stripped of filler. This is a strong prior, NOT an order: if it "
        "plainly contradicts what the user actually wrote, ignore it and use the "
        "tool that fits. Do not re-plan past it and do not add widgets it did not "
        "name unless the ask clearly needs them. If a directive below names a "
        "widget id, that id wins over anything here.\n"
    )




# Modalities the composition planner is allowed to combine (a subset of
# ROUTER_WIDGETS — the ones that make sense as parts of one rich answer).
_COMPOSE_MODALITIES = ("answer", "image", "video", "news", "map", "stock", "weather")



























# ── Obsidian vault: notes saved from the canvas become .md files ─────────────








class SaveNoteRequest(BaseModel):
    title: str = "Untitled"
    content: str = ""
    tags: List[str] = []
    slug: str = ""      # set when re-saving an existing note (keeps the same file)











@app.get("/graph")
async def get_graph():
    """
    Returns nodes and edges formatted for visual graph rendering.
    """
    try:
        notes = database.list_all_notes()
        nodes = []
        edges = []
        
        for n in notes:
            nodes.append({
                "data": {
                    "id": n["id"],
                    "label": n["title"],
                    "version": n["version"]
                }
            })
            for link_target in n.get("links", []):
                edges.append({
                    "data": {
                        "id": f"edge_{n['id']}_{link_target}",
                        "source": n["id"],
                        "target": link_target
                    }
                })
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




async def _mcp_scopes_serving_us(client: httpx.AsyncClient) -> list:
    """Scopes where lazy-tool-service IS connected, for diagnosing a mismatch.

    Prism scopes /mcp-servers by the x-project/x-username headers, so there is
    no "list every scope" call — we probe the scopes we know this ecosystem
    registers under. Best-effort and purely diagnostic.
    """
    candidates = [(AGENT_PROJECT, "lazycat"), (AGENT_PROJECT, "admin"),
                  ("coding", "admin"), ("vllm-trading-bot", "lazy-trader")]
    found = []
    for project, username in candidates:
        if (project, username) == (AGENT_PROJECT, AGENT_USERNAME):
            continue
        try:
            rows = (await client.get(
                f"{PRISM_URL}/mcp-servers",
                headers={"x-project": project, "x-username": username})).json()
            rows = rows if isinstance(rows, list) else rows.get("servers", [])
            if any(r.get("name") in MCP_SERVER_NAMES and r.get("connected")
                   for r in rows):
                found.append(f"{project}/{username}")
        except Exception:
            continue
    return found


async def _agent_dependency_status() -> dict:
    """Can a research ask actually work right now?

    Every tier-3 ask runs on Prism against the CUSTOM_HTML_NOTES_CANVAS persona,
    using tools served over MCP by lazy-tool-service. None of that lives in this
    repo, and when it breaks this app keeps answering /health/app with "ok"
    while every research ask dies on `Unknown tool error`. So check the thing
    that actually has to be true:

      1. Prism is reachable at all,
      2. the lazy-tool-service MCP server is CONNECTED for our scope and serving
         a non-zero tool count (a connected server with 0 tools is the shape a
         half-dead SSE link takes),
      3. our persona exists — without it the run is unscoped, which is not an
         outage but is a large silent quality regression (~79 tools, the agent
         wanders into execute_python).

    Never raises: a health probe that throws is worse than one that reports.
    """
    detail = {"prism": PRISM_URL, "project": AGENT_PROJECT,
              "agent_id": PRISM_AGENT_ID}
    headers = {"x-project": AGENT_PROJECT, "x-username": AGENT_USERNAME}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            servers = (await client.get(f"{PRISM_URL}/mcp-servers",
                                        headers=headers)).json()
            servers = servers if isinstance(servers, list) else servers.get("servers", [])
            mine = next((s for s in servers if s.get("name") in MCP_SERVER_NAMES), None)
            if not mine:
                # Not in OUR scope — but Prism serves MCP tools globally once a
                # server is connected under any scope, so this is a registration
                # mismatch rather than an outage, and research may well still
                # work. Report it as degraded and name the scopes that do have
                # it, because the alternative (calling it down) cries wolf while
                # the real symptom is only that nothing shows under our project.
                elsewhere = await _mcp_scopes_serving_us(client)
                if elsewhere:
                    return {**detail, "ok": True, "degraded": True,
                            "error": f"none of {MCP_SERVER_NAMES} is registered for "
                                     f"{AGENT_PROJECT}/{AGENT_USERNAME}; serving from "
                                     f"{', '.join(elsewhere)} instead (tools resolve, "
                                     f"but our own scope shows none)"}
                return {**detail, "ok": False,
                        "error": f"none of {MCP_SERVER_NAMES} is registered for this scope"}
            tools = int(mine.get("toolCount") or 0)
            detail |= {"mcp_connected": bool(mine.get("connected")), "tool_count": tools}
            if not mine.get("connected") or tools <= 0:
                return {**detail, "ok": False,
                        "error": "MCP server registered but not serving tools"}

            agents = (await client.get(f"{PRISM_URL}/custom-agents",
                                       headers=headers)).json()
            agents = agents if isinstance(agents, list) else agents.get("agents", [])
            persona = next((a for a in agents
                            if a.get("agentId") == PRISM_AGENT_ID), None)
            detail["persona_tools"] = len(persona.get("availableTools") or []) if persona else 0
            if not persona:
                # Degraded, not down: research still runs, just unscoped.
                return {**detail, "ok": True, "degraded": True,
                        "error": f"persona {PRISM_AGENT_ID} is missing — runs will be unscoped"}
            return {**detail, "ok": True}
    except Exception as e:
        return {**detail, "ok": False, "error": f"prism unreachable: {e}"}





class InternalToolRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = {}

# The complete dispatch set of internal_tool_execute. Membership is checked
# BEFORE dispatch so a compromised or misconfigured caller can only name these
# tools — anything else is rejected without touching the elif chain.
_INTERNAL_EXECUTE_TOOLS = frozenset({
    "html_notes_create_note", "html_notes_update_note", "html_notes_get_note",
    "html_notes_search_notes", "html_notes_link_notes", "html_notes_modify_dom",
    "render_component", "canvas_read_dom", "canvas_modify_dom",
    "html_notes_add_youtube_widget", "create_widget", "update_widget",
    "html_notes_youtube_search", "html_notes_web_search", "html_notes_read_page",
    "html_notes_news", "html_notes_stock_history", "html_notes_stock_news",
    "html_notes_get_weather", "html_notes_sports_scores", "canvas_add_widget",
    "html_notes_list_services", "html_notes_open_app", "html_notes_curate_app",
    "html_notes_app_action", "html_notes_list_actions",
})
_internal_execute_auth_warned = False


CANVAS_BLOCK_RE = re.compile(r'<!--CANVAS_HTML_START-->(.*?)<!--CANVAS_HTML_END-->', re.DOTALL)


# The raw static/index.html hard-codes its asset refs as `index.js?v=3.0`,
# `js/widgets.js?v=1.4` etc. Only the `/` route (read_root) rewrites those to a
# per-deploy mtime fingerprint; a browser that loads `/static/index.html`
# directly (an old bookmark from when the UI was served from /static/) gets the
# frozen `?v=3.0` URLs, so it reuses its long-cached index.js FOREVER — even on
# refresh — and runs frontend code from before the reconcile/SSE/paint fixes.
# The visible symptom is "widgets don't update live, only after a refresh".
# Funnel every UI entry point through `/` so the fingerprinted assets are the
# only ones a browser ever sees. Registered BEFORE the /static mount so it wins.
@app.get("/static/index.html", include_in_schema=False)
@app.get("/static/", include_in_schema=False)
def _redirect_stale_ui_entrypoint():
    # 307 (not 301) + no-store so browsers never cache the redirect itself and a
    # stranded tab is rescued the next time it loads.
    return RedirectResponse(url="/", status_code=307,
                            headers={"Cache-Control": "no-store"})




# Transparent 1×1 PNG — served when a traffic tile can't be fetched, so the base
# map shows through cleanly instead of a broken-image frame.
_BLANK_PNG = _base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
# TomTom probe result is logged once per state-change so the reason traffic is/ isn't
# showing is visible in the container logs without spamming every tile.
_tomtom_last_status: Dict[str, int] = {}










# Mount UI static files at root
app.mount("/icons", StaticFiles(directory="app/static/icons"), name="icons")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_CACHE_BUSTED_ASSETS = ("index.js", "index.css", "js/widgets.js", "hud-theme.css")
_ASSET_QUERY_RE = re.compile(r'(index\.js|index\.css|js/widgets\.js|hud-theme\.css)\?v=[^"\']*')


def _asset_version() -> str:
    """Fingerprint the frontend assets by mtime.

    The ?v= tokens in index.html were hand-written and never got bumped, so a
    returning browser kept running whichever index.js it had cached from an
    earlier deploy — new server behaviour talking to old client code. Deriving
    the token from the files themselves means it can't drift again.
    """
    stamps = []
    for name in _CACHE_BUSTED_ASSETS:
        try:
            stamps.append(str(os.path.getmtime(os.path.join("app/static", name))))
        except OSError:
            pass
    return hashlib.sha1("|".join(stamps).encode()).hexdigest()[:10]


@app.get("/")
def read_root():
    html = pathlib.Path("app/static/index.html").read_text()
    html = _ASSET_QUERY_RE.sub(rf'\1?v={_asset_version()}', html)
    # index.html references its assets relatively ("index.js", "lib/…"), which
    # resolved fine when the page was served from /static/. Serving it at / makes
    # those resolve to /index.js and 404 — no JS, no app. <base> repoints every
    # relative URL back at /static/ without touching the markup. Absolute paths
    # (/session/message, /models) are unaffected.
    html = html.replace("<head>", '<head>\n    <base href="/static/">', 1)
    # The shell itself must never be cached, or the browser keeps serving an old
    # copy carrying the old ?v= tokens and the whole scheme is pointless.
    return Response(content=html, media_type="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})

from app.utils import *
from app.llm import *
from app.services.search import *
from app.services.finance import *
from app.services.portal import *
from app.services.app_actions import *
from app.services.location import *
from app.services.sports import *
from app.services.youtube_helpers import *
from app.config_builders import *
from app.canvas_manager import *

# Kept under the old name: it's what the registered tool schema calls.
stock_history = stock_snapshot
_load_blocklists()

__all__ = list(globals().keys())

from app.routes.message import router as message_router
app.include_router(message_router)
from app.routes.notes import router as notes_router
app.include_router(notes_router)
from app.routes.health import router as health_router
app.include_router(health_router)
from app.routes.api import router as api_router
app.include_router(api_router)
from app.routes.internal import router as internal_router, internal_tool_execute, InternalToolRequest
app.include_router(internal_router)
