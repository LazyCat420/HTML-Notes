import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

try:
    from app.services.search import _enrich_news as _fallback_enrich_news, news_search as _fallback_news_search, web_search as _fallback_web_search
except Exception:
    _fallback_enrich_news = None
    _fallback_news_search = None
    _fallback_web_search = None

try:
    from app.llm import fast_llm_json as _fallback_fast_llm_json
except Exception:
    _fallback_fast_llm_json = None

def pick_theme(text: str) -> Optional[str]:
    """Resolve a free-text appearance request to the closest catalog theme name.

    Scores each theme by how many request tokens hit its keywords (fuzzy, so
    "forset"/"pastal" still land), with an exact theme-name mention winning
    outright. A light/dark hint ("light mode", "dark") breaks ties toward the
    matching family. Returns None when nothing scores — the caller then just
    opens settings without changing the theme, letting the user pick."""
    if not text:
        return None
    low = text.lower()
    for name in _THEME_NAMES:
        if re.search(rf"\b{name}\b", low):
            return name
    toks = [w for w in re.findall(r"[a-z]+", low)
            if w not in _THEME_STOP and len(w) > 2]
    if not toks:
        return None
    best, best_score = None, 0.0
    for t in THEME_CATALOG:
        kw = set()
        for k in t["keywords"]:
            kw.update(k.split())
        score = sum(1 for w in toks if _fuzzy_hit(w, kw))
        # A family hint is a light nudge, never enough on its own.
        if ("light" in toks or "bright" in toks or "day" in toks) and not t["dark"]:
            score += 0.5
        if ("dark" in toks or "night" in toks) and t["dark"]:
            score += 0.5
        if score > best_score:
            best, best_score = t["name"], score
    return best if best_score > 0 else None


def cache_tool_result(key: str, value: dict) -> None:
    now = time.time()
    for k, (stamp, _) in list(_tool_results.items()):
        if now - stamp > _TOOL_RESULT_TTL:
            _tool_results.pop(k, None)
    _tool_results[key.lower().strip()] = (now, value)


def get_cached_tool_result(key: str) -> Optional[dict]:
    entry = _tool_results.get((key or "").lower().strip())
    if not entry:
        return None
    stamp, value = entry
    return value if time.time() - stamp <= _TOOL_RESULT_TTL else None


def cached_stock_symbols() -> list:
    """Every ticker whose snapshot we fetched recently, newest first."""
    now = time.time()
    hits = [
        (stamp, value.get("symbol"))
        for key, (stamp, value) in _tool_results.items()
        if key.startswith("stock:") and now - stamp <= _TOOL_RESULT_TTL
        and isinstance(value, dict) and value.get("symbol")
    ]
    return [symbol for _, symbol in sorted(hits, reverse=True)]


def _graceful_fallback_config(config: dict) -> dict:
    """A readable 'couldn't load this' card — never a raw key dump or blank."""
    topic = ""
    for k in ("news_topic", "topic", "search_query", "map_query", "query",
              "symbol", "ticker", "title"):
        v = config.get(k) if isinstance(config, dict) else None
        if v:
            topic = str(v).strip()
            break
    icon = (config.get("icon") if isinstance(config, dict) else None) or "info"
    return {
        "title": topic.title() if topic else "No results",
        "icon": icon,
        "content": (f"Couldn't pull up **{topic or 'that'}** right now — the "
                    f"source came back empty. Try rephrasing, or ask again in a moment."),
    }


def _data_card_quality_gap(config: dict) -> str:
    """SYNC classifier for the two quality symptoms. Returns 'bare_links' (a
    list-mode card whose linked items mostly have no summary), 'no_sources' (an
    answer card with no supporting items), or '' (fine). Used to decide whether
    the async enrichment pass below is worth running."""
    if not isinstance(config, dict):
        return ""
    # `content` counts as an answer here for the same reason render_data_card
    # treats it as one: the MCP tool schema documents `content` as the prose key
    # while the SYSTEM_PROMPT says `answer`, so the model uses either. Reading
    # only `answer` made a content-bearing card look prose-less, so the floor
    # would try to "repair" a card that was already fine.
    answer = ((config.get("answer") or "") or (config.get("content") or "")).strip()
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    if answer and not items:
        return "no_sources"
    if not answer and items:
        linky = [it for it in items if isinstance(it, dict)
                 and (it.get("url") or it.get("link"))]
        bare = [it for it in linky if not (it.get("description")
                or it.get("summary") or it.get("snippet"))]
        # ANY bare link is unacceptable: "if it shows links it must summarise them".
        # (Was ">= half"; a single naked link is still a wall-of-links to the user.)
        if linky and bare:
            return "bare_links"
    return ""


def _linky_items(config: dict) -> list:
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    return [it for it in items if isinstance(it, dict) and (it.get("url") or it.get("link"))]


def _bare_items(items: list) -> list:
    return [it for it in items if not (it.get("description")
            or it.get("summary") or it.get("snippet"))]


async def _synthesize_answer_from_items(config: dict, query_hint: str = "") -> str:
    """Last-resort prose so a card is NEVER just links: write a short overview from
    the item titles/descriptions we already have. Faithful (no new facts beyond the
    rows) and cheap. Returns '' only if the model call fails."""
    items = _linky_items(config)
    if not items:
        return ""
    rows = "\n".join(
        f'- {it.get("title","")}'
        + (f': {(it.get("description") or it.get("summary") or it.get("snippet") or "")[:160]}'
           if (it.get("description") or it.get("summary") or it.get("snippet")) else "")
        for it in items[:8])
    q = (query_hint or config.get("title") or "").strip()
    llm_fn = getattr(main, "fast_llm_json", None) or _fallback_fast_llm_json
    if not llm_fn:
        return ""
    data = await llm_fn(
        "You write a 2-3 sentence plain-language overview that ties together the "
        "items below into a direct answer. Use ONLY what the items state — invent "
        "no facts. Return ONLY JSON: {\"answer\": \"...\"}\n\n"
        + (f"QUESTION: {q}\n\n" if q else "") + "ITEMS:\n" + rows,
        max_tokens=400)
    if isinstance(data, dict):
        return (data.get("answer") or "").strip()
    return ""


def _favicon_for(url: str) -> str:
    """Google's favicon service for any host. A site's own mark is a far better
    tile than a monogram letter — it identifies the SOURCE at a glance — and it
    resolves for essentially every real domain, which is what makes near-total
    image coverage cheap. Was previously built only inside the Google-News RSS
    path; it generalises to any URL."""
    try:
        host = urllib.parse.urlparse(url).netloc
    except Exception:
        return ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128" if host else ""


def _items_of(config: dict) -> list:
    """items/sources, normalised to a list of dicts."""
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    return [it for it in items if isinstance(it, dict)]


def _items_missing_images(config: dict) -> list:
    """Linked items with no picture. These are what the backfill targets."""
    return [it for it in _items_of(config)
            if (it.get("url") or it.get("link"))
            and not (it.get("image") or it.get("thumbnail"))]


async def _backfill_item_images(items: list, timeout: float = 6.0) -> int:
    """Give every linked item a picture: real og:image where the page has one,
    the site's favicon otherwise.

    Image population used to be a SIDE EFFECT of description repair — it ran only
    on items that were also missing a summary, and only when a quality gap had
    already fired. A well-written card with good descriptions and no pictures was
    therefore 'fine' by the floor and rendered as a column of monogram letters.
    Pictures are now their own concern, applied to any linked item that lacks one.
    """
    targets = [it for it in items
               if (it.get("url") or it.get("link"))
               and not (it.get("image") or it.get("thumbnail"))]
    if not targets:
        return 0
    probe = [{"url": it.get("url") or it.get("link"), "snippet": "x", "image": ""}
             for it in targets]
    try:
        # snippet is pre-filled above: _enrich_news only fills falsy fields, so a
        # non-empty snippet keeps this pass strictly about images and stops it
        # overwriting descriptions the card already has.
        enrich_fn = getattr(main, "_enrich_news", None) or _fallback_enrich_news
        if enrich_fn:
            await asyncio.wait_for(enrich_fn(probe, timeout=timeout), timeout=timeout + 1.0)
    except (asyncio.TimeoutError, Exception):
        pass  # best-effort; the favicon fallback below still applies
    filled = 0
    for it, p in zip(targets, probe):
        img = (p.get("image") or "").strip() or _favicon_for(it.get("url") or it.get("link"))
        if img:
            it["image"] = img
            filled += 1
    return filled


async def _ensure_data_card_quality(config: dict, query_hint: str = "") -> dict:
    """Quality floor for data_cards (async — enriches, so not usable inside the
    sync render path). Guarantees the user never gets a wall of bare links or a
    sourceless answer, no matter how the config arrived (esp. the agent path,
    where a hand-built config bypasses every rehydration branch).

    - bare_links: backfill each summary-less linked item from its og:description
      (cheap meta fetch); any still-bare rows get one faithful LLM one-liner pass;
      if rows are STILL bare after that, synthesize a top-level answer so the card
      renders as prose-over-sources rather than naked links.
    - no_sources: fetch a few real sources for the answer's subject and attach.
    Fails SAFE, not open: on any error a card with links but no summary at least
    gets a minimal answer stitched from its titles, so links never render alone.
    """
    gap = _data_card_quality_gap(config)
    # Missing pictures are a gap in their OWN right. Returning early on `not gap`
    # meant a card with solid descriptions but no images skipped enrichment
    # entirely and rendered as a column of monogram letters.
    if not gap:
        if _items_missing_images(config):
            n = await _backfill_item_images(_items_of(config))
            if n:
                logger.info(f"[QUALITY] backfilled {n} item image(s) on an otherwise-fine card")
        return config
    try:
        if gap == "bare_links":
            items = config.get("items") or config.get("sources") or []
            if isinstance(items, dict):
                items = [items]
            bare = [it for it in items if isinstance(it, dict)
                    and (it.get("url") or it.get("link"))
                    and not (it.get("description") or it.get("summary") or it.get("snippet"))]
            enrich = [{"url": it.get("url") or it.get("link"), "snippet": "",
                       "image": it.get("image", "")} for it in bare]
            try:
                enrich_fn = getattr(main, "_enrich_news", None) or _fallback_enrich_news
                if enrich_fn:
                    await asyncio.wait_for(enrich_fn(enrich, timeout=6.0), timeout=7.0)
            except asyncio.TimeoutError:
                pass
            for it, e in zip(bare, enrich):
                if e.get("snippet"):
                    it["description"] = e["snippet"][:400]
                if not it.get("image") and e.get("image"):
                    it["image"] = e["image"]
            still = [it for it in bare if not it.get("description")]
            if still:
                lines = "\n".join(f'[{i}] {it.get("title","")}' for i, it in enumerate(still))
                llm_fn = getattr(main, "fast_llm_json", None) or _fallback_fast_llm_json
                data = None
                if llm_fn:
                    data = await llm_fn(
                        'You are a news editor. For each headline below write a faithful '
                        'one-sentence summary of what it is about — do NOT invent facts '
                        'beyond the headline. Return ONLY JSON: '
                        '{"items":[{"index":<N>,"summary":"..."}]}\n\nHEADLINES:\n' + lines,
                        max_tokens=600)
                if data and isinstance(data.get("items"), list):
                    by_i = {d.get("index"): (d.get("summary") or "").strip()
                            for d in data["items"] if isinstance(d.get("index"), int)}
                    for i, it in enumerate(still):
                        if by_i.get(i):
                            it["description"] = by_i[i][:300]
            config["items"] = items
            # Backstop: if any row is STILL bare (enrichment + editor both failed),
            # synthesize a top-level answer so the card is prose-over-sources, never
            # a naked link list. Only when there's no answer already.
            if _bare_items(_linky_items(config)) and not (config.get("answer") or "").strip():
                ans = await _synthesize_answer_from_items(config, query_hint)
                if ans:
                    config["answer"] = ans
                    logger.info("[QUALITY] synthesized a summary for an otherwise link-only card")
        elif gap == "no_sources":
            q = (query_hint or config.get("title") or "").strip()
            if q:
                # Cite what the answer was actually WRITTEN FROM.
                hits = get_cached_tool_result(f"search:{q}")
                origin = "cached web_search"
                if not hits:
                    searcher = (getattr(main, "news_search", None) or _fallback_news_search) if NEWS_ASK_RE.search(q) else (getattr(main, "web_search", None) or _fallback_web_search)
                    origin = getattr(searcher, "__name__", "search")
                    try:
                        if searcher:
                            hits = await asyncio.wait_for(searcher(q, limit=5), timeout=8.0)
                    except asyncio.TimeoutError:
                        hits = []
                hits = (hits or [])[:5]
                # These are UNVETTED search hits about to be attached to a card
                # as "Source". Nothing used to check they had anything to do with
                # the answer, which is a direct route to "the card gained sources
                # that have nothing to do with it". Drop the ads, then put them
                # through the same relevance gate the news card uses — both fail
                # open, so a grading outage still attaches what it found.
                if hits:
                    dropper = getattr(main, "_drop_pr_spam", None)
                    if dropper:
                        hits = dropper(hits)
                    gate = getattr(main, "filter_items_by_relevance", None)
                    if gate and q:
                        try:
                            hits = await asyncio.wait_for(
                                gate(q, [], hits, min_keep=1), timeout=8.0)
                        except asyncio.TimeoutError:
                            pass
                if hits:
                    try:
                        enrich_fn = getattr(main, "_enrich_news", None) or _fallback_enrich_news
                        if enrich_fn:
                            await asyncio.wait_for(enrich_fn(hits, timeout=5.0), timeout=6.0)
                    except asyncio.TimeoutError:
                        pass
                srcs = [{"title": (h.get("title") or "")[:120],
                         "description": (h.get("snippet") or "")[:240],
                         "url": h.get("url", ""), "image": h.get("image", ""),
                         "meta": h.get("publisher") or _host_of(h.get("url", "")),
                         "badge": "Source"}
                        for h in hits if h.get("url")][:5]
                if srcs:
                    config["items"] = srcs
                    logger.info(f"[QUALITY] attached {len(srcs)} sources "
                                f"({origin}) to a sourceless answer for {q!r}")
    except Exception as e:
        logger.warning(f"_ensure_data_card_quality ({gap}) failed: {e}")
    # Fail SAFE: whatever happened above, a card that still shows links with neither
    # a top-level answer nor any per-item description must not go out naked. Give the
    # bare rows a minimal honest description derived from their own title.
    try:
        if not (config.get("answer") or "").strip():
            for it in _bare_items(_linky_items(config)):
                t = (it.get("title") or "").strip()
                if t:
                    it["description"] = t
    except Exception:
        pass
    # Final image sweep. The gap branches above fill images only for the subset
    # they happened to touch (bare items, or freshly-fetched sources), so anything
    # they left alone still lands here and gets at least a favicon.
    try:
        if _items_missing_images(config):
            n = await _backfill_item_images(_items_of(config))
            if n:
                logger.info(f"[QUALITY] backfilled {n} item image(s) after {gap} repair")
    except Exception as e:
        logger.warning(f"[QUALITY] image backfill failed: {e}")
    return config


def _tool_repeat_key(tool_name: str, args) -> str:
    """Stable key for 'the same call again'. Args are canonicalized so key order
    can't disguise a repeat as a new call."""
    try:
        blob = json.dumps(args, sort_keys=True, default=str)[:400]
    except Exception:
        blob = str(args)[:400]
    return f"{tool_name}|{blob}"


def _summarize_tool_args(args) -> dict:
    """The one or two argument values worth showing a human, small enough to
    send with every tool_call.

    The browser has always read `data.args` on this event — it was the server
    that never sent it, so a research turn printed the bare MCP tool name and
    nothing about what was actually being searched for."""
    if not isinstance(args, dict):
        return {}
    out = {}
    for key in _TOOL_DETAIL_KEYS:
        val = args.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        out[key] = val.strip()[:_MAX_DETAIL_CHARS]
        if len(out) >= 2:
            break
    return out


def _phase_for_tool(tool_name: str) -> str:
    """Phase implied by a tool call. Split on the MCP prefix rather than a
    prefix-strip so a bare name (the fork serves some tools unprefixed) keys the
    same as its mcp__lazy-tool-service__ form."""
    return _TOOL_PHASES.get((tool_name or "").rsplit("__", 1)[-1], _PHASE_RESEARCHING)


def _strip_agent_narration(text: str) -> str:
    """Drop the model's process commentary and stray ellipsis filler, leaving the
    substantive answer (if any). Used only by the no-widget fallback card, where
    the alternative is showing narration to the user as though it were the result.

    Observed verbatim on a live research turn: after 18 successful research calls
    the whole reply was `"... ... ... Now I have comprehensive data from three
    major review sources. Let me build a data_card with the best sandals."` —
    every word of it narration, and not one word of the answer it had gathered.
    That must become the honest "couldn't answer" card, not a card whose body is
    a promise to make a card."""
    body = (text or "").strip()
    # Runs of "..." are what a tool-only turn streams between calls.
    body = re.sub(r"(?:^|\s)\.{2,}(?=\s|$)", " ", body)
    kept = [
        s for s in re.split(r"(?<=[.!?])\s+", body)
        if s.strip()
        and not _NARRATION_SENTENCE_RE.match(s)
        and not _TOOL_TALK_RE.search(s)
    ]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def _text_answer_card_config(question: str, answer: str) -> dict:
    """A data_card carrying a prose answer, for the agent turn that answered but
    never touched the canvas. The alternative was a blank canvas plus a spoken
    answer — the failure that reads as "it called tools but no widget appeared".

    Titled from the QUESTION rather than the answer: the question is short and
    already scoped, whereas the answer's first line is often a full sentence.

    When the reply is too thin to BE an answer, say so plainly instead of
    rendering a card whose body is "...". Observed live with the MCP research
    tools down: the agent streamed 5 characters and committed nothing."""
    q = re.sub(r"\s+", " ", (question or "").strip())
    title = (q[:57] + "...") if len(q) > 60 else (q or "Answer")
    body = _strip_agent_narration(answer)
    if len(body) < _MIN_ANSWER_CHARS:
        return {
            "title": title[:60],
            "answer": ("I couldn't put together an answer for this one — the "
                       "research tools didn't return anything usable.\n\n"
                       "Try rephrasing, or ask something more specific."),
            "source_note": "no result — the agent finished without an answer",
        }
    return {
        "title": title[:60],
        "answer": body[:4000],
        "source_note": "answered without a canvas tool — rendered from the reply",
    }


def _clean_fact(s: str) -> str:
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    s = re.split(r'\b(?:and|but|so|because|although|though|please)\b', s, maxsplit=1)[0]
    # Trailing temporal/filler ("Boston now", "Seattle currently") isn't part of
    # the fact.
    s = re.sub(r'\s+(now|today|currently|these days|right now|at the moment|anymore)\s*$',
               '', s, flags=re.I)
    s = s.strip(" .,'\"!?")
    return (s.title() if s.islower() else s)[:60]


def capture_user_facts(message: str) -> dict:
    """Persist first-person profile facts from a message. Returns {key: value} for
    whatever was captured (empty if nothing). Overwrites in place (a new city
    replaces the old)."""
    captured = {}
    for rx in (_NAME_CAP_RE, _CALLME_RE):
        mm = rx.search(message or "")
        if mm:
            name = _clean_fact(mm.group(1))
            if name:
                database.set_user_fact("name", name)
                captured["name"] = name
            break
    mm = _FROM_CAP_RE.search(message or "")
    if mm:
        loc = _clean_fact(mm.group(1))
        if loc:
            database.set_user_fact("location", loc)
            captured["location"] = loc
    mm = _LIKE_CAP_RE.search(message or "")
    if mm:
        like = _clean_fact(mm.group(1))
        if like:
            facts = database.get_user_facts()
            existing = [x.strip() for x in facts.get("likes", "").split(",") if x.strip()]
            if like.lower() not in [e.lower() for e in existing]:
                existing.append(like)
                database.set_user_fact("likes", ", ".join(existing[:12]))
            captured["likes"] = like
    return captured


def _user_facts_prompt() -> str:
    """An 'ABOUT THE USER' line for the agent system prompt, or '' if unknown."""
    facts = database.get_user_facts()
    parts = []
    if facts.get("name"):
        parts.append(f"their name is {facts['name']}")
    if facts.get("location"):
        parts.append(f"they are based in {facts['location']}")
    if facts.get("likes"):
        parts.append(f"they like {facts['likes']}")
    if not parts:
        return ""
    return ("ABOUT THE USER: " + "; ".join(parts) + ". Use this when relevant — "
            "default any location request (weather, maps, 'near me') to their city "
            "unless they name another place.\n\n")


def _strip_citation_markers(text: str) -> str:
    return _CITATION_RE.sub("", text or "").strip()


async def _noop_dict() -> dict:
    """An awaitable {} — lets asyncio.gather keep a fixed shape when one branch
    (CoinGecko description) is skipped for a DexScreener-sourced token."""
    return {}


def _fmt_usd(v) -> str:
    """Compact USD for stat chips: $1.2B, $3.4M, $0.0000029."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n == 0:
        return "$0"
    a = abs(n)
    if a >= 1e12:
        return f"${n/1e12:.2f}T"
    if a >= 1e9:
        return f"${n/1e9:.2f}B"
    if a >= 1e6:
        return f"${n/1e6:.2f}M"
    if a >= 1e3:
        return f"${n/1e3:.1f}K"
    if a >= 1:
        return f"${n:,.2f}"
    # Sub-dollar memecoin prices: keep the first significant digits.
    return f"${n:.8f}".rstrip("0").rstrip(".")


async def _image_url_loads(url: str) -> bool:
    """Does this URL actually serve an image?

    Only ever used on a URL the MODEL supplied. The agent has no image-search
    tool, so such a URL is recalled, not looked up, and a plausible-looking one
    is frequently dead (observed: a Wikimedia thumb path for "red panda" that
    404s at the CDN and returns 400). An <img> with a dead src is indis-
    tinguishable from a good one in the DOM, so check the bytes.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=6.0) as client:
            r = await client.head(url)
            # Some CDNs refuse HEAD; fall back to a ranged GET before believing it.
            if r.status_code >= 400:
                r = await client.get(url, headers={"Range": "bytes=0-2047"})
            return (r.status_code < 400
                    and r.headers.get("content-type", "").lower().startswith("image/"))
    except Exception as e:
        logger.info(f"[WIDGET INJECTOR] image URL check failed for {url[:80]!r}: {e}")
        return False


async def _fetch_secret(name: str) -> str:
    """One secret by name: env var first, then vault-service (bearer token).
    A resolved value is cached for the process lifetime; an empty result is cached
    only for _SECRET_MISS_TTL seconds, so a key added to the vault after boot is
    picked up without a restart. Returns "" when unavailable so callers degrade."""
    if name in _secret_cache:
        return _secret_cache[name]
    if time.time() - _secret_miss_at.get(name, 0.0) < _SECRET_MISS_TTL:
        return ""
    val = os.getenv(name, "") or ""
    if not val and VAULT_SERVICE_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get(f"{VAULT_SERVICE_URL}/secrets",
                                params={"keys": name},
                                headers={"Authorization": f"Bearer {VAULT_SERVICE_TOKEN}"})
                if r.status_code == 200:
                    val = str((r.json() or {}).get(name, "") or "")
        except Exception as e:
            logger.warning(f"[VAULT] fetch {name!r} failed: {e}")
    if val:
        _secret_cache[name] = val
        _secret_miss_at.pop(name, None)
    else:
        _secret_miss_at[name] = time.time()
    return val


def _note_slug(title: str) -> str:
    """A filesystem-safe slug from a note title. No path separators, no dots at
    the ends — so a title can never escape the vault dir."""
    s = re.sub(r"[^\w\s-]", "", (title or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-.")
    return (s or "note")[:80]


def _note_path(slug: str) -> Optional[pathlib.Path]:
    """Resolve a slug to a .md path INSIDE the vault, or None if it would escape."""
    safe = _note_slug(slug)
    if not safe:
        return None
    vault = pathlib.Path(OBSIDIAN_VAULT_DIR).resolve()
    p = (vault / f"{safe}.md").resolve()
    try:
        p.relative_to(vault)   # must stay within the vault
    except ValueError:
        return None
    return p


def _yaml_frontmatter(meta: dict) -> str:
    """Minimal YAML frontmatter Obsidian reads. Hand-rolled (no pyyaml dep):
    title quoted, tags as an inline array, timestamps as ISO strings."""
    def q(v):
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    tags = meta.get("tags") or []
    tags_line = "[" + ", ".join(_note_slug(t) for t in tags if t) + "]"
    lines = ["---",
             f"title: {q(meta.get('title', 'Untitled'))}",
             f"tags: {tags_line}",
             f"created: {meta.get('created', '')}",
             f"updated: {meta.get('updated', '')}",
             "source: html-notes",
             "---", ""]
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> dict:
    """Pull title/tags/created out of an existing note's frontmatter block, so an
    update preserves `created` and can round-trip the metadata."""
    out = {"title": "", "tags": [], "created": "", "body": text}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return out
    fm, body = m.group(1), m.group(2)
    out["body"] = body
    for line in fm.splitlines():
        km = re.match(r"\s*(\w+)\s*:\s*(.*)$", line)
        if not km:
            continue
        key, val = km.group(1), km.group(2).strip()
        if key == "title":
            if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            out["title"] = val
        elif key == "created":
            out["created"] = val.strip('"')
        elif key == "tags":
            inner = val.strip("[]")
            out["tags"] = [t.strip().strip('"') for t in inner.split(",") if t.strip()]
    return out


@app.post("/tts/synthesize")
async def tts_synthesize(req: TTSSynthesizeRequest):
    """
    Proxies TTS synthesis request to tts-service.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{TTS_SERVICE_URL}/api/v1/tts/synthesize",
                json={"text": req.text}
            )
            if resp.status_code != 200:
                logger.error(f"TTS service returned status code {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=503, detail="TTS service failure")
            return Response(content=resp.content, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Failed proxying to TTS service: {e}")
        raise HTTPException(status_code=503, detail=f"TTS service unavailable: {str(e)}")


__all__ = [k for k in globals().keys() if not k.startswith('__')]
