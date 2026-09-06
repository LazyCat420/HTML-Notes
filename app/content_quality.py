"""Content Quality & Source Reputation Engine for HTML-Notes.

Modelled after youtube-wallgarden's scoring, topic parole, and affinity algorithm:
1. Multi-axis scoring: source reputation, heuristic quality checks (tabloid,
   gossip, clickbait, PR spam, ALL CAPS, excessive punctuation), freshness,
   and explicit item-level user vote overrides (+25 / -50).
2. Wallgarden-style burn parole system: repeated downvotes trigger domain burns
   that serve a sentence of 6 months * 2^(strikes - 1). Expired burns become
   eligible again with doubled sentences upon re-burn.
3. User voting (+1 upvote, -1 downvote) on individual news stories and data items.
"""
import os
import re
import time
import sqlite3
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

try:
    from app.config import DATABASE_URL
except ImportError:
    DATABASE_URL = os.getenv("DATABASE_URL", "data/notes.db")

# Wallgarden constants adapted for news sources
BURN_PAROLE_BASE_SECONDS = 180 * 86400  # 6 months (in seconds)
AUTO_BURN_THRESHOLD = 3                 # 3 net downvotes or 3 raw downvotes with 0 upvotes

# Known tabloid & gossip publication domains
TABLOID_DOMAINS = frozenset({
    "dailymail.co.uk", "mailonsunday.co.uk", "tmz.com", "pagesix.com",
    "thesun.co.uk", "thesun.com", "nationalenquirer.com", "usmagazine.com",
    "eonline.com", "perezhilton.com", "okmagazine.com", "lifeandstylemag.com",
    "radaronline.com", "celebdirtylaundry.com", "star-magazine.com",
    "mirror.co.uk", "dailystar.co.uk", "express.co.uk", "closerweekly.com",
    "intouchweekly.com", "heatworld.com", "hellomagazine.com"
})

# Gossip and celebrity drama patterns
GOSSIP_PATTERNS_RE = re.compile(
    r"\b(?:spotted with|seen with|rumored to date|romance rumors|breakup rumors|"
    r"bikini body|steamy photos|caught on camera|plastic surgery|cheating scandal|"
    r"baby bump|drama erupts|tell-all|secret lover|shocking split|feud boils over|"
    r"unrecognizable|fans are furious|claps back at)\b",
    re.IGNORECASE
)

# Clickbait phrasing (adapted from wallgarden WG_CLICKBAIT_RE)
CLICKBAIT_PATTERNS_RE = re.compile(
    r"\b(?:you won['’]t believe|shocking (?:truth|reveal|moment|twist)|"
    r"jaw[- ]dropping|what happens next|will blow your mind|can['’]t unsee|"
    r"everyone is talking about|leaves (?:fans|crowd|viewers) (?:stunned|speechless|furious)|"
    r"this one trick|mind[- ]boggling|the real reason why)\b",
    re.IGNORECASE
)

# Disguised PR / Sponsored placements
PR_SPAM_PATTERNS_RE = re.compile(
    r"\b(?:sponsored content|promoted story|advertorial|brand spotlight|"
    r"paid partnership|presented by|in partnership with|contributed content|"
    r"press release distributor|paid press release)\b",
    re.IGNORECASE
)

# In-memory caches for low-latency scoring
_reputation_cache: Dict[str, dict] = {}
_burned_cache: Optional[Dict[str, Any]] = None
_item_votes_cache: Dict[str, int] = {}
_cache_timestamp: float = 0.0


def invalidate_cache():
    """Clear all in-memory scoring caches."""
    global _reputation_cache, _burned_cache, _item_votes_cache, _cache_timestamp
    _reputation_cache = {}
    _burned_cache = None
    _item_votes_cache = {}
    _cache_timestamp = 0.0


def get_connection():
    """Connect to SQLite with row factory."""
    os.makedirs(os.path.dirname(DATABASE_URL) or ".", exist_ok=True)
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    return conn


def init_content_quality_tables():
    """Create tables for votes and domain reputations."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS content_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        domain TEXT NOT NULL,
        publisher TEXT,
        title TEXT,
        vote INTEGER NOT NULL, -- +1 for upvote, -1 for downvote
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS source_reputation (
        domain TEXT PRIMARY KEY,
        upvotes INTEGER NOT NULL DEFAULT 0,
        downvotes INTEGER NOT NULL DEFAULT 0,
        score REAL NOT NULL DEFAULT 0.0,
        burn_strikes INTEGER NOT NULL DEFAULT 0,
        burn_start REAL DEFAULT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_votes_domain ON content_votes(domain);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_votes_url ON content_votes(url);")
    conn.commit()
    conn.close()


def extract_domain(url: str, fallback: str = "") -> str:
    """Clean domain/hostname from a URL or publisher name."""
    url_str = (url or "").strip()
    if url_str:
        if not url_str.startswith(("http://", "https://")):
            url_str = "https://" + url_str
        try:
            parsed = urlparse(url_str)
            host = parsed.netloc.lower()
            if ":" in host:
                host = host.split(":", 1)[0]
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        except Exception:
            pass

    fb = (fallback or "").strip().lower()
    fb = re.sub(r"[^\w.\-]", "", fb)
    return fb


def classify_content_quality(item: dict) -> dict:
    """Analyze an item's title, url, description, and source for quality signals.

    Returns:
        quality_class: "GENUINE", "TABLOID", "GOSSIP", "PR_SPAM", "CLICKBAIT"
        flags: list of triggered heuristic flags
        penalty: total heuristic score penalty
    """
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or item.get("link") or "").strip()
    desc = str(item.get("description") or item.get("summary") or item.get("snippet") or "").strip()
    meta = str(item.get("meta") or item.get("source") or item.get("publisher") or "").strip()

    combined_text = f"{title} {desc} {meta}".lower()
    domain = extract_domain(url, meta)

    flags = []
    penalty = 0.0

    # 1. Tabloid domain check
    if domain in TABLOID_DOMAINS or any(domain.endswith("." + td) for td in TABLOID_DOMAINS):
        flags.append("tabloid_source")
        penalty += 25.0

    # 2. ALL-CAPS check (wallgarden heuristic: >5 letters, >80% uppercase)
    letters = re.findall(r"[a-zA-Z]", title)
    if len(letters) > 5:
        upper = sum(1 for c in letters if c.isupper())
        if upper / len(letters) > 0.8:
            flags.append("all_caps")
            penalty += 8.0

    # 3. Excessive punctuation (??? or !!!)
    if re.search(r"(\?{3,}|!{3,})", title):
        flags.append("excessive_punctuation")
        penalty += 5.0

    # 4. Clickbait phrasing
    if CLICKBAIT_PATTERNS_RE.search(title) or CLICKBAIT_PATTERNS_RE.search(desc):
        flags.append("clickbait_phrasing")
        penalty += 12.0

    # 5. Gossip / Celebrity rumor patterns
    if GOSSIP_PATTERNS_RE.search(title) or GOSSIP_PATTERNS_RE.search(desc):
        flags.append("gossip_content")
        penalty += 15.0

    # 6. Disguised PR / Sponsored content
    if PR_SPAM_PATTERNS_RE.search(combined_text):
        flags.append("pr_spam")
        penalty += 20.0

    # Determine dominant classification
    if "tabloid_source" in flags:
        quality_class = "TABLOID"
    elif "gossip_content" in flags:
        quality_class = "GOSSIP"
    elif "pr_spam" in flags:
        quality_class = "PR_SPAM"
    elif "clickbait_phrasing" in flags or "all_caps" in flags:
        quality_class = "CLICKBAIT"
    else:
        quality_class = "GENUINE"

    return {
        "quality_class": quality_class,
        "flags": flags,
        "penalty": penalty,
    }


def get_source_reputation(domain: str) -> dict:
    """Retrieve reputation stats for a domain."""
    clean_domain = extract_domain("", domain)
    if clean_domain in _reputation_cache:
        return _reputation_cache[clean_domain]

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM source_reputation WHERE domain = ?", (clean_domain,))
    row = cursor.fetchone()
    conn.close()

    if row:
        rep = {
            "domain": row["domain"],
            "upvotes": row["upvotes"],
            "downvotes": row["downvotes"],
            "score": row["score"],
            "burn_strikes": row["burn_strikes"],
            "burn_start": row["burn_start"],
            "updated_at": row["updated_at"],
        }
    else:
        rep = {
            "domain": clean_domain,
            "upvotes": 0,
            "downvotes": 0,
            "score": 0.0,
            "burn_strikes": 0,
            "burn_start": None,
            "updated_at": "",
        }

    _reputation_cache[clean_domain] = rep
    return rep


def is_source_burned(domain: str, now: Optional[float] = None) -> bool:
    """Check if a domain is currently serving a burn parole sentence."""
    if not domain:
        return False
    clean_domain = extract_domain("", domain)
    rep = get_source_reputation(clean_domain)
    burn_start = rep.get("burn_start")
    if not burn_start:
        return False

    current_time = now if now is not None else time.time()
    strikes = max(1, rep.get("burn_strikes") or 1)
    sentence = BURN_PAROLE_BASE_SECONDS * (2 ** (strikes - 1))
    return (current_time - burn_start) < sentence


def burn_source(domain: str, strikes: Optional[int] = None, start_time: Optional[float] = None) -> dict:
    """Burn a source with Wallgarden parole sentence doubling."""
    clean_domain = extract_domain("", domain)
    current_rep = get_source_reputation(clean_domain)
    now = start_time if start_time is not None else time.time()

    if strikes is not None:
        new_strikes = strikes
    else:
        prev_strikes = current_rep.get("burn_strikes", 0)
        new_strikes = prev_strikes + 1 if prev_strikes > 0 else 1

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO source_reputation (domain, upvotes, downvotes, score, burn_strikes, burn_start, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    ON CONFLICT(domain) DO UPDATE SET
        burn_strikes = excluded.burn_strikes,
        burn_start = excluded.burn_start,
        updated_at = datetime('now');
    """, (clean_domain, current_rep.get("upvotes", 0), current_rep.get("downvotes", 0),
          current_rep.get("score", 0.0), new_strikes, now))
    conn.commit()
    conn.close()

    invalidate_cache()
    return get_source_reputation(clean_domain)


def unburn_source(domain: str) -> dict:
    """Manual override: release a domain from active burn sentence."""
    clean_domain = extract_domain("", domain)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE source_reputation
    SET burn_start = NULL, updated_at = datetime('now')
    WHERE domain = ?;
    """, (clean_domain,))
    conn.commit()
    conn.close()
    invalidate_cache()
    return get_source_reputation(clean_domain)


def record_vote(url: str, title: str, publisher: str, vote: int) -> dict:
    """Record a user vote (+1 or -1) and update source reputation."""
    vote_val = 1 if vote > 0 else -1
    clean_domain = extract_domain(url, publisher)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    conn = get_connection()
    cursor = conn.cursor()

    # Record the individual vote
    cursor.execute("""
    INSERT INTO content_votes (url, domain, publisher, title, vote, created_at)
    VALUES (?, ?, ?, ?, ?, ?);
    """, (url, clean_domain, publisher, title, vote_val, now_iso))

    # Read current domain stats
    cursor.execute("SELECT upvotes, downvotes, burn_strikes, burn_start FROM source_reputation WHERE domain = ?", (clean_domain,))
    row = cursor.fetchone()
    up = (row["upvotes"] if row else 0) + (1 if vote_val > 0 else 0)
    down = (row["downvotes"] if row else 0) + (1 if vote_val < 0 else 0)
    strikes = row["burn_strikes"] if row else 0
    burn_start = row["burn_start"] if row else None

    # Wallgarden-style score formula: upvotes yield diminishing positive affinity, downvotes penalize
    new_score = (min(15.0, up * 3.0)) - (down * 5.0)

    # Check auto-burn threshold
    should_burn = False
    if vote_val < 0 and (down >= AUTO_BURN_THRESHOLD and (down - up) >= 2):
        should_burn = True

    if should_burn:
        strikes = strikes + 1 if strikes > 0 else 1
        burn_start = time.time()

    cursor.execute("""
    INSERT INTO source_reputation (domain, upvotes, downvotes, score, burn_strikes, burn_start, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    ON CONFLICT(domain) DO UPDATE SET
        upvotes = excluded.upvotes,
        downvotes = excluded.downvotes,
        score = excluded.score,
        burn_strikes = excluded.burn_strikes,
        burn_start = excluded.burn_start,
        updated_at = datetime('now');
    """, (clean_domain, up, down, new_score, strikes, burn_start))

    conn.commit()
    conn.close()

    invalidate_cache()
    rep = get_source_reputation(clean_domain)
    rep["is_burned"] = is_source_burned(clean_domain)
    return rep


def _get_item_vote(url: str) -> int:
    """Check if the user has specifically voted on this exact URL."""
    if not url:
        return 0
    if url in _item_votes_cache:
        return _item_votes_cache[url]

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT vote FROM content_votes WHERE url = ? ORDER BY id DESC LIMIT 1", (url,))
    row = cursor.fetchone()
    conn.close()

    vote = row["vote"] if row else 0
    _item_votes_cache[url] = vote
    return vote


def score_content_item(item: dict, now: Optional[float] = None) -> float:
    """Compute the multi-axis quality score for a content item.

    Matches the Wallgarden getScoreAndMatches algorithm:
    - Domain reputation affinity (+3 per upvote, capped at +15)
    - Downvote penalty (-5 per downvote)
    - Heuristics: all-caps (-8), excessive punctuation (-5), clickbait (-12),
      gossip (-15), PR spam (-20), tabloid domain (-25)
    - Active burn: -100.0
    - Item-level override: +25 for liked, -50 for disliked
    """
    url = str(item.get("url") or item.get("link") or "").strip()
    meta = str(item.get("meta") or item.get("source") or item.get("publisher") or "").strip()
    domain = extract_domain(url, meta)

    score = 0.0
    flags = []

    # 1. Source reputation
    rep = get_source_reputation(domain)
    if is_source_burned(domain, now=now):
        score -= 100.0
        flags.append("burned_source")
    else:
        if rep["upvotes"] > 0:
            boost = min(15.0, rep["upvotes"] * 3.0)
            score += boost
            flags.append("liked_source")
        if rep["downvotes"] > 0:
            score -= (rep["downvotes"] * 5.0)
            flags.append("downvoted_source")

    # 2. Heuristic signals
    classification = classify_content_quality(item)
    score -= classification["penalty"]
    flags.extend(classification["flags"])

    # 3. Item-level rating override (Wallgarden +25 / -50)
    item_vote = _get_item_vote(url)
    if item_vote > 0:
        score += 25.0
        flags.append("user_upvoted")
    elif item_vote < 0:
        score -= 50.0
        flags.append("user_downvoted")

    item["_quality_score"] = round(score, 2)
    item["_quality_flags"] = flags
    item["_quality_class"] = classification["quality_class"]
    return score


def rank_and_filter_content_items(items: list, now: Optional[float] = None) -> list:
    """Score, sort (best first), and filter burned sources unless card would be empty."""
    if not items:
        return []

    scored_items = []
    for it in items:
        if isinstance(it, dict):
            score_content_item(it, now=now)
            scored_items.append(it)

    # Filter out actively burned items, but fail open (if all burned, keep items)
    unburned = [it for it in scored_items if "burned_source" not in it.get("_quality_flags", [])]
    pool = unburned if unburned else scored_items

    # Sort descending by score
    pool.sort(key=lambda x: x.get("_quality_score", 0.0), reverse=True)
    return pool


def get_burned_sources(now: Optional[float] = None) -> list:
    """List all domains currently serving a burn sentence."""
    current_time = now if now is not None else time.time()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM source_reputation WHERE burn_start IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    burned = []
    for row in rows:
        strikes = max(1, row["burn_strikes"] or 1)
        sentence = BURN_PAROLE_BASE_SECONDS * (2 ** (strikes - 1))
        remaining = sentence - (current_time - row["burn_start"])
        if remaining > 0:
            burned.append({
                "domain": row["domain"],
                "burn_strikes": strikes,
                "burn_start": row["burn_start"],
                "remaining_days": max(1, int(remaining // 86400)),
            })
    return burned


def get_quality_profile() -> dict:
    """Aggregate overview of the user's learned quality profile."""
    conn = get_connection()
    cursor = conn.cursor()

    # Total votes count
    cursor.execute("SELECT COUNT(*) as cnt FROM content_votes")
    total_votes = cursor.fetchone()["cnt"]

    # Top trusted sources (upvotes > 0, not currently burned)
    cursor.execute("""
    SELECT domain, upvotes, downvotes, score
    FROM source_reputation
    WHERE upvotes > 0
    ORDER BY score DESC, upvotes DESC
    LIMIT 10;
    """)
    trusted = [dict(r) for r in cursor.fetchall()]

    # Recent vote history
    cursor.execute("""
    SELECT id, url, domain, publisher, title, vote, created_at
    FROM content_votes
    ORDER BY id DESC
    LIMIT 20;
    """)
    recent = [dict(r) for r in cursor.fetchall()]
    conn.close()

    burned = get_burned_sources()
    return {
        "total_votes": total_votes,
        "trusted_sources": trusted,
        "burned_sources": burned,
        "recent_votes": recent,
    }


def reset_quality_profile() -> dict:
    """Clear all votes and source reputations."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM content_votes;")
    cursor.execute("DELETE FROM source_reputation;")
    conn.commit()
    conn.close()
    invalidate_cache()
    return {"status": "reset", "total_votes": 0}


def synthesize_multi_widget_takeaway(placed: list, message: str = "") -> Optional[str]:
    """Generates an editorial synthesis for multi-query or multi-widget responses.

    Instead of blindly parroting headlines or stringing together disconnected summaries,
    identifies the single highest-signal takeaway across all committed widgets:
    "Here's the data. From what we pulled, this is what I think you should be focusing on: <takeaway>."
    """
    if not placed or len(placed) < 2:
        return None

    # Gather items and answers across all placed widgets
    scored_candidates = []
    text_answers = []

    for item_tuple in placed:
        if not isinstance(item_tuple, (list, tuple)) or len(item_tuple) < 3:
            continue
        _rid, wtype, wcfg = item_tuple[0], item_tuple[1], item_tuple[2]
        if not isinstance(wcfg, dict):
            continue

        # Look for explicit answers / briefs
        ans = (wcfg.get("answer") or wcfg.get("overview") or wcfg.get("summary") or "").strip()
        if ans:
            first_sentence = re.split(r"(?<=[.!?])\s+", ans)[0].strip()
            first_sentence = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", first_sentence)
            first_sentence = re.sub(r"[*_`#>\[\]()]", "", first_sentence).strip()
            if first_sentence:
                text_answers.append((wcfg.get("title", ""), first_sentence))

        # Look for structured items
        items = wcfg.get("items") or wcfg.get("sources") or []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if isinstance(it, dict) and it.get("title"):
                score = it.get("_quality_score", 0.0)
                scored_candidates.append((score, it))

    # Pick the most prominent takeaway
    focus_story = ""
    if scored_candidates:
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        top_it = scored_candidates[0][1]
        top_title = (top_it.get("title") or "").strip()
        top_desc = (top_it.get("description") or "").strip()
        top_title = re.sub(r"[*_`#>\[\]()]", "", top_title).strip()
        if top_desc:
            first_desc = re.split(r"(?<=[.!?])\s+", top_desc)[0].strip()
            first_desc = re.sub(r"[*_`#>\[\]()]", "", first_desc).strip()
            if len(first_desc) > 10 and not first_desc.lower().startswith(top_title[:15].lower()):
                focus_story = f"{top_title} — {first_desc}"
            else:
                focus_story = top_title
        else:
            focus_story = top_title
    elif text_answers:
        focus_story = text_answers[0][1]

    if not focus_story:
        first_cfg = placed[0][2] if isinstance(placed[0][2], dict) else {}
        focus_story = first_cfg.get("title") or "the key findings"

    if len(focus_story) > 160:
        focus_story = focus_story[:160].rsplit(" ", 1)[0] + "…"

    if not focus_story.endswith((".", "!", "?")):
        focus_story += "."

    return f"Here's the data. From what we pulled, this is what I think you should be focusing on: {focus_story}"


# Ensure tables exist on module load
try:
    init_content_quality_tables()
except Exception:
    pass

