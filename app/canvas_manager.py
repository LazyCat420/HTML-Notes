import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

def coerce_widget_type(widget_type: str, widget_id: str, config: dict) -> tuple:
    """Send a ticker to stock_card even when the model asked for 'chart'.

    'chart' is a bare Chart.js line — no price header, no range tabs, no technicals,
    no fundamentals. 'chart' and 'stock_card' sit next to each other in one enum, so
    which one a ticker gets was decided by sampling, and the answer changed between
    identical requests. Both the tool description and the system prompt said "never
    chart a ticker" and neither held, because a rule in prose cannot outvote a token
    distribution. Deciding it here makes it deterministic.

    The model has to call html_notes_stock_history before it can chart a price, so a
    cached snapshot whose symbol appears in the config, title or widget_id is a
    positive ID for the ticker it is trying to draw.
    """
    if widget_type != "chart" or not isinstance(config, dict):
        return widget_type, config

    # A deliberate multi-ticker comparison chart is the one chart a ticker
    # SHOULD get — its title/id name several cached symbols, which is exactly
    # what the positive-ID heuristic below would latch onto. Never coerce it.
    if config.get("compare_symbols") or (
            isinstance(config.get("chart"), dict)
            and len((config["chart"].get("data") or {}).get("datasets") or []) > 1):
        return widget_type, config

    symbol = config.get("symbol") or config.get("ticker") or ""
    if not symbol:
        haystack = f"{widget_id} {config.get('title', '')}".upper()
        symbol = next(
            (s for s in cached_stock_symbols() if s.upper() in haystack),
            "",
        )
    if not symbol:
        return widget_type, config

    logger.info(f"[WIDGET COERCE] chart -> stock_card for {symbol}")
    return "stock_card", {"symbol": str(symbol).upper()}


def _widget_is_degenerate(widget_type: str, config: dict) -> bool:
    """True when a data-ish widget carries a topic/query but no real content, so
    it would render as an empty shell / raw-config dump. Scoped to the widget
    types whose renderers lack their own graceful empty state (weather, map,
    image, checklist, youtube all handle empty results themselves; the new
    widget-pack renderers degrade to a data_card on their own, but a query-only
    config should still fall through to the fallback card)."""
    if not isinstance(config, dict) or widget_type not in (
            "data_card", "stock_card", "scoreboard", "chart", "multi_chart",
            "table", "kpi_row", "timeline", "versus_card", "profile_card",
            "progress"):
        return False
    if any(config.get(k) for k in _CONTENT_KEYS):
        return False
    meaningful = [k for k, v in config.items()
                  if v and k not in ("title", "subtitle", "icon")]
    return not meaningful or all(k in _QUERY_ONLY_KEYS for k in meaningful)


def render_widget(widget_type: str, widget_id: str, config: dict) -> str:
    """Single choke point for widget HTML: alias + coerce the type, then render."""
    widget_type = _WIDGET_TYPE_ALIASES.get(str(widget_type or "").strip().lower(),
                                           widget_type)
    widget_type, config = coerce_widget_type(widget_type, widget_id, config)
    # Universal safety net: a data widget with only a topic/query and no content
    # would render as a raw key/value dump — a card that looks broken. Downgrade
    # it to a readable fallback so the canvas never shows an empty shell, no
    # matter how the config arrived (fast-path, agent, or cache-miss rehydration).
    if _widget_is_degenerate(widget_type, config):
        logger.info(
            "[WIDGET GUARD] degenerate %s (%s) keys=%s -> fallback card",
            widget_type, widget_id,
            list(config.keys()) if isinstance(config, dict) else config)
        widget_type, config = "data_card", _graceful_fallback_config(config)
    return generate_widget_html(widget_type, widget_id, config)


def find_singleton_media_widget(soup, widget_type: str):
    """Return the existing widget-container div for this media type, if any."""
    marker = _MEDIA_WIDGET_MARKERS.get(widget_type)
    if not marker:
        return None
    for div in soup.find_all("div", class_="widget-container"):
        if marker in div.get("x-data", ""):
            return div
    return None


def _stamp_media_seq(node, widget_type: str, req_seq: int) -> None:
    """Stamp a freshly-appended media widget with the request arrival seq so a
    later-committing OLDER request can't overwrite it (see _place_media_widget)."""
    if req_seq and widget_type in _MEDIA_WIDGET_MARKERS:
        root = node.find("div", class_="widget-container")
        if root is not None:
            root["data-req-seq"] = str(req_seq)


def _place_media_widget(soup, widget_type: str, widget_id: str, config: dict, req_seq: int) -> bool:
    """Swap the singleton media widget of this type in place, honouring request
    arrival order. Returns True if it handled the widget (caller should stop),
    False if there is no existing media widget to replace (caller appends)."""
    media_div = find_singleton_media_widget(soup, widget_type)
    if media_div is None:
        return False
    try:
        existing_seq = int(media_div.get("data-req-seq") or 0)
    except (TypeError, ValueError):
        existing_seq = 0
    if req_seq and existing_seq and req_seq < existing_seq:
        # A newer request already placed a media widget here — don't clobber it
        # with this older (slower-committing) request's video/track.
        return True
    existing_id = media_div.get("id", widget_id)
    new_node = BeautifulSoup(render_widget(widget_type, existing_id, config), "html.parser")
    root = new_node.find("div", class_="widget-container")
    if root is not None and req_seq:
        root["data-req-seq"] = str(req_seq)
    media_div.replace_with(new_node)
    return True


def _canvas_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def get_session_canvas(session_id: str) -> str:
    return _session_canvas.get(session_id, "")


def set_session_canvas(session_id: str, html: str, bump_version: bool = True) -> int:
    """Store the canvas and return its version.

    `bump_version=False` stores without minting a new version — for ADOPTING the
    client's own snapshot, where the client already has exactly this HTML. Minting
    a version there desynchronized the client permanently: nothing emits a
    `component` event for an adoption, so the client never learned the new number,
    every later request looked stale (`client_version < server_version`), and its
    snapshots — including the user's widget dismissals — were refused for the rest
    of the session, until a reload reseeded the version from /history."""
    global _last_active_session
    _session_canvas[session_id] = html
    if bump_version or session_id not in _session_canvas_version:
        _session_canvas_version[session_id] = next(_version_counter)
    _last_active_session = session_id
    return _session_canvas_version[session_id]


async def commit_canvas(session_id: str, mutate) -> Optional[str]:
    """Apply `mutate(soup)` to the live canvas under the session lock.

    `mutate` returns False to abort (e.g. its target selector matched nothing).
    Returns a ready-to-send SSE `component` line, or None if the mutation was a
    no-op. Reading the base INSIDE the lock is what lets turns run in parallel:
    each one commits against whatever is on the canvas right now.
    """
    async with _canvas_lock(session_id):
        soup = BeautifulSoup(get_session_canvas(session_id) or "", 'html.parser')
        if mutate(soup) is False:
            return None
        html = str(soup)
        version = set_session_canvas(session_id, html)
    logger.info(f"[CANVAS] committed v{version} ({len(html)} bytes) session={session_id[:8]} "
                f"— emitting component frame to client")
    return f'data: {json.dumps({"type": "component", "content": html, "version": version})}\n\n'


async def _run_turn(session_id: str, current_canvas: str, generator_factory,
                    client_version: Optional[int] = None):
    """Gate a turn on the concurrency semaphore and track it as in-flight.

    While no turn is running for a session, the client's canvas is authoritative
    (it may have dismissed a widget locally), so we adopt its snapshot. Once a
    turn is in flight the server's copy wins — a concurrent turn's client
    snapshot predates whatever just landed.

    `client_version` guards the adoption against a STALE snapshot: the client
    stamps each request with the canvas version its snapshot is based on. Two
    queries fired back-to-back both snapshot the pre-first-widget canvas; if the
    first turn is fast (traffic/weather fast lane) it can fully COMMIT before
    the second turn arrives, so inflight is 0 again — and adopting the second
    snapshot would silently wipe the first turn's widget. If the server's canvas
    is newer than what the snapshot saw, keep the server copy (a version of
    None adopts unconditionally, preserving pre-upgrade client behavior).
    """
    async with _turn_semaphore:
        async with _canvas_lock(session_id):
            if current_canvas and _session_inflight.get(session_id, 0) == 0:
                server_version = _session_canvas_version.get(session_id, 0)
                snapshot_is_stale = (client_version is not None
                                     and get_session_canvas(session_id)
                                     and client_version < server_version)
                if snapshot_is_stale:
                    logger.info(f"[CANVAS] refusing stale client snapshot "
                                f"(client v{client_version} < server v{server_version}) "
                                f"session={session_id[:8]}")
                else:
                    # Adoption only — the client already HAS this exact HTML, so
                    # keep the version it knows about. Minting one here made the
                    # client permanently stale (see set_session_canvas).
                    set_session_canvas(session_id, current_canvas, bump_version=False)
            _session_inflight[session_id] = _session_inflight.get(session_id, 0) + 1
        try:
            async for chunk in generator_factory():
                yield chunk
        finally:
            async with _canvas_lock(session_id):
                _session_inflight[session_id] = max(0, _session_inflight.get(session_id, 0) - 1)


def _classify_canvas_widget(card) -> str:
    """The widget_type of a canvas node, from its stamped type, then its container
    class, then its x-data. Returns 'custom' only for genuinely hand-built widgets.

    `data-widget-type` is checked FIRST and is authoritative: it is stamped by
    generate_widget_html for every widget, whereas the class/x-data maps below only
    cover the types that happen to carry a distinctive marker. `iframe_app` carries
    NEITHER — no type class, no x-data — so a directions map ("San Jose → SF",
    which renders as iframe_app, not map) was classified 'custom'. It then showed
    up in the agent's canvas inventory as `custom`, so when the user said "close
    the map" the one widget that WAS a map by every human measure was the one line
    that didn't say map."""
    stamped = (card.get("data-widget-type") or "").strip()
    if stamped:
        return stamped
    for cls in card.get("class", []):
        if cls in _CANVAS_CLASS_TYPE:
            return _CANVAS_CLASS_TYPE[cls]
    xdata = card.get("x-data", "") or ""
    for marker, wtype in _CANVAS_XDATA_TYPE.items():
        if marker in xdata:
            return wtype
    return "custom"


def _iter_canvas_widgets(html: str):
    """Yield (widget_id, widget_type, title) for every widget on the canvas, most
    recent last (DOM order). Shared by the summary and the reuse lookup."""
    if not html or not html.strip() or html == "Canvas is empty.":
        return
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select(".glass-card, .widget-container"):
        title_el = card.select_one(".glass-card-title, h3, h2, h4")
        title = title_el.get_text(strip=True) if title_el else ""
        yield card.get("id", "unknown"), _classify_canvas_widget(card), title


def find_existing_widget_by_id_prefix(session_id: str, prefix: str) -> Optional[str]:
    """The id of the most-recent widget whose id starts with `prefix`.

    Identity by PREFIX because a traffic ask does not have a stable widget TYPE:
    build_traffic_widget returns `map` when geocoding succeeds and `iframe_app`
    when it misses or the ask is "from A to B". Both are "the traffic map" to the
    user, but type-keyed reuse (SINGLETON_WIDGET_TYPES) can't see across that
    fork, so consecutive traffic asks landing on different sides of it stacked a
    new widget every time — which is exactly what "traffic to san jose" then
    "traffic to sf" then "san ramon to san jose" did. The id prefix is passed by
    the caller as the widget's semantic role, so it survives the type change.
    """
    found = None
    try:
        soup = BeautifulSoup(get_session_canvas(session_id) or "", "html.parser")
        for card in soup.select(".glass-card, .widget-container"):
            wid = card.get("id") or ""
            if wid.startswith(f"{prefix}-"):
                found = wid  # last match wins — the most recently added
    except Exception as e:
        logger.warning(f"find_existing_widget_by_id_prefix failed: {e}")
    return found


def find_existing_widget(session_id: str, widget_type: str) -> Optional[str]:
    """The id of the most-recent widget of `widget_type` on the session canvas, or
    None. Lets a singleton ask (a second map, a refreshed weather) UPDATE the widget
    in place — passing its id back re-renders it rather than stacking a duplicate."""
    found = None
    try:
        for wid, wtype, _title in _iter_canvas_widgets(get_session_canvas(session_id)):
            if wtype == widget_type and wid and wid != "unknown":
                found = wid  # last match wins — the most recently added
    except Exception as e:
        logger.warning(f"find_existing_widget failed: {e}")
    return found


def _score_widget_for_query(query: str, title: str, detail: str = "") -> float:
    """How strongly `query` is about a widget, 0..1.

    Scores against the title AND the ledger's content gist, taking the better of
    the two. Measured on real follow-ups, title alone returns 0.00 on 5 of 7 —
    the subject usually lives in the card BODY ("show me teva instead" vs a card
    titled "Best Sandals"). The two signals are complementary, not redundant:
    "what about cheaper sandals?" scores 0.50/0.00 (title carries it) while
    "under $50" scores 0.00/1.00 (only the detail carries it)."""
    return max(_subject_overlap(query, title),
               _subject_overlap(query, detail))


def find_reuse_target(session_id: str, widget_type: str,
                      message: str = "", subject: str = "") -> Optional[str]:
    """The id of an open widget this ask should UPDATE in place, or None to spawn a
    fresh one. Unifies both reuse policies:
      - SINGLETON_WIDGET_TYPES (map/weather): always reuse the open one.
      - TOPIC_SINGLETON_TYPES (answer cards): reuse only when the ask is a
        follow-up on the open card — deictic phrasing OR a shared subject. A new,
        distinct subject falls through to None and gets its own card.
    Any other type returns None (multiple instances are fine)."""
    if widget_type in SINGLETON_WIDGET_TYPES:
        return find_existing_widget(session_id, widget_type)
    if widget_type not in REUSABLE_WIDGET_TYPES:
        return None
    probe = subject or message or ""
    details = _ledger_details(session_id)
    try:
        candidates = [
            (_score_widget_for_query(probe, title, details.get(wid, "")), order, wid)
            for order, (wid, wtype, title)
            in enumerate(_iter_canvas_widgets(get_session_canvas(session_id)))
            if wtype == widget_type and wid and wid != "unknown"
        ]
    except Exception as e:
        logger.warning(f"find_reuse_target failed: {e}")
        return None
    if not candidates:
        return None
    # Topical match wins. Ties break toward the more recent widget (higher order).
    score, _order, wid = max(candidates, key=lambda c: (c[0], c[1]))
    if score >= _REUSE_SCORE_THRESHOLD:
        return wid
    # No topical signal anywhere. Deictic/narrowing phrasing still means "the
    # thing on screen" ("only under $50" names no subject at all) — fall back to
    # the most recent widget of this type. Recency is the TIEBREAKER, never the
    # rule: a genuinely new subject scores 0, reads as no follow-up, and returns
    # None so it gets its own card.
    #
    # The phrasing check alone is NOT enough for that: _is_refining_followup
    # matches `more\b` anywhere, so "find me MORE info on birkenstock arizona"
    # reads as a refinement — and this fallback then rewrote the (unrelated)
    # most recent card with birkenstock content, even after the model honestly
    # asked for a NEW widget id. A message carrying two or more content words
    # that matched nothing on canvas is a fresh subject, not deixis.
    if _is_refining_followup(message) and len(_subject_tokens(message)) < 2:
        return candidates[-1][2]
    return None


def _remember_widget_config(session_id: str, widget_id: str, config: dict) -> None:
    if not (session_id and widget_id and isinstance(config, dict)):
        return
    _session_widget_configs.setdefault(session_id, {})[widget_id] = config


def _stack_data_card_update(session_id: str, widget_id: str, widget_type: str,
                            config: dict, on_canvas: bool) -> dict:
    """Merge a data_card's previous content under its new content, bounded by
    _STACK_WORD_BUDGET. Returns the (possibly merged) config.

    Stacks only when ALL hold: it's a data_card, the widget is being updated
    in place (already on canvas), we remember what it showed, and the new
    answer doesn't already contain the old one (a model that rewrote WITH
    history must not get it duplicated back). Other widget types are stateful
    displays (stock cards, maps, clocks) — they always replace."""
    prev = _session_widget_configs.get(session_id, {}).get(widget_id)
    if (widget_type != "data_card" or not on_canvas or not isinstance(prev, dict)
            or not isinstance(config, dict)):
        return config
    # A provisional widget (early tool-result preview) is not history to
    # preserve — the final commit is the finished version of the SAME content,
    # so it replaces outright instead of stacking a duplicate underneath.
    if prev.get("provisional"):
        return config
    new_ans = str(config.get("answer") or "").strip()
    old_ans = str(prev.get("answer") or "").strip()
    merged = dict(config)

    if new_ans and old_ans:
        # Substring guard: compare a distinctive slice of the old text.
        old_head = re.sub(r"\s+", " ", old_ans)[:120]
        if old_head and old_head not in re.sub(r"\s+", " ", new_ans):
            room = _STACK_WORD_BUDGET - _word_count(new_ans)
            if room >= _STACK_MIN_KEEP:
                old_words = old_ans.split()
                kept = " ".join(old_words[:room])
                if len(old_words) > room:
                    kept += " …"
                merged["answer"] = new_ans + _EARLIER_RULE + kept
                logger.info(f"[WIDGET STACK] #{widget_id}: kept "
                            f"{min(len(old_words), room)} earlier words under the "
                            f"new answer ({_word_count(merged['answer'])} total)")
            else:
                logger.info(f"[WIDGET STACK] #{widget_id}: new answer fills the "
                            f"budget — replacing outright")

    # Sources/items accumulate too (dedupe by url-or-title, newest first).
    for key in ("items", "sources"):
        new_items = merged.get(key) or []
        old_items = prev.get(key) or []
        if isinstance(new_items, dict):
            new_items = [new_items]
        if isinstance(old_items, dict):
            old_items = [old_items]
        if not (isinstance(new_items, list) and isinstance(old_items, list) and old_items):
            continue
        seen, combined = set(), []
        for it in list(new_items) + list(old_items):
            if not isinstance(it, dict):
                continue
            k = (it.get("url") or it.get("title") or "").strip().lower()
            if k and k in seen:
                continue
            seen.add(k)
            combined.append(it)
        merged[key] = combined[:8]
    return merged


def _spoken_summary(widget_type: str, config: dict, question: str = "") -> str:
    """A single sentence describing what a widget SHOWS, for reading aloud.

    The agent is supposed to write this itself (rule 5 of SYSTEM_PROMPT), but the
    FAST LOOP closes its stream the moment the canvas commits — deliberately, to
    save a slow closing turn — so that sentence usually never arrives and the
    fallback was the canned "Added it to your canvas.", spoken aloud. That told a
    user who wasn't looking at the screen precisely nothing, twice in a row.

    So derive it from the config we just rendered. It is the ANSWER, not the
    filing: never mention widgets, cards or the canvas — the user can see those.
    Returns "" when there is nothing worth saying, so the caller can fall back.
    """
    if not isinstance(config, dict):
        return ""

    def _clip(text: str, limit: int = 220) -> str:
        """First sentence, or a clean truncation — TTS reads this out."""
        text = str(text or "")
        # Spell out symbols that carry meaning aloud. The TTS endpoint strips
        # anything outside [\w\s.,!?\-'":;À-ÿ], so an arrow in a directions title
        # ("San Ramon → San Jose") would otherwise vanish and be read as
        # "San Ramon  San Jose" — two places and no relationship between them.
        text = text.replace("→", " to ").replace("->", " to ").replace("&", " and ")
        # Markdown links: keep the LABEL, drop the URL. Stripping only the
        # brackets left "label(http://x.com)", and a URL read aloud is noise —
        # the listener can't act on it and it buries the actual sentence.
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"[*_`#>\[\]()]", "", text)
        text = re.sub(r"\s+([.,!?;:])", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""
        first = re.split(r"(?<=[.!?])\s+", text)[0]
        if len(first) > limit:
            first = first[:limit].rsplit(" ", 1)[0] + "…"
        return first

    title = (config.get("title") or "").strip()

    if widget_type == "weather":
        loc = (config.get("location") or title or "").strip()
        return f"Here's the forecast for {loc}." if loc else ""

    if widget_type in ("map", "iframe_app"):
        # Traffic and directions maps title themselves "Traffic: San Jose" /
        # "San Ramon → San Jose", which reads well aloud once the label is dropped.
        if title.lower().startswith("traffic:"):
            return f"Live traffic for {_clip(title.split(':', 1)[1])}."
        return f"Showing {_clip(title)}." if title else ""

    if widget_type == "stock_card":
        sym = (config.get("symbol") or title or "").strip()
        return f"Here's {sym}, with its chart and fundamentals." if sym else ""

    if widget_type == "youtube_player":
        return _clip(title) or ""

    if widget_type == "mini_music_player":
        genre = (config.get("genre") or "").strip()
        return f"Playing {genre}." if genre else "Starting the music."

    if widget_type == "scoreboard":
        league = (config.get("league") or title or "").strip()
        return f"Here are the latest {league.upper()} scores." if league else ""

    # Content widgets: the prose IS the answer, so speak the substance of it.
    answer = (config.get("answer") or config.get("content") or "").strip()
    if answer:
        return _clip(answer)

    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    named = [(it.get("title") or "").strip() for it in items if isinstance(it, dict)]
    named = [n for n in named if n]
    if named:
        lead = _clip(named[0], 120)
        if len(named) > 1:
            return f"Found {len(named)}, starting with {lead}."
        return f"Found {lead}."

    spoken = _clip(title)
    # A bare title is a label, not a sentence; punctuate it so TTS doesn't run it
    # into whatever is spoken next.
    if spoken and spoken[-1] not in ".!?":
        spoken += "."
    return spoken


def _widget_detail(config: dict) -> str:
    """A <=200-char gist of what a widget shows, for the ledger — the anaphora +
    PROVENANCE record a follow-up reads. It carries the content gist (so 'what
    about the taco bell one?' ties back to the right widget) and, when the
    builder tagged one, `config['provenance']`: HOW the data was gathered. That
    provenance is what makes a follow-up context-aware — 'why are these
    trending?' can be answered from the momentum data the trending chart was
    built from, instead of a fresh (and empty) per-ticker news lookup."""
    if not isinstance(config, dict):
        return ""
    gist = _widget_content_gist(config)
    prov = str(config.get("provenance") or "").strip()
    if prov:
        # Provenance goes LAST so the content gist (names a follow-up anaphora
        # matches on) is never the part that gets clipped at 200.
        return ((gist + " · " if gist else "") + prov)[:200]
    return gist


def _widget_content_gist(config: dict) -> str:
    """The content half of the ledger detail: the first line of a widget's
    answer, or its top item/story titles.

    The gist is the session's anaphora database, so it must carry the
    DISTINCTIVE NAMES from the whole text, not just a prefix. Live: a sushi
    card's answer truncated at 160 chars exactly before "...include Miku,
    Tojo, and Shizen", so "tell me more about Miku" matched nothing, fell back
    to the recency focus (a video widget), and the directive ordered a YouTube
    search — Hatsune Miku instead of the restaurant."""
    if not isinstance(config, dict):
        return ""
    ans = (config.get("answer") or "").strip()
    if ans:
        flat = re.sub(r"\s+", " ", ans)
        gist = flat[:150]
        # Proper-noun-ish tokens from BEYOND the prefix — the names a
        # follow-up will use. Sentence-initial words are mostly ordinary
        # capitalization, but false positives only cost gist space.
        tail_names = re.findall(r"\b[A-Z][A-Za-z0-9'&-]{2,}\b", flat[150:])
        fresh = [n for n in dict.fromkeys(tail_names)
                 if n.lower() not in gist.lower()][:8]
        if fresh:
            gist += " · " + " ".join(fresh)
        return gist[:200]
    items = config.get("items") or config.get("sources") or []
    if isinstance(items, dict):
        items = [items]
    titles = [(it.get("title") or "").strip() for it in items
              if isinstance(it, dict)][:3]
    titles = [t for t in titles if t]
    if titles:
        return " · ".join(titles)[:160]
    return (config.get("subtitle") or config.get("title") or "").strip()[:160]


def record_turn(session_id: str, message: str, route: str, widgets: list) -> None:
    """Append one turn to the session ledger. `widgets` is a list of
    (widget_id, widget_type, subject, detail) tuples for what the turn produced
    (empty for a turn that only cleared/answered). Best-effort — never raises."""
    if not session_id:
        return
    try:
        entry = {
            "message": (message or "").strip()[:200],
            "route": route or "",
            "widgets": [{"id": wid, "type": wt, "subject": (subj or "")[:80],
                         # 200 matches _widget_detail's budget — clipping back
                         # to 160 here silently re-amputated the appended names.
                         "detail": (det or "")[:200]}
                        for (wid, wt, subj, det) in widgets if wid],
        }
        led = _session_turn_ledger.setdefault(session_id, [])
        led.append(entry)
        if len(led) > _LEDGER_MAX_TURNS:
            del led[:-_LEDGER_MAX_TURNS]
    except Exception as e:
        logger.warning(f"record_turn failed: {e}")


def _summarize_canvas_for_history(canvas_html: str) -> str:
    """A prior assistant turn's canvas HTML -> a factual one-line summary naming
    each widget and its ID, for the agent's message history.

    NOT a prose placeholder. The previous "[Visual Component Rendered]" string
    looked like a legitimate text answer, so on the next turn the model copied it
    verbatim and called no tools at all — the canvas never changed. Naming the
    widgets (a) makes it obvious a TOOL produced this, not prose, and (b) hands
    the model the exact id to reuse when a follow-up refines what's on screen."""
    try:
        soup = BeautifulSoup(canvas_html or "", "html.parser")
        parts = []
        for w in soup.select(".widget-container"):
            wid = w.get("id") or "?"
            wtype = (w.get("data-widget-type") or "widget")
            head = w.select_one("h1,h2,h3,.widget-title")
            title = head.get_text(strip=True)[:60] if head else ""
            parts.append(f'{wtype}#{wid}' + (f' "{title}"' if title else ""))
        if not parts:
            return "[tool call rendered the canvas]"
        return ("[tool call rendered the canvas — now showing: "
                + "; ".join(parts[:6]) + "]")
    except Exception:
        return "[tool call rendered the canvas]"


def build_turn_context(session_id: str, current_canvas: str = "") -> dict:
    """The shared awareness bundle every routing tier reads: what's on the canvas
    now, what recent turns built (with a content gist), and the focus widget (the
    most recent one produced). Returns {inventory, context_block, focus_id}."""
    canvas_html = get_session_canvas(session_id) or current_canvas or ""
    inventory = get_canvas_summary(canvas_html)
    led = _session_turn_ledger.get(session_id, [])[-6:]
    lines = []
    for e in led:
        wpart = ", ".join(
            f'{w["type"]} #{w["id"]}' + (f' — {w["detail"]}' if w["detail"] else "")
            for w in e["widgets"]) or "(no widget)"
        lines.append(f'- "{e["message"]}" → {wpart}')
    ledger_text = "\n".join(lines)
    focus_id = None
    for e in reversed(led):
        if e["widgets"]:
            focus_id = e["widgets"][-1]["id"]
            break
    if not focus_id:
        # The ledger is in-memory, so a container restart loses it while the
        # canvas survives (the client resends current_canvas). focus_id going
        # None silently disabled BOTH the follow-up directive and the user-message
        # rewrite, so follow-ups behaved differently before/after a restart with
        # identical on-screen state. Recover from the canvas: last widget in DOM
        # order is the most recently added.
        try:
            widgets = [wid for wid, _t, _ti in _iter_canvas_widgets(canvas_html)
                       if wid and wid != "unknown"]
            focus_id = widgets[-1] if widgets else None
        except Exception as e:
            logger.warning(f"focus_id canvas fallback failed: {e}")
    # CURRENT CANVAS goes FIRST: route_with_llm truncates this block to 1200
    # chars, and the widget ids are the part the model cannot invent — losing
    # them to truncation while still being told "never invent a widget id"
    # guaranteed a duplicate. History is the expendable half.
    # TODAY line FIRST: it feeds both the tier-2 classifier (which truncates
    # this block to 1200 chars) and the tier-3 agent prompt — without it neither
    # model knows the date, so "new"/"this week" asks and any date reasoning ran
    # on the model's training-cutoff prior.
    today = datetime.date.today()
    block = (f"TODAY: {today.isoformat()} ({today:%A})\n\n"
             "CURRENT CANVAS:\n" + inventory)
    if ledger_text:
        block += ("\n\nRECENT TURNS (oldest first — reuse these widget ids for "
                  "follow-ups):\n" + ledger_text)
    return {"inventory": inventory, "context_block": block, "focus_id": focus_id}


def _widget_showing(session_id: str, wid: Optional[str], message: str = "") -> str:
    """'title — gist — …context around the referenced name…' for a widget, to
    ANCHOR follow-up directives. Naming only the id told the model where to
    render but not what the thread is about, so an ambiguous name in the ask
    fell back to world knowledge ("tell me more about Miku" over a sushi card
    → Hatsune Miku). When the message names something found in the widget's
    BODY, the snippet AROUND that name is included — "…options include Miku,
    Tojo, and Shizen…" is what actually pins Miku to sushi. Empty when unknown."""
    if not wid:
        return ""
    parts = []
    body_text = ""
    try:
        soup = BeautifulSoup(get_session_canvas(session_id) or "", "html.parser")
        card = soup.find(id=wid)
        if card:
            title_el = card.select_one(".glass-card-title, h3, h2, h4")
            title = title_el.get_text(strip=True) if title_el else ""
            if title and title.lower() not in ("data", "widget"):
                parts.append(title)
            body_text = card.get_text(" ", strip=True)
    except Exception:
        pass
    gist = _ledger_details(session_id).get(wid, "")
    if gist:
        parts.append(gist)
    # The context window around the first referenced name in the body.
    # Fuzzy fallback: a misspelled ask ("tell me more about Mikku") should
    # still quote the window around the real name it meant.
    body_lower = body_text.lower()
    for tok in sorted(_subject_tokens(message), key=len, reverse=True):
        pos = body_lower.find(tok)
        if pos < 0:
            match = next((w for w in _subject_tokens(body_text)
                          if _fuzzy_hit(tok, {w})), None)
            pos = body_lower.find(match) if match else -1
        if pos >= 0:
            lo, hi = max(0, pos - 60), min(len(body_text), pos + 90)
            parts.append(f"…{body_text[lo:hi].strip()}…")
            break
    return " — ".join(parts)[:400]


def _widget_on_canvas(session_id: str, wid: str, widget_type: str) -> Optional[str]:
    """`wid` if it names a REAL canvas widget of `widget_type`, else None. The
    type check is what stops a stale/ghost id from clobbering an unrelated
    widget."""
    if not wid:
        return None
    want = str(wid).lstrip("#").strip()
    try:
        for cid, ctype, _title in _iter_canvas_widgets(get_session_canvas(session_id)):
            if cid == want and ctype == widget_type:
                return cid
    except Exception:
        pass
    return None


def _resolve_widget_target(session_id: str, widget_type: str, model_target: str,
                           message: str = "", subject: str = "",
                           focus_widget_id: str = "", id_prefix: str = "") -> Optional[str]:
    """The id to render into, most trustworthy signal first:
      P0  focus_widget_id — the CLIENT told us which widget the question came
          from. That is a fact, not an inference, so it outranks everything.
      P1  the model's explicit `target`, when it names a real widget of this type.
      P2  deterministic topical reuse (find_reuse_target).
      P3  the widget's semantic ROLE, via its id prefix — for roles that are
          singletons but whose widget_type varies. A traffic ask renders as `map`
          when geocoding succeeds and `iframe_app` when it misses or the ask is
          "from A to B", and every P0-P2 signal above is keyed on widget_type, so
          two traffic asks landing on different sides of that fork could not see
          each other and stacked a second widget every time.
    Returns None to mint a fresh id."""
    return (_widget_on_canvas(session_id, focus_widget_id, widget_type)
            or _widget_on_canvas(session_id, model_target, widget_type)
            or find_reuse_target(session_id, widget_type, message, subject)
            or (find_existing_widget_by_id_prefix(session_id, id_prefix)
                if id_prefix in SINGLETON_ROLE_PREFIXES else None))


def _resolve_agent_widget_id(session_id: str, widget_type: str,
                             model_widget_id: str, message: str = "",
                             focus_widget_id: str = "") -> str:
    """The agent tier's id resolution — the tier-3 counterpart of
    _resolve_widget_target, and the fix for the biggest seam in follow-up
    targeting.

    The agent branch used to take `tool_args["widget_id"]` VERBATIM, so in-place
    update depended entirely on the model echoing an 8-hex id exactly right. A
    hallucinated or near-miss id ("#news-card" for "#answer-3f2a91b0") missed
    `soup.find(id=...)` and silently appended a BRAND NEW widget — the single
    most common way a follow-up failed to edit the widget it came from.

    Always returns an id: a resolved existing one, the model's own id when this
    is genuinely a NEW widget, or a freshly minted one."""
    resolved = _resolve_widget_target(session_id, widget_type, model_widget_id,
                                      message, "", focus_widget_id)
    if resolved:
        if model_widget_id and resolved != str(model_widget_id).lstrip("#").strip():
            logger.info(f"[WIDGET TARGET] agent id {model_widget_id!r} matched no "
                        f"{widget_type} on canvas — retargeted to #{resolved}")
        return resolved
    # Nothing to reuse: this is a new widget, so the model's chosen id is fine
    # (and keeps naming stable if it reuses it next turn). Only mint when the
    # model gave us nothing — discarding its id here would have renamed every
    # first-of-its-kind widget.
    if model_widget_id:
        return str(model_widget_id).lstrip("#").strip()
    return f"widget-{uuid.uuid4().hex[:8]}"


def get_canvas_summary(html: str) -> str:
    """Raw canvas HTML → a tiny, token-efficient inventory the router/agent reads to
    stay DOM-aware. Each line names the widget's id, its real widget_type, and what
    it shows, so a follow-up can reuse an id instead of spawning a duplicate."""
    if not html or html.strip() == "" or html == "Canvas is empty.":
        return "Canvas is currently empty."
    try:
        widgets = []
        for wid, wtype, title in _iter_canvas_widgets(html):
            desc = f' "{title}"' if title else ""
            widgets.append(f"- #{wid} · {wtype}{desc}")
        if not widgets:
            return "Canvas contains no recognizable widgets."
        return "\n".join(widgets)
    except Exception as e:
        logger.error(f"Error getting canvas summary: {e}")
        return html[:2000]


async def _drop_offsubject_widgets(message: str, good: list) -> list:
    """Cross-widget consistency backstop for the router's multi-widget fan-out.
    The router can compose a set where one widget drifts off the ask (the live
    "sandals -> a stock-market NEWS card + a Caribbean-resort VIDEO" failure).
    Given the ask and each built widget's subject, drop the clearly-unrelated ones.

    Conservative and FAILS OPEN: only runs for 2+ widgets, keeps anything the model
    doesn't flag, and never empties the set. `good` items are
    (wtype, id_prefix, wcfg, model_target) tuples; returns the same shape filtered."""
    if len(good) < 2:
        return good
    lines = []
    for i, (wtype, _p, wcfg, _t) in enumerate(good):
        cfg = wcfg or {}
        subj = cfg.get("title") or cfg.get("subtitle") or cfg.get("subject") or wtype
        lines.append(f'[{i}] {wtype}: {str(subj)[:90]}')
    data = await fast_llm_json(
        "A live dashboard built these widgets for ONE user ask. Some may be "
        "OFF-TOPIC — a different meaning of a word in the ask, or padding unrelated "
        'to what was asked. Return ONLY JSON {"keep": [indices genuinely about the '
        'ask]}. Keep every widget that plausibly serves the ask; drop ONLY the '
        "clearly-unrelated ones.\n"
        f'ASK: "{message}"\nWIDGETS:\n' + "\n".join(lines),
        max_tokens=120)
    keep = (data or {}).get("keep")
    if not isinstance(keep, list):
        return good
    idxs = [i for i in keep if isinstance(i, int) and 0 <= i < len(good)]
    kept = [good[i] for i in idxs]
    if not kept:                      # model dropped everything → fail open
        return good
    if len(kept) < len(good):
        dropped = [good[i][0] for i in range(len(good)) if i not in set(idxs)]
        logger.info(f"[CONSISTENCY] dropped off-subject {dropped} for {message!r}")
    return kept


__all__ = [k for k in globals().keys() if not k.startswith('__')]
