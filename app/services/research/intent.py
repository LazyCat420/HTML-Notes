"""Deterministic Intent Extraction for the Research Protocol.

Transforms user query text into a strongly typed ResearchIntent prior to expensive
LLM reasoning, ensuring sub-10ms routing for known market & research question shapes.
"""
from __future__ import annotations

import re
from typing import Optional, List, Set, Tuple

from .models import ResearchIntent, Entity, TimeWindow, ResearchMode, EvidenceDepth


_GREETINGS_RE = re.compile(r"^(hello|hi|hey|greetings|good\s+(morning|afternoon|evening)|thanks|thank\s+you|ok|okay|cool|great|bye|goodbye)[!.?]*$", re.I)
_CANVAS_CONTROL_RE = re.compile(r"\b(close|remove|clear|dismiss|delete)\s+(all|everything|widgets?|canvas|cards?)\b", re.I)
_WEATHER_RE = re.compile(r"\bweather\s+(in|for|at)\b|\bforecast\b", re.I)
_SPORTS_SCORE_RE = re.compile(r"\b(score|scores|standings|schedule)\b", re.I)
_MEDIA_PURE_RE = re.compile(r"^(play|listen\s+to|watch)\s+", re.I)

# Ticker and entity identification patterns
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,6})\b")
_KNOWN_EQUITIES: dict[str, str] = {
    "nvda": "NVIDIA Corporation",
    "nvidia": "NVIDIA Corporation",
    "tsla": "Tesla, Inc.",
    "tesla": "Tesla, Inc.",
    "aapl": "Apple Inc.",
    "apple": "Apple Inc.",
    "msft": "Microsoft Corporation",
    "microsoft": "Microsoft Corporation",
    "googl": "Alphabet Inc.",
    "goog": "Alphabet Inc.",
    "google": "Alphabet Inc.",
    "alphabet": "Alphabet Inc.",
    "amzn": "Amazon.com, Inc.",
    "amazon": "Amazon.com, Inc.",
    "meta": "Meta Platforms, Inc.",
    "amd": "Advanced Micro Devices, Inc.",
    "intc": "Intel Corporation",
    "intel": "Intel Corporation",
    "nflx": "Netflix, Inc.",
    "netflix": "Netflix, Inc.",
    "spy": "SPDR S&P 500 ETF Trust",
    "qqq": "Invesco QQQ Trust",
    "sofi": "SoFi Technologies, Inc.",
    "pltr": "Palantir Technologies Inc.",
    "palantir": "Palantir Technologies Inc.",
    "smh": "VanEck Semiconductor ETF",
}

_KNOWN_CRYPTO: dict[str, str] = {
    "btc": "Bitcoin",
    "bitcoin": "Bitcoin",
    "eth": "Ethereum",
    "ethereum": "Ethereum",
    "sol": "Solana",
    "solana": "Solana",
    "pepe": "Pepe",
    "doge": "Dogecoin",
    "dogecoin": "Dogecoin",
}

_KNOWN_INDICES: dict[str, str] = {
    "s&p 500": "S&P 500",
    "sp500": "S&P 500",
    "nasdaq": "Nasdaq Composite",
    "dow": "Dow Jones Industrial Average",
    "dow jones": "Dow Jones Industrial Average",
    "russell 2000": "Russell 2000",
    "vix": "CBOE Volatility Index",
}

_EXPLAIN_MOVE_RE = re.compile(
    r"\b(why\s+(?:is|are|was|did)|what\s+happened\s+to|what\s+caused)\b.*"
    r"\b(down|up|fall|falling|fell|drop|dropping|dropped|plunge|plunging|plunged|"
    r"tank|tanking|tanked|selloff|sell-off|rally|rallying|rallied|surge|surging|surged|"
    r"dip|dipping|dipped|moving|sink|sinking)\b",
    re.I
)

_SHORT_MOVE_RE = re.compile(
    r"\b(down|up|drop|plunge|tank|rally|surge|dip)\s+today\b|"
    r"\bwhat\s+(?:happened|is\s+happening)\s+to\b|"
    r"\bwhy\s+(?:is|did)\s+[A-Za-z0-9$]+\s+(?:down|up|drop|fall|rally|surge|tank)\b",
    re.I
)

_EVENT_REPORT_RE = re.compile(
    r"\b(earnings|transcript|conference\s+call|guidance|10-?k|10-?q|8-?k|"
    r"sec\s+filing|filing|quarterly\s+results|cpi|fomc|fed\s+decision|fed\s+meeting|"
    r"rate\s+cut|rate\s+hike|m&a|acquisition|merger|takeover)\b",
    re.I
)

_COMPARISON_RE = re.compile(
    r"\b(compare|comparison|vs\.?|versus|better\s+buy|difference\s+between)\b",
    re.I
)

_DOSSIER_RE = re.compile(
    r"\b(bull\s*(?:and|/|vs)\s*bear|thesis|bear\s+case|bull\s+case|deep[\s-]?dive|"
    r"deep\s+research|full\s+(?:report|analysis|breakdown)|comprehensive\s+review)\b",
    re.I
)

_QUANT_SIGNAL_RE = re.compile(
    r"\b(quant\s+signal|backtest|build\s+a\s+signal|trading\s+strategy|systematic\s+strategy|"
    r"mean\s+reversion|momentum\s+signal|alpha\s+factor)\b",
    re.I
)

_MONITOR_RE = re.compile(
    r"\b(track\s+my\s+watchlist|monitor|watchlist\s+today|portfolio\s+tracker|alert\s+when)\b",
    re.I
)

_MARKET_BRIEF_RE = re.compile(
    r"\b(stock\s+market\s+news|market\s+news|stock\s+market\s+(?:today|for\s+the\s+day)|"
    r"how\s+is\s+the\s+(?:stock\s+)?market\s+doing|market\s+today|wall\s+street\s+today|market\s+overview|"
    r"whats\s+going\s+on\s+in\s+the\s+(?:stock\s+)?market|top\s+gainers|top\s+losers)\b",
    re.I
)

_OPINION_RE = re.compile(r"\b(what\s+do\s+you\s+think|should\s+i\s+(?:buy|sell|hold)|your\s+opinion|good\s+buy)\b", re.I)
_SOURCES_RE = re.compile(r"\b(sources?|citations?|references?|links?)\b", re.I)
_DEPTH_RE = re.compile(r"\b(deep|in-depth|thorough|comprehensive|detailed|full\s+report)\b", re.I)


def extract_entities(text: str) -> List[Entity]:
    """Extract recognized stock tickers, companies, cryptos, and indices."""
    found: dict[str, Entity] = {}

    # 1. Cashtags ($NVDA)
    for tag in _CASHTAG_RE.findall(text):
        sym = tag.upper()
        name = _KNOWN_EQUITIES.get(sym.lower(), f"{sym} Stock")
        found[sym] = Entity(symbol=sym, name=name, kind="equity")

    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9&]+", text)]

    # 2. Known Equities by keyword or ticker
    for word in words:
        if word in _KNOWN_EQUITIES:
            # Map canonical ticker
            sym = word.upper() if len(word) <= 5 and word in {"nvda", "tsla", "aapl", "msft", "googl", "goog", "amzn", "meta", "amd", "intc", "nflx", "spy", "qqq", "sofi", "pltr", "smh"} else ""
            if not sym:
                # Company name reverse lookup
                for s, n in _KNOWN_EQUITIES.items():
                    if word in n.lower():
                        sym = s.upper()
                        break
            sym = sym or word.upper()
            found[sym] = Entity(symbol=sym, name=_KNOWN_EQUITIES.get(word, _KNOWN_EQUITIES.get(sym.lower(), sym)), kind="equity")

    # 3. Known Cryptos
    for word in words:
        if word in _KNOWN_CRYPTO:
            sym = f"{word.upper()}-USD" if len(word) <= 5 else f"{word[:3].upper()}-USD"
            found[sym] = Entity(symbol=sym, name=_KNOWN_CRYPTO[word], kind="crypto")

    # 4. Indices with word boundaries (avoids "dow" matching "down")
    low_text = text.lower()
    for idx_key, idx_name in _KNOWN_INDICES.items():
        if re.search(r"\b" + re.escape(idx_key) + r"\b", low_text):
            sym = "^GSPC" if "s&p" in idx_key else ("^IXIC" if "nasdaq" in idx_key else ("^DJI" if "dow" in idx_key else idx_key.upper()))
            found[sym] = Entity(symbol=sym, name=idx_name, kind="index")

    # 5. Fallback for all-caps 2-5 letter word surrounded by market context
    has_market_context = bool(re.search(r"\b(stock|shares?|price|earnings|call|put|buy|sell|dividend|quarter|q[1-4]|ceo)\b", text, re.I))
    if has_market_context:
        for cap in re.findall(r"\b[A-Z]{2,5}\b", text):
            cap_low = cap.lower()
            if cap_low not in {"why", "how", "what", "when", "today", "the", "and", "for", "with", "news", "view"}:
                if cap not in found:
                    found[cap] = Entity(symbol=cap, name=f"{cap} Corporation", kind="equity")

    return list(found.values())


def classify_research_intent(raw: str) -> Optional[ResearchIntent]:
    """Deterministic classifier returning a ResearchIntent, or None if non-research."""
    text = (raw or "").strip()
    if not text:
        return None

    # Exclude non-research conversational / canvas-control queries
    if _GREETINGS_RE.match(text) or _CANVAS_CONTROL_RE.search(text):
        return None

    # Exclude pure media, weather, sports score requests unless research is asked
    if (_WEATHER_RE.search(text) or _SPORTS_SCORE_RE.search(text) or _MEDIA_PURE_RE.match(text)) and not ("research" in text.lower() or "report" in text.lower()):
        return None

    entities = extract_entities(text)
    low = text.lower()

    # Determine event types
    event_types: List[str] = []
    if re.search(r"\bearnings|quarterly\b", low):
        event_types.append("earnings")
    if re.search(r"\bguidance|outlook\b", low):
        event_types.append("guidance")
    if re.search(r"\bcpi|inflation\b", low):
        event_types.append("cpi")
    if re.search(r"\bfomc|fed\s+meeting|interest\s+rates?\b", low):
        event_types.append("fomc")
    if re.search(r"\b10-?k|10-?q|8-?k|sec\s+filing\b", low):
        event_types.append("filing")
    if re.search(r"\bm&a|merger|acquisition\b", low):
        event_types.append("m&a")

    # Classify Mode
    mode: ResearchMode = "general"
    if _QUANT_SIGNAL_RE.search(low):
        mode = "quant_signal"
    elif _MONITOR_RE.search(low):
        mode = "monitor"
    elif _DOSSIER_RE.search(low):
        mode = "dossier"
    elif _COMPARISON_RE.search(low) or (len(entities) >= 2 and (" vs " in low or " or " in low or "compare" in low)):
        mode = "comparison"
    elif (_EXPLAIN_MOVE_RE.search(low) or _SHORT_MOVE_RE.search(low)) and (entities or "market" in low or "stocks" in low):
        mode = "explain_move"
    elif _EVENT_REPORT_RE.search(low) and (entities or event_types):
        mode = "event_report"
    elif _MARKET_BRIEF_RE.search(low) or ("stock market" in low and ("news" in low or "today" in low or "overview" in low)):
        mode = "fast_market_brief"
    elif entities and ("news" in low or "report" in low or "overview" in low):
        mode = "event_report" if event_types else "fast_market_brief"
    elif "news" in low or "what's going on" in low or "what is happening" in low:
        mode = "general"
    else:
        # If no recognized research pattern, return None to let general router / reply handle
        return None

    # Time window detection
    window_label = "today"
    if re.search(r"\b5\s*d(?:ays?)?|this\s+week\b", low):
        window_label = "5d"
    elif re.search(r"\b1\s*m(?:onth)?|past\s+month\b", low):
        window_label = "1m"
    elif re.search(r"\byesterday|1\s*d(?:ay)?\b", low):
        window_label = "1d"
    elif re.search(r"\bsince\s+(?:earnings|event|last\s+week)\b", low):
        window_label = "since_event"

    time_window = TimeWindow(label=window_label)

    # Evidence Depth
    evidence_depth: EvidenceDepth = "standard"
    if mode == "dossier" or _DEPTH_RE.search(low):
        evidence_depth = "deep"
    elif mode in ("fast_market_brief", "monitor"):
        evidence_depth = "fast"

    asset_classes = ["equity"]
    if any(e.kind == "crypto" for e in entities) or "crypto" in low or "bitcoin" in low:
        asset_classes.append("crypto")
    if any(e.kind == "index" for e in entities) or "rates" in low or "treasury" in low:
        asset_classes.append("macro")

    user_asked_for_opinion = bool(_OPINION_RE.search(low))
    user_asked_for_sources = bool(_SOURCES_RE.search(low))
    needs_live_price = bool(entities or mode in ("explain_move", "comparison", "fast_market_brief"))
    needs_primary_sources = bool(event_types or mode in ("event_report", "dossier"))
    needs_comparison = bool(mode == "comparison" or len(entities) >= 2)

    return ResearchIntent(
        mode=mode,
        entities=entities,
        geography="us",
        asset_classes=asset_classes,
        time_window=time_window,
        evidence_depth=evidence_depth,
        user_asked_for_opinion=user_asked_for_opinion,
        user_asked_for_sources=user_asked_for_sources,
        needs_live_price=needs_live_price,
        needs_primary_sources=needs_primary_sources,
        needs_comparison=needs_comparison,
        event_types=event_types,
        raw_query=raw,
        subject_hint=", ".join(e.symbol for e in entities) if entities else text[:40],
    )
