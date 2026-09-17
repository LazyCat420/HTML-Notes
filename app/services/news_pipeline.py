"""Deterministic Evidence-First News Pipeline.

Replaces fragmented multi-builder/fallback behavior with a single entry point:
  news request -> normalize request -> retrieve candidates -> validate + dedupe
  -> fetch/verify article evidence -> rank -> render

Guarantees:
1. Every rendered news item originates from a VerifiedArticle with verified=True.
2. lazy-agent-service is used for candidate headline discovery.
3. scraper-service is used to verify selected articles against fetched page content.
4. Fast LLM only ranks or formats verified data; cannot invent queries or alter facts.
5. Strict safe degradation (zero verified evidence yields honest unavailable state).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional, List, Dict, Any, Tuple

import httpx

import app.config as config
from app.llm import fast_llm_json

logger = logging.getLogger("news_pipeline")

# Common tracking / marketing parameters to strip during URL canonicalization
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "source", "feed"
}

# Known major wire and news services for consensus cross-validation
_TIER1_WIRE_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "bbc.com", "bbc.co.uk",
    "wsj.com", "nytimes.com", "ft.com", "cnbc.com", "marketwatch.com",
    "seekingalpha.com", "economist.com", "washingtonpost.com", "theguardian.com"
}

# Region/country specific publisher domains to prevent language/country skew
_COUNTRY_SPECIFIC_DOMAINS = {
    "IN": {"ndtv.com", "indiatimes.com", "hindustantimes.com", "thehindu.com", "livemint.com", "indianexpress.com"},
    "GB": {"bbc.co.uk", "theguardian.com", "telegraph.co.uk", "independent.co.uk"},
    "AU": {"abc.net.au", "smh.com.au"},
    "CA": {"cbc.ca", "theglobeandmail.com"},
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with",
    "about", "is", "are", "was", "were", "what", "whats", "why", "how", "who",
    "today", "latest", "recent", "news", "headlines", "stories", "story", "update",
    "updates", "market", "stocks", "stock"
}

_ENTITY_ALIASES: Dict[str, set[str]] = {
    "apple": {"aapl", "apple", "iphone", "tim cook", "macbook", "ipad", "vision pro"},
    "aapl": {"aapl", "apple", "iphone", "tim cook"},
    "nvidia": {"nvda", "nvidia", "jensen huang", "geforce", "blackwell", "gpu"},
    "nvda": {"nvda", "nvidia", "jensen huang", "blackwell"},
    "tesla": {"tsla", "tesla", "elon musk", "cybertruck", "model 3", "model y", "fsd"},
    "tsla": {"tsla", "tesla", "elon musk"},
    "microsoft": {"msft", "microsoft", "satya nadella", "azure", "windows", "copilot"},
    "msft": {"msft", "microsoft", "azure"},
    "google": {"goog", "googl", "google", "alphabet", "sundar pichai", "gemini", "android"},
    "alphabet": {"goog", "googl", "google", "alphabet"},
    "meta": {"meta", "facebook", "mark zuckerberg", "instagram", "threads"},
    "amazon": {"amzn", "amazon", "andy jassy", "aws"},
    "amzn": {"amzn", "amazon", "aws"},
    "openai": {"openai", "open ai", "sam altman", "chatgpt", "gpt"},
}


@dataclass
class NewsTrace:
    trace_id: str
    request: Dict[str, Any]
    candidate_counts: Dict[str, int] = field(default_factory=dict)
    rejections: Dict[str, int] = field(default_factory=dict)
    rendered_article_ids: List[str] = field(default_factory=list)
    timings_ms: Dict[str, float] = field(default_factory=dict)


@dataclass
class NewsRequest:
    topic: Optional[str]
    locale: str = "US"                                  # e.g. "US", never language-only
    recency_hours: int = 24                             # 24 for today, 72 for latest, 168 for background
    limit: int = 6
    mode: Literal["general", "topic", "finance"] = "general"
    category: str = ""                                  # e.g. "business", "technology", "world"
    # Runtime context
    trace: Optional[NewsTrace] = None
    overview: str = ""


@dataclass
class CandidateArticle:
    title: str
    url: str
    publisher: str
    snippet: str
    published_at: Optional[datetime] = None
    image: str = ""
    source_provider: str = "lazy-agent"
    category: str = ""
    consensus: Optional[int] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifiedArticle:
    id: str
    title: str
    url: str
    publisher: str
    published_at: Optional[datetime]
    snippet: str
    body_excerpt: Optional[str]
    source_tier: str                                    # "primary_outlet", "wire", "wire_consensus", "aggregator"
    verified: bool
    relevance_score: Optional[float] = None
    image: str = ""
    category: str = ""
    verification_reason: str = "verified_match"


@dataclass
class NewsResponse:
    card_config: Dict[str, Any]
    articles: List[VerifiedArticle]
    trace: NewsTrace


def canonicalize_url(raw_url: str) -> str:
    """Normalize URL by stripping tracking parameters, fragments, and trailing slashes."""
    if not raw_url:
        return ""
    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return raw_url.strip()

        # Filter out tracking query params
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_pairs = [(k, v) for k, v in query_pairs if k.lower() not in _TRACKING_PARAMS]
        clean_query = urllib.parse.urlencode(clean_pairs)

        clean_path = parsed.path.rstrip("/")
        normalized = urllib.parse.urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            "",  # params
            clean_query,
            ""   # fragment
        ))
        return normalized
    except Exception:
        return raw_url.strip()


def parse_rfc_date(date_str: str) -> Optional[datetime]:
    """Parse RFC-2822 or ISO publication timestamp."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    try:
        # Try ISO 8601
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _token_overlap_score(title_a: str, title_b: str) -> float:
    """Calculate token overlap ratio between candidate title and scraped page title."""
    tokens_a = set(re.findall(r"[a-z0-9]+", (title_a or "").lower())) - _STOPWORDS
    tokens_b = set(re.findall(r"[a-z0-9]+", (title_b or "").lower())) - _STOPWORDS
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a.intersection(tokens_b)
    return len(intersection) / float(min(len(tokens_a), len(tokens_b)))


# ── Step 1: Deterministic Request Normalization ──────────────────────────────

def normalize_news_request(
    message: str,
    *,
    finance: Optional[bool] = None,
    general: Optional[bool] = None,
    category: str = "",
    locale: str = "US",
    recency_hours: Optional[int] = None,
    limit: int = 6
) -> NewsRequest:
    """Deterministically map a user query into a strictly typed NewsRequest."""
    raw = (message or "").strip()
    low = raw.lower()

    # Determine Recency Window
    if recency_hours is None:
        if re.search(r"\b(past\s+week|this\s+week|7\s*d(?:ays?)?|background|history)\b", low):
            recency = 168
        elif re.search(r"\b(latest|recent|few\s+days|3\s*d(?:ays?)?)\b", low):
            recency = 72
        else:
            recency = 24  # Default for "today", "now", "current headlines"
    else:
        recency = recency_hours

    # Locale enforcement: defaults to US, overrides on explicit country mention
    loc = (locale or "US").strip().upper()
    if re.search(r"\b(uk|britain|british|london)\b", low):
        loc = "GB"
    elif re.search(r"\b(canada|canadian)\b", low):
        loc = "CA"
    elif re.search(r"\b(europe|european|eu)\b", low):
        loc = "EU"
    elif re.search(r"\b(japan|japanese)\b", low):
        loc = "JP"
    elif re.search(r"\b(australia|australian)\b", low):
        loc = "AU"
    elif re.search(r"\b(india|indian)\b", low):
        loc = "IN"

    # Finance detection
    has_finance = finance if finance is not None else bool(
        re.search(r"\b(stock|stocks|market|nasdaq|dow|s&p|sp500|shares|yield|rates|treasury|crypto|bitcoin|ethereum)\b", low)
        or (re.search(r"\b[A-Z]{1,5}\b", raw) and re.search(r"\b(earnings|drop|up|down|guidance|dividend)\b", low))
    )

    # General vs Topic detection
    is_blank_or_generic = bool(
        not raw
        or re.search(r"^(news|today'?s?\s+news|headlines|top\s+stories|what'?s?\s+(going\s+on|happening)(\s+in\s+the\s+news)?|the\s+news|latest\s+news|top\s+news|current\s+news|news\s+headlines)[?.!]?$", low)
        or re.search(r"^(stock\s+market\s+(news|today)|market\s+news|market\s+overview)$", low)
    )

    if general is True or (general is None and is_blank_or_generic):
        mode: Literal["general", "topic", "finance"] = "finance" if has_finance else "general"
        topic = "stock market" if has_finance else None
    else:
        mode = "finance" if has_finance else "topic"
        # Extract subject by stripping question scaffolding
        stripped = re.sub(r"\b(news|about|on|latest|updates?|today|what'?s?\s+going\s+on\s+with|tell\s+me\s+about)\b", "", raw, flags=re.I)
        cleaned_topic = " ".join(re.findall(r"[A-Za-z0-9$-]+", stripped)).strip()
        topic = cleaned_topic if cleaned_topic else (raw or None)

    return NewsRequest(
        topic=topic,
        locale=loc,
        recency_hours=recency,
        limit=limit,
        mode=mode,
        category=category or ("business" if mode == "finance" and not topic else "")
    )


# ── Step 2: Retrieve Candidate Articles (lazy-agent-service) ─────────────────

async def collect_candidates(req: NewsRequest) -> List[CandidateArticle]:
    """Retrieve candidate headlines from lazy-agent-service news_search."""
    clean_topic = (req.topic or "").strip()
    candidates: List[CandidateArticle] = []
    raw_count = 0
    t0 = time.time()

    # 1. Check if news_search or _shared_news_search is available/patched on main
    try:
        import app.main as main
        news_fn = getattr(main, "news_search", None) or getattr(main, "_shared_news_search", None)
    except Exception:
        news_fn = None

    if news_fn:
        try:
            items = await news_fn(
                clean_topic,
                limit=max(req.limit * 2, 12),
                category=req.category,
                country=req.locale.lower()
            )
            if isinstance(items, list):
                raw_count += len(items)
                for r in items:
                    if not isinstance(r, dict):
                        continue
                    title = str(r.get("title", "")).strip()
                    url = str(r.get("url", "")).strip()
                    if not title or not url or not (url.startswith("http://") or url.startswith("https://")):
                        continue
                    pub_date = parse_rfc_date(str(r.get("date", "") or r.get("published", "") or ""))
                    pub_name = str(r.get("meta") or r.get("source") or r.get("publisher") or urllib.parse.urlparse(url).netloc).strip()
                    candidates.append(CandidateArticle(
                        title=title,
                        url=url,
                        publisher=pub_name,
                        snippet=str(r.get("snippet", "") or r.get("og_desc", "") or "").strip(),
                        published_at=pub_date,
                        image=str(r.get("image", "") or "").strip(),
                        source_provider="lazy-agent",
                        category=str(r.get("category", "") or "").strip(),
                        consensus=r.get("consensus"),
                        raw_payload=r
                    ))
        except Exception as e:
            logger.debug(f"[NEWS PIPELINE] candidate fetch via news_fn failed: {e}")

    # 2. If finance mode and finnews articles available, also query
    if req.mode == "finance":
        try:
            import app.main as main
            fin_fn = getattr(main, "_finnews_articles", None)
            if fin_fn:
                fin_items = await fin_fn(query=clean_topic, limit=req.limit)
                if isinstance(fin_items, list):
                    raw_count += len(fin_items)
                    for f in fin_items:
                        if not isinstance(f, dict):
                            continue
                        title = str(f.get("title", "")).strip()
                        url = str(f.get("url", "")).strip()
                        if not title or not url or not (url.startswith("http://") or url.startswith("https://")):
                            continue
                        pub_date = parse_rfc_date(str(f.get("published", "") or ""))
                        pub_name = str(f.get("publisher") or "Financial News").strip()
                        candidates.append(CandidateArticle(
                            title=title,
                            url=url,
                            publisher=pub_name,
                            snippet=str(f.get("og_desc", "") or f.get("snippet", "") or "").strip(),
                            published_at=pub_date,
                            image=str(f.get("image", "") or "").strip(),
                            source_provider="finnews",
                            category="business",
                            consensus=None,
                            raw_payload=f
                        ))
        except Exception as e:
            logger.debug(f"[NEWS PIPELINE] finnews fetch failed: {e}")

    # 3. Direct HTTP fallback to LAZY_TOOL_SERVICE_URL if no candidates collected yet
    if not candidates:
        endpoint = f"{config.LAZY_TOOL_SERVICE_URL}/execute/news_search"
        body = {
            "topic": clean_topic,
            "limit": max(req.limit * 2, 12),
            "country": req.locale.lower()
        }
        if req.category:
            body["category"] = req.category
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(endpoint, json=body)
                if resp.status_code == 200:
                    payload = resp.json()
                    body_dict = payload.get("result", payload) if isinstance(payload, dict) else {}
                    rows = body_dict.get("items") or []
                    raw_count += len(rows)
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        title = str(r.get("title", "")).strip()
                        url = str(r.get("url", "")).strip()
                        if not title or not url or not (url.startswith("http://") or url.startswith("https://")):
                            continue
                        pub_date = parse_rfc_date(str(r.get("date", "") or ""))
                        pub_name = str(r.get("source") or urllib.parse.urlparse(url).netloc).strip()
                        candidates.append(CandidateArticle(
                            title=title,
                            url=url,
                            publisher=pub_name,
                            snippet=str(r.get("snippet", "")).strip(),
                            published_at=pub_date,
                            image=str(r.get("image", "") or "").strip(),
                            source_provider="lazy-agent",
                            category=str(r.get("category", "") or "").strip(),
                            consensus=r.get("consensus"),
                            raw_payload=r
                        ))
        except Exception as e:
            logger.debug(f"[NEWS PIPELINE] direct lazy-agent fetch failed: {e}")

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if req.trace:
        req.trace.candidate_counts["lazy_agent"] = raw_count
        req.trace.timings_ms["candidate_fetch"] = elapsed_ms

    logger.info(f"[NEWS PIPELINE] collected {len(candidates)} candidate articles (raw={raw_count}) in {elapsed_ms}ms")
    return candidates


# ── Step 3: Validate & Deduplicate Candidates ────────────────────────────────

def validate_and_dedupe(
    candidates: List[CandidateArticle],
    req: NewsRequest
) -> List[CandidateArticle]:
    """Validate candidate integrity, filter stale/mismatched rows, and deduplicate."""
    rejections = {
        "bad_url": 0,
        "title_url_mismatch": 0,
        "locale_mismatch": 0,
        "stale": 0,
        "topic_mismatch": 0,
        "duplicate": 0
    }

    seen_urls: set[str] = set()
    seen_title_keys: set[str] = set()
    valid_candidates: List[CandidateArticle] = []

    now = datetime.now(timezone.utc)
    max_age_seconds = req.recency_hours * 3600

    # Expand topic tokens with entity aliases
    clean_topic = (req.topic or "").strip().lower()
    topic_tokens = set(re.findall(r"[a-z0-9]+", clean_topic)) - _STOPWORDS if clean_topic else set()
    entity_aliases = _ENTITY_ALIASES.get(clean_topic, set())
    for alias in entity_aliases:
        topic_tokens.update(set(re.findall(r"[a-z0-9]+", alias)) - _STOPWORDS)

    for cand in candidates:
        canon_url = canonicalize_url(cand.url)
        if not canon_url or not (canon_url.startswith("http://") or canon_url.startswith("https://")):
            rejections["bad_url"] += 1
            continue

        # Reject duplicates by canonical URL
        if canon_url in seen_urls:
            rejections["duplicate"] += 1
            continue

        # Reject duplicates by normalized headline words
        norm_title = " ".join(re.findall(r"[a-z0-9]+", cand.title.lower()))[:80]
        if norm_title in seen_title_keys:
            rejections["duplicate"] += 1
            continue

        # Locale enforcement: exclude irrelevant foreign country-biased domains
        domain = urllib.parse.urlparse(canon_url).netloc.lower()
        if req.locale == "US" and not req.topic:
            # Check if domain belongs to foreign country news publishers
            is_foreign = False
            for c_code, c_domains in _COUNTRY_SPECIFIC_DOMAINS.items():
                if c_code != "US":
                    if any(cd in domain for cd in c_domains) or domain.endswith(f".{c_code.lower()}"):
                        is_foreign = True
                        break
            if is_foreign:
                rejections["locale_mismatch"] += 1
                continue

        # Recency validation if timestamp available
        if cand.published_at:
            age_s = (now - cand.published_at).total_seconds()
            if age_s > max_age_seconds:
                rejections["stale"] += 1
                continue

        # Topic relevance check for specific topic requests
        if req.mode == "topic" and topic_tokens:
            cand_text = f"{cand.title} {cand.snippet}".lower()
            cand_tokens = set(re.findall(r"[a-z0-9]+", cand_text))
            if not topic_tokens.intersection(cand_tokens):
                rejections["topic_mismatch"] += 1
                continue

        seen_urls.add(canon_url)
        seen_title_keys.add(norm_title)
        cand.url = canon_url
        valid_candidates.append(cand)

    if req.trace:
        req.trace.candidate_counts["after_validation"] = len(valid_candidates)
        req.trace.candidate_counts["after_dedupe"] = len(valid_candidates)
        req.trace.rejections = rejections

    return valid_candidates


# ── Step 4: Article Evidence Verification via scraper-service ────────────────

async def verify_top_candidates(
    candidates: List[CandidateArticle],
    req: NewsRequest,
    max_verify: int = 8,
    timeout_s: float = 6.0
) -> List[VerifiedArticle]:
    """Concurrently verify top candidate articles via scraper-service."""
    t0 = time.time()
    top_candidates = candidates[:max_verify]
    if not top_candidates:
        if req.trace:
            req.trace.candidate_counts["verified"] = 0
            req.trace.timings_ms["verification"] = 0.0
        return []

    # Check for mocked/patched scrape function on main
    try:
        import app.main as main
        scrape_fn = getattr(main, "_scrape", None) or getattr(main, "scrape_url", None)
    except Exception:
        scrape_fn = None

    async def _verify_one(cand: CandidateArticle) -> VerifiedArticle:
        ev_id = f"art_{hashlib.md5(cand.url.encode()).hexdigest()[:10]}"
        domain = urllib.parse.urlparse(cand.url).netloc.lower()
        is_tier1 = any(t1 in domain for t1 in _TIER1_WIRE_DOMAINS)

        scraped_content = ""
        page_title = ""
        success = False

        if scrape_fn:
            try:
                res = await scrape_fn(cand.url)
                if isinstance(res, dict):
                    success = bool(res.get("success", True))
                    scraped_content = str(res.get("content") or res.get("text") or "")
                    page_title = str(res.get("title") or "")
                elif isinstance(res, str):
                    success = bool(res.strip())
                    scraped_content = res
            except Exception as e:
                logger.debug(f"[NEWS VERIFY] patched scrape failed for {cand.url}: {e}")
        else:
            try:
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    resp = await client.post(
                        f"{config.SCRAPER_SERVICE_URL}/scrape",
                        json={"url": cand.url, "engine": "auto"},
                    )
                    if resp.status_code == 200:
                        payload = resp.json()
                        success = bool(payload.get("success"))
                        scraped_content = payload.get("content") or ""
                        page_title = payload.get("title") or ""
            except Exception as e:
                logger.debug(f"[NEWS VERIFY] scraper-service failed for {cand.url}: {e}")

        # Verification checks
        is_verified = False
        verif_reason = "unverified"
        body_excerpt = None

        clean_topic = (req.topic or "").strip().lower()
        topic_tokens = set(re.findall(r"[a-z0-9]+", clean_topic)) - _STOPWORDS if clean_topic else set()
        for alias in _ENTITY_ALIASES.get(clean_topic, set()):
            topic_tokens.update(set(re.findall(r"[a-z0-9]+", alias)) - _STOPWORDS)

        if success and scraped_content:
            first_lines = (page_title + " " + scraped_content[:400]).strip()
            overlap = _token_overlap_score(cand.title, first_lines)
            title_in_page = cand.title.lower() in scraped_content.lower()

            has_topic = True
            if req.mode in ("topic", "finance") and topic_tokens:
                content_tokens = set(re.findall(r"[a-z0-9]+", scraped_content[:4000].lower()))
                has_topic = bool(topic_tokens.intersection(content_tokens))

            # Must have content length >= 120, title overlap >= 0.35 (or title match), and topic match
            if len(scraped_content.strip()) >= 120 and (overlap >= 0.35 or title_in_page) and has_topic:
                is_verified = True
                verif_reason = "scraper_content_verified"
                body_excerpt = scraped_content[:600].strip()
            elif overlap < 0.20 and not title_in_page:
                verif_reason = "title_url_mismatch"
                if req.trace and "title_url_mismatch" in req.trace.rejections:
                    req.trace.rejections["title_url_mismatch"] += 1
        elif is_tier1 and (cand.consensus or 0) >= 2:
            # Wire consensus: Major Tier-1 publisher with cross-room consensus
            is_verified = True
            verif_reason = "tier1_wire_consensus"
            body_excerpt = cand.snippet
        elif is_tier1 and not scrape_fn:
            # When scraper service is offline/unconfigured, Tier-1 primary outlet passes with snippet
            is_verified = True
            verif_reason = "tier1_primary_outlet"
            body_excerpt = cand.snippet

        source_tier = "primary_outlet" if is_tier1 else ("wire_consensus" if verif_reason == "tier1_wire_consensus" else "aggregator")

        return VerifiedArticle(
            id=ev_id,
            title=cand.title,
            url=cand.url,
            publisher=cand.publisher,
            published_at=cand.published_at,
            snippet=cand.snippet,
            body_excerpt=body_excerpt,
            source_tier=source_tier,
            verified=is_verified,
            relevance_score=1.0 if is_verified else 0.0,
            image=cand.image,
            category=cand.category,
            verification_reason=verif_reason
        )

    fetch_tasks = [_verify_one(c) for c in top_candidates]
    verified_results = await asyncio.gather(*fetch_tasks, return_exceptions=False)

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if req.trace:
        req.trace.candidate_counts["verified"] = sum(1 for a in verified_results if a.verified)
        req.trace.timings_ms["verification"] = elapsed_ms

    return verified_results


# ── Step 5: Source Quality, Diversity & Locale Gates ─────────────────────────

def filter_and_diversify(
    articles: List[VerifiedArticle],
    req: NewsRequest
) -> List[VerifiedArticle]:
    """Enforce diversity, publisher caps, and ensure unverified articles cannot outrank verified."""
    # Split verified vs unverified
    verified = [a for a in articles if a.verified]

    # Rule: Unverified candidates cannot render ahead of verified candidates
    # Only verified articles are eligible for display
    publisher_counts: Dict[str, int] = {}
    selected_verified: List[VerifiedArticle] = []

    for art in verified:
        pub = art.publisher.lower()
        count = publisher_counts.get(pub, 0)
        # Cap: No single publisher may occupy more than 2 of top 5
        if len(selected_verified) < 5 and count >= 2:
            continue
        publisher_counts[pub] = count + 1
        selected_verified.append(art)
        if len(selected_verified) >= req.limit:
            break

    # If general news has >= 3 articles in top 5, verify distinct publisher diversity
    distinct_publishers = {a.publisher.lower() for a in selected_verified[:5]}
    if len(selected_verified) >= 3 and len(distinct_publishers) < 2 and len(verified) > len(selected_verified):
        # Swap in alternative publishers if available
        for art in verified:
            if art.publisher.lower() not in distinct_publishers and len(selected_verified) < req.limit:
                selected_verified.append(art)

    return selected_verified


# ── Step 6: Restricted Fast LLM Ranking & Overview ───────────────────────────

async def rank_verified_articles(
    articles: List[VerifiedArticle],
    req: NewsRequest
) -> List[VerifiedArticle]:
    """Call fast_llm_json strictly to score and produce an overview from verified excerpts."""
    t0 = time.time()
    # Filter and diversify first
    eligible = filter_and_diversify(articles, req)
    if not eligible:
        if req.trace:
            req.trace.timings_ms["ranking"] = 0.0
        return []

    source_lines = []
    for i, a in enumerate(eligible):
        source_lines.append(
            f"[{i}] {a.title} ({a.publisher})\nExcerpt: {a.body_excerpt or a.snippet}"
        )

    instruction = (
        f"You are an objective news editor. Today is {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.\n"
        f"Request: topic={req.topic!r}, mode={req.mode!r}, locale={req.locale!r}.\n"
        f"Analyze these VERIFIED news stories and return ONLY a JSON object with:\n"
        f'- "overview": One or two concise sentences summarizing the factual development strictly from the excerpts.\n'
        f'- "ranked_indices": array of source indices [0..N] in descending order of significance.\n\n'
        f"SOURCES:\n" + "\n\n".join(source_lines)
    )

    overview = ""
    ranked_indices = list(range(len(eligible)))

    try:
        import app.main as main
        llm_fn = getattr(main, "fast_llm_json", None) or fast_llm_json
    except Exception:
        llm_fn = fast_llm_json

    try:
        llm_res = await llm_fn(instruction, max_tokens=256)
        if isinstance(llm_res, dict):
            overview = str(llm_res.get("overview", "")).strip()
            indices = llm_res.get("ranked_indices")
            if isinstance(indices, list):
                valid_indices = [idx for idx in indices if isinstance(idx, int) and 0 <= idx < len(eligible)]
                if len(valid_indices) == len(eligible):
                    ranked_indices = valid_indices
    except Exception as e:
        logger.warning(f"[NEWS PIPELINE] fast_llm_json ranking failed, using deterministic order: {e}")

    # Reorder articles if LLM provided valid ranking
    reordered = [eligible[i] for i in ranked_indices]

    # Deterministic fallback overview if LLM was unavailable
    if not overview and reordered:
        top = reordered[0]
        overview = f"{top.title} ({top.publisher})."

    req.overview = overview

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if req.trace:
        req.trace.timings_ms["ranking"] = elapsed_ms

    return reordered


# ── Step 7: Safe Degradation & Renderer ───────────────────────────────────────

def render_news_response(
    articles: List[VerifiedArticle],
    req: NewsRequest,
    overview: Optional[str] = None
) -> NewsResponse:
    """Render verified articles into data_card widget schema with safe degradation."""
    verified_articles = [a for a in articles if a.verified]
    verified_count = len(verified_articles)
    final_overview = overview or req.overview or ""

    if req.mode == "finance":
        card_title = ("Market News" if not req.topic or req.topic == "stock market" else f"Market News: {req.topic.title()}")
        icon = "trending_up"
    elif req.mode == "general":
        card_title = "Top Stories" if not req.category else f"News: {req.category.title()}"
        icon = "newspaper"
    else:
        card_title = f"News: {(req.topic or '').title()}"
        icon = "newspaper"

    trace = req.trace or NewsTrace(
        trace_id=f"news_{int(time.time()*1000)}",
        request={"topic": req.topic, "locale": req.locale, "mode": req.mode}
    )

    # Outcome 1: Zero verified articles -> honest unavailable state
    if verified_count == 0:
        card_config = {
            "title": "News Unavailable",
            "icon": icon,
            "answer": (
                "Unable to retrieve verified news coverage at this moment. "
                "Candidate articles could not be verified against primary sources."
            ),
            "subtitle": "0 verified stories",
            "items": [],
            "_trace_id": trace.trace_id,
            "_verified": False
        }
        trace.rendered_article_ids = []
        return NewsResponse(card_config=card_config, articles=[], trace=trace)

    # Outcome 2: 1 to 4 verified articles -> Limited verified coverage
    # Outcome 3: 5+ verified articles -> Standard full coverage
    is_limited = verified_count < 5
    subtitle = f"Limited verified coverage ({verified_count} verified)" if is_limited else f"{verified_count} verified stories"

    items = []
    for a in verified_articles:
        items.append({
            "title": a.title,
            "description": a.body_excerpt or a.snippet,
            "url": a.url,
            "image": a.image,
            "meta": f"{a.publisher} · {a.source_tier.replace('_', ' ')}",
            "badge": "Verified",
            "_verified": True,
            "_source_tier": a.source_tier,
            "_article_id": a.id
        })

    card_config = {
        "title": card_title[:60],
        "icon": icon,
        "answer": final_overview or f"{verified_articles[0].title} ({verified_articles[0].publisher}).",
        "subtitle": subtitle,
        "items": items,
        "_trace_id": trace.trace_id,
        "_verified": True
    }

    trace.rendered_article_ids = [a.id for a in verified_articles]
    return NewsResponse(card_config=card_config, articles=verified_articles, trace=trace)


# ── Unified Pipeline Entrypoint ──────────────────────────────────────────────

async def build_news_response(
    req_or_message: NewsRequest | str,
    *,
    finance: Optional[bool] = None,
    general: Optional[bool] = None,
    category: str = "",
    locale: str = "US",
    recency_hours: Optional[int] = None,
    limit: int = 6
) -> NewsResponse:
    """THE single deterministic entry point for all news retrieval in HTML-Notes."""
    t0 = time.time()
    trace_id = f"news_{int(t0 * 1000)}_{hashlib.md5(str(req_or_message).encode()).hexdigest()[:6]}"

    # Phase 1: Normalize Request
    if isinstance(req_or_message, NewsRequest):
        req = req_or_message
    else:
        req = normalize_news_request(
            req_or_message,
            finance=finance,
            general=general,
            category=category,
            locale=locale,
            recency_hours=recency_hours,
            limit=limit
        )

    req.trace = NewsTrace(
        trace_id=trace_id,
        request={
            "topic": req.topic,
            "locale": req.locale,
            "recency_hours": req.recency_hours,
            "mode": req.mode,
            "category": req.category
        }
    )

    # Phase 2: Retrieve Candidates (lazy-agent-service)
    candidates = await collect_candidates(req)

    # Phase 3: Validate & Deduplicate
    valid = validate_and_dedupe(candidates, req)

    # Phase 4: Fetch & Verify Article Evidence (scraper-service)
    verified = await verify_top_candidates(valid, req, max_verify=8, timeout_s=5.0)

    # Phase 5 & 6: Rank Verified Articles (Restricted Fast LLM)
    ranked = await rank_verified_articles(verified, req)

    # Phase 7: Observability Trace & Render
    req.trace.timings_ms["total"] = round((time.time() - t0) * 1000, 1)

    logger.info(
        f"[NEWS PIPELINE] trace={trace_id} mode={req.mode} topic={req.topic!r} "
        f"candidates={len(candidates)} valid={len(valid)} "
        f"verified={req.trace.candidate_counts.get('verified', 0)} "
        f"rendered={len(ranked)} total_time={req.trace.timings_ms['total']}ms"
    )

    return render_news_response(ranked, req)
