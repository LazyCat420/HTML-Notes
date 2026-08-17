from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

router = APIRouter()

@router.get("/models")
async def get_models():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{PRISM_URL}/config?includeLocal=true")
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch models from Prism")
            
            data = resp.json()
            models_map = data.get("textToText", {}).get("models", {})
            
            flat_models = []
            for provider, provider_models in models_map.items():
                for model in provider_models:
                    flat_models.append({
                        "provider": provider,
                        "model": model.get("name"),
                        "label": model.get("label") or model.get("name")
                    })
            return JSONResponse(content={"models": flat_models})
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/session/message")
async def send_message(req: MessageRequest):
    try:
        # Stamp this request's arrival order so a slower-committing older video
        # can't overwrite a newer one (see _place_media_widget). Captured as a
        # local so both nested closures (fast-path _append, agent injector _add)
        # read the same value regardless of context propagation.
        req_seq = next(_request_counter)
        # Audit trail: every query is logged at ingress with its req_seq so a
        # failed-to-render query can be correlated with whether a canvas commit
        # (logged in commit_canvas as [CANVAS]) was ever emitted for it. If a
        # [QUERY] has a matching [CANVAS] emit but nothing appeared on screen,
        # the loss is client-side (SSE framing); if there is no [CANVAS], the
        # turn produced no widget server-side.
        logger.info(f"[QUERY] seq={req_seq} session={req.session_id[:8]} msg={req.message!r}")
        # Save user message
        user_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        database.save_chat_message(
            message_id=user_msg_id,
            session_id=req.session_id,
            role="user",
            content=req.message
        )

        
        text_lower = req.message.lower().strip()
        text_clean = text_lower.strip()

        # Remember first-person profile facts ("I'm from Seattle", "my name is
        # Alex") so later turns can use them (default weather/map location, etc).
        try:
            capture_user_facts(req.message)
        except Exception as e:
            logger.warning(f"[USER PROFILE] capture failed: {e}")

        def spawn_widget_stream(widget_type: str, id_prefix: str, config: dict = None,
                                config_builder=None, status: str = None,
                                widget_id: str = None):
            """Heuristic fast-path: append a prebuilt widget to the CURRENT canvas
            and stream back the full canvas — same contract as the agent path, so
            existing widgets always survive.

            `config_builder` is an async callable for widgets whose content has to
            be written first (a grocery list's items). It runs inside the stream so
            the user sees a status line while it works, and it still skips the
            agentic loop entirely.
            """
            async def stream():
                message = status or f"heuristic-path: spawning {widget_type} widget..."
                # Debug breadcrumb for the browser console: which route fired and
                # what widget it chose. The client console.logs this so a misroute
                # ("grocery list" → map) is visible in DevTools without server logs.
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "fast-path", "widget_type": widget_type,
                    "id_prefix": id_prefix, "query": req.message}) + '\n\n')
                yield f'data: {json.dumps({"type": "status", "message": message})}\n\n'

                widget_config = dict(config or {})
                if config_builder:
                    built = await config_builder()
                    if built:
                        widget_config.update(built)

                # QUALITY FLOOR (fast-path mirror of the agent injector): never let
                # a data_card render as a wall of bare links or a sourceless answer —
                # covers the degraded builder paths (stock_report/market_research
                # answer with no sources, news/stock-news items with no summaries).
                if widget_type == "data_card" and _data_card_quality_gap(widget_config):
                    widget_config = await _ensure_data_card_quality(
                        widget_config, query_hint=req.message)

                # No explicit id → reuse the open widget this ask refines (a
                # follow-up on the same thread), else mint a fresh one. This is what
                # stops a new data_card/scoreboard/stock_card stacking on every
                # conversational follow-up.
                resolved_id = (_widget_on_canvas(req.session_id, req.focus_widget_id or "",
                                                 widget_type)
                               or widget_id
                               or find_reuse_target(req.session_id, widget_type, req.message,
                                                    subject=widget_config.get("title", ""))
                               # Role-prefix reuse, for roles whose rendered TYPE
                               # varies between asks (traffic → map or iframe_app).
                               # Every lookup above is type-keyed and so cannot see
                               # across that fork. See SINGLETON_ROLE_PREFIXES.
                               or (find_existing_widget_by_id_prefix(req.session_id, id_prefix)
                                   if id_prefix in SINGLETON_ROLE_PREFIXES else None)
                               or f"{id_prefix}-{uuid.uuid4().hex[:8]}")

                def _append(soup):
                    # Media widgets (video, music) are players: a new one replaces
                    # whatever's already playing instead of stacking a second one —
                    # but only if this request is newer than whatever placed it, so
                    # a slower older request can't overwrite it.
                    if _place_media_widget(soup, widget_type, resolved_id, widget_config, req_seq):
                        return

                    target = soup.select_one('#dashboard-grid')
                    if target is None:
                        grid = BeautifulSoup(
                            '<div id="dashboard-grid" class="dashboard-grid"></div>', 'html.parser')
                        soup.append(grid)
                        target = soup.select_one('#dashboard-grid')
                    node = BeautifulSoup(
                        render_widget(widget_type, resolved_id, widget_config), 'html.parser')
                    _stamp_media_seq(node, widget_type, req_seq)
                    # An explicit widget_id that already exists is an EDIT (e.g.
                    # merging into a checklist): replace it in place instead of
                    # appending a duplicate.
                    existing = soup.find(id=resolved_id)
                    if existing is not None:
                        existing.replace_with(node)
                    else:
                        target.append(node)

                event = await commit_canvas(req.session_id, _append)
                if event:
                    record_turn(req.session_id, req.message, f"fast-path:{widget_type}",
                                [(resolved_id, widget_type,
                                  widget_config.get("title", "") or req.message,
                                  _widget_detail(widget_config))])
                    database.save_chat_message(
                        message_id=f"msg_{uuid.uuid4().hex[:8]}",
                        session_id=req.session_id,
                        role="assistant",
                        content=f"\n\n<!--CANVAS_HTML_START-->\n{get_session_canvas(req.session_id)}\n<!--CANVAS_HTML_END-->"
                    )
                    yield event
                    # Say something useful. The fast path never involves the model,
                    # so it emitted no prose at all and these turns — traffic,
                    # weather, the most common asks — were completely SILENT to a
                    # user relying on audio.
                    try:
                        spoken = _spoken_summary(widget_type, widget_config, req.message)
                        if spoken:
                            # Carry the widget this sentence DESCRIBES. The client
                            # reveals that specific widget when this sentence
                            # plays, so the pairing is semantic rather than
                            # positional — otherwise sentence 1 reveals widget 1
                            # even when it is talking about widget 2.
                            yield (f'data: {json.dumps({"type": "chunk", "content": spoken, "widget_id": resolved_id})}'
                                   f'\n\n')
                    except Exception as e:
                        logger.warning(f"[TTS] fast-path spoken summary failed: {e}")
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        def _stream_clear_canvas(status: str = "closing everything..."):
            """Clear the ENTIRE canvas in one commit and stream it back. Bypasses
            the agent, which can only remove one widget per iteration and stops
            after the first mutation — so it could never close them all."""
            async def stream():
                yield f'data: {json.dumps({"type": "status", "message": status})}\n\n'

                def _clear(soup):
                    grid = soup.select_one('#dashboard-grid')
                    if grid is not None:
                        grid.clear()
                    else:
                        for c in soup.select('.glass-card, .widget-container'):
                            c.decompose()

                event = await commit_canvas(req.session_id, _clear)
                if event:
                    # Canvas wiped — reset the ledger so stale widget ids can't be
                    # offered as follow-up targets next turn.
                    _session_turn_ledger.pop(req.session_id, None)
                    record_turn(req.session_id, req.message, "clear", [])
                    database.save_chat_message(
                        message_id=f"msg_{uuid.uuid4().hex[:8]}",
                        session_id=req.session_id,
                        role="assistant",
                        content=f"\n\n<!--CANVAS_HTML_START-->\n{get_session_canvas(req.session_id)}\n<!--CANVAS_HTML_END-->"
                    )
                    yield event
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        def _stream_remove_widget():
            """Deterministically remove matching widget(s) from the canvas in ~50ms
            without invoking the LLM agent."""
            target_text = re.sub(
                r'\b(remove|delete|close|hide|clear|dismiss|drop|kill|stop|widget|card|window|the|it|my|a|an|get rid of)\b',
                '', text_clean, flags=re.I).strip()
            target_tokens = [t.lower() for t in re.findall(r'\w+', target_text) if len(t) > 1]

            async def stream():
                yield f'data: {json.dumps({"type": "status", "message": "closing widget..."})}\n\n'

                def _remove_matching(soup):
                    widgets = soup.select('.canvas-widget, .glass-card, .widget-container, [id^="widget-"], [data-widget-type]')
                    removed = False
                    for w in widgets:
                        w_id = (w.get("id") or "").lower()
                        w_type = (w.get("data-widget-type") or "").lower()
                        w_title = (w.get("data-title") or "").lower()
                        w_text = w.get_text().lower()

                        combined = f"{w_id} {w_type} {w_title} {w_text}"
                        
                        if not target_tokens or any(t in combined for t in target_tokens):
                            w.decompose()
                            removed = True
                    return removed

                event = await commit_canvas(req.session_id, _remove_matching)
                if event:
                    database.save_chat_message(
                        message_id=f"msg_{uuid.uuid4().hex[:8]}",
                        session_id=req.session_id,
                        role="assistant",
                        content=f"\n\n<!--CANVAS_HTML_START-->\n{get_session_canvas(req.session_id)}\n<!--CANVAS_HTML_END-->"
                    )
                    yield event
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        def _stream_open_app(app: dict):
            """Open one resolved catalog app in a new tab with zero LLM: emit
            the same open_url frame the agent interceptor uses (window.open +
            clickable-toast fallback client-side), say so in one sentence, and
            end the turn. No canvas mutation."""
            app_name, app_url = app["name"], app["launch_url"]

            async def stream():
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "fast-path",
                    "widget_type": "open_app", "id_prefix": app["id"],
                    "query": req.message}) + '\n\n')
                yield f'data: {json.dumps({"type": "status", "message": f"opening {app_name}…"})}\n\n'
                logger.info(f"[APP HUB] fast-path open_url → {app['id']}")
                yield f'data: {json.dumps({"type": "open_url", "url": app_url, "name": app_name})}\n\n'
                text = f"Opening {app_name} in a new tab."
                yield f'data: {json.dumps({"type": "chunk", "content": text})}\n\n'
                database.save_chat_message(
                    message_id=f"msg_{uuid.uuid4().hex[:8]}",
                    session_id=req.session_id,
                    role="assistant",
                    content=text)
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        def _stream_open_candidates(cands: list):
            """Two or more apps match — ask, with ZERO LLM. The old behaviour
            was to fall into a 15-40s agent turn that could spawn something
            random; a list of clickable links answers in ~0.1s and never
            guesses. Markdown links are rendered by the chat bubble and get
            target=_blank in index.js."""
            lines = "\n".join(
                f"- {c.get('icon') or '🌐'} [{c['name']}]({c['launch_url']})"
                + (f" — {c['description'][:60]}" if c.get("description") else "")
                for c in cands[:4] if c.get("launch_url"))
            text = f"Which one did you mean?\n\n{lines}"

            async def stream():
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "fast-path",
                    "widget_type": "open_app_candidates",
                    "id_prefix": ",".join(c["id"] for c in cands[:4]),
                    "query": req.message}) + '\n\n')
                logger.info("[APP HUB] fast-path candidates → "
                            + ", ".join(c["id"] for c in cands[:4]))
                yield f'data: {json.dumps({"type": "chunk", "content": text})}\n\n'
                database.save_chat_message(
                    message_id=f"msg_{uuid.uuid4().hex[:8]}",
                    session_id=req.session_id,
                    role="assistant",
                    content=text)
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        def _stream_settings():
            """Pop (or update) the singleton settings panel and, when the ask
            named a look, apply the closest palette. A UI-control action, so it
            skips the agent — same class as clearing the canvas above."""
            apply = pick_theme(req.message)
            cfg = {
                "themes": [{"name": t["name"], "label": t["label"], "swatch": t["swatch"]}
                           for t in THEME_CATALOG],
                "active": apply or "hud",
                "apply": apply or "",
            }
            status = (f"switching to the {apply} theme..." if apply else "opening settings...")

            async def stream():
                yield f'data: {json.dumps({"type": "status", "message": status})}\n\n'
                html = render_widget("settings", "settings-panel", cfg)

                def _mutate(soup):
                    existing = soup.find(id="settings-panel")
                    node = BeautifulSoup(html, "html.parser")
                    if existing is not None:
                        existing.replace_with(node)
                    else:
                        grid = soup.select_one('#dashboard-grid')
                        (grid or soup).append(node)

                event = await commit_canvas(req.session_id, _mutate)
                if event:
                    record_turn(req.session_id, req.message, "settings",
                                [("settings-panel", "settings", "appearance settings", "")])
                    # Persist the snapshot: this was the ONLY committing path that
                    # wrote no assistant message, so a theme turn left the DB's
                    # newest canvas pre-settings and the panel vanished on reload.
                    database.save_chat_message(
                        message_id=f"msg_{uuid.uuid4().hex[:8]}",
                        session_id=req.session_id,
                        role="assistant",
                        content=f"\n\n<!--CANVAS_HTML_START-->\n{get_session_canvas(req.session_id)}\n<!--CANVAS_HTML_END-->"
                    )
                    yield event
                spoken = (f"Switched to the {apply} theme." if apply
                          else "Here are your settings.")
                yield f'data: {json.dumps({"type": "chunk", "content": spoken, "widget_id": "settings-panel"})}\n\n'
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        def spawn_router_stream(specs: list, reason: str = None):
            """Build the router's chosen widget(s) and append them to the canvas in
            ONE commit. Building happens inside the stream (with a status line) so
            a slow pull — news summarising, page reads — shows progress instead of
            a dead spinner. Widgets that fail to build are skipped; if EVERY one
            fails, we degrade to an answer card on the original ask so the user
            still gets something rather than an empty turn."""
            async def stream():
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "router", "widgets": [s.get("type") for s in specs],
                    # The QUERY is the field that names a misroute — the type alone
                    # says "converter" without saying it was handed a cooking
                    # question. Emit what each spec was actually built from.
                    "queries": [s.get("query") for s in specs],
                    "targets": [s.get("target") for s in specs],
                    "reason": reason or "", "query": req.message}) + '\n\n')
                label = (", ".join(s.get("type", "") for s in specs)
                         if len(specs) > 1 else (specs[0].get("type", "widget") if specs else "widget"))
                yield f'data: {json.dumps({"type": "status", "message": f"building {label}..."})}\n\n'

                built = await asyncio.gather(
                    *[build_router_widget(s, req.session_id, req.message) for s in specs],
                    return_exceptions=True)
                # A spec builds to one widget (tuple) OR several (list of tuples, e.g.
                # a trip → itinerary card + map). Flatten both shapes, carrying the
                # spec's model-chosen `target` (P2) alongside each built widget. A
                # multi-widget spec can't map a single target, so those get None.
                good = []  # (wtype, id_prefix, wcfg, model_target)
                for s, b in zip(specs, built):
                    tgt = s.get("target") if isinstance(s, dict) else None
                    if isinstance(b, list):
                        good.extend((x[0], x[1], x[2], None)
                                    for x in b if isinstance(x, tuple) and x)
                    elif isinstance(b, tuple) and b:
                        good.append((b[0], b[1], b[2], tgt))
                if not good:
                    logger.info("[ROUTER] all builds empty — degrading to an answer card")
                    good = [("data_card", "answer", await build_answer_config(req.message), None)]

                # Cross-widget consistency: drop any built widget that drifted off
                # the ask (a stray stock-market card / resort video on a "sandals"
                # ask) BEFORE they're committed to the canvas. Fails open.
                good = await _drop_offsubject_widgets(req.message, good)

                placed = []  # (rid, wtype, wcfg) for the ledger, filled during _append

                def _append(soup):
                    target = soup.select_one('#dashboard-grid')
                    if target is None:
                        grid = BeautifulSoup(
                            '<div id="dashboard-grid" class="dashboard-grid"></div>', 'html.parser')
                        soup.append(grid)
                        target = soup.select_one('#dashboard-grid')
                    for wtype, id_prefix, wcfg, model_target in good:
                        # UPDATE the widget this ask refines instead of stacking a
                        # second: the model's explicit target when valid (P2), else
                        # the deterministic follow-up reuse (P0). Stops "two maps" and
                        # "a fresh news card per follow-up".
                        reuse = _resolve_widget_target(req.session_id, wtype, model_target,
                                                       req.message, (wcfg or {}).get("title", ""),
                                                       req.focus_widget_id or "",
                                                       id_prefix=id_prefix)
                        rid = reuse or f"{id_prefix}-{uuid.uuid4().hex[:8]}"
                        placed.append((rid, wtype, wcfg or {}))
                        # Media widgets (video, music) swap the current player in
                        # place; everything else appends.
                        if _place_media_widget(soup, wtype, rid, wcfg or {}, req_seq):
                            continue
                        node = BeautifulSoup(render_widget(wtype, rid, wcfg or {}), 'html.parser')
                        _stamp_media_seq(node, wtype, req_seq)
                        existing = soup.find(id=rid)
                        if existing is not None:
                            existing.replace_with(node)
                        else:
                            target.append(node)

                event = await commit_canvas(req.session_id, _append)
                if event:
                    record_turn(req.session_id, req.message, "router",
                                [(rid, wt, (wc.get("title", "") or req.message),
                                  _widget_detail(wc)) for (rid, wt, wc) in placed])
                    database.save_chat_message(
                        message_id=f"msg_{uuid.uuid4().hex[:8]}",
                        session_id=req.session_id,
                        role="assistant",
                        content=f"\n\n<!--CANVAS_HTML_START-->\n{get_session_canvas(req.session_id)}\n<!--CANVAS_HTML_END-->"
                    )
                    yield event
                    # One spoken line covering everything this turn placed. The
                    # router can commit several widgets at once ("weather + map"),
                    # and reading a sentence per widget aloud would be worse than
                    # silence — so join at most two and stop.
                    try:
                        # One chunk PER WIDGET, each tagged with its own id, rather
                        # than one joined blob. The client reveals each widget as
                        # its own sentence is spoken, so "weather + map" shows the
                        # weather card on the weather sentence and the map on the
                        # map sentence. Joining them threw that pairing away.
                        # Still capped at two: more than that read aloud is worse
                        # than silence.
                        spoken_any = 0
                        for (rid_, wt_, wc_) in placed:
                            if spoken_any >= 2:
                                break
                            ln = _spoken_summary(wt_, wc_, req.message)
                            if not ln:
                                continue
                            yield (f'data: {json.dumps({"type": "chunk", "content": ln, "widget_id": rid_})}'
                                   f'\n\n')
                            spoken_any += 1
                    except Exception as e:
                        logger.warning(f"[TTS] router spoken summary failed: {e}")
                yield 'data: {"type": "done"}\n\n'

            return StreamingResponse(
                _run_turn(req.session_id, req.current_canvas or "", stream, req.canvas_version),
                media_type="text/event-stream",
            )

        # Removal/modification intents must reach the agent (canvas_modify_dom) —
        # never spawn a widget off keywords like "remove the clock".
        wants_removal = bool(re.search(
            r'\b(remove|delete|close|hide|clear|dismiss|drop|kill|stop)\b|get rid of', text_clean))

        # Media/data intents override widget-name keywords: "clock for video",
        # "video of a clock" or "news about checklists" must reach the agent,
        # not spawn a clock/checklist off a substring match.
        is_video_ask = bool(VIDEO_ASK_RE.search(text_clean))
        is_data_ask = bool(DATA_ASK_RE.search(text_clean))
        # A grocery/shopping/checklist ask ("add potato salad to the grocery list")
        # names "grocery", which is ALSO a POI noun in POI_MAP_RE — so the map
        # branch used to steal it and (with no city) show the "which city?" prompt.
        # Any query that names a *list* is list-management, never a store map.
        is_list_ask = bool(re.search(r'\b(list|checklist|to-?dos?)\b', text_clean)
                           or LIST_EDIT_RE.search(text_clean)
                           or LIST_ITEM_REMOVE_RE.search(text_clean))

        is_stock_ask = bool(STOCK_REPORT_RE.search(text_clean) or STOCK_WORD_RE.search(text_clean) or re.search(r'\b(chart|charts|stock|stocks|ticker|share|shares|price|nvda|tsla|aapl|msft|googl|amzn)\b', text_clean))
        has_conjunction = bool(re.search(r'\b(and|also|plus|along with|both|as well as|with)\b', text_clean))
        has_data_noun = bool(re.search(r'\b(article|articles|news|list|boots|trails|guide|summary|info|review|reviews|buying|buy)\b', text_clean))
        
        intents_list = [
            is_video_ask,
            (is_data_ask or has_data_noun),
            is_stock_ask,
            is_list_ask,
            bool(MAP_ASK_RE.search(text_clean)),
            bool(WEATHER_ASK_RE.search(text_clean))
        ]
        active_intents_count = sum(1 for x in intents_list if x)
        is_compound_ask = bool(has_conjunction and active_intents_count >= 2)

        wants_music = bool(re.search(r'\b(music|radio|song|songs|playlist)\b', text_clean))
        league = resolve_league(text_clean)

        # ── Canvas-control intercepts: ALWAYS ON, prism mode included ───────
        # NOT latency shortcuts — capability workarounds, so they deliberately
        # sit OUTSIDE the prism-mode guard below. The agent removes ONE widget
        # per iteration and stops after the first mutation, so it structurally
        # CANNOT clear a full canvas ("close everything" silently failed while
        # these were gated). A list-item edit sent to the agent decomposes the
        # whole widget instead of editing it. These are unambiguous imperatives
        # about LOCAL UI state needing no knowledge, tools or research, so a
        # 60-90s agent turn is worse on every axis. Content asks go to prism.
        # LIST ITEM REMOVE — "delete the veggies from the grocery list" edits the
        # list in place. Checked before CLEAR_ALL and before wants_removal sends it
        # to the agent (which would decompose the whole widget).
        if LIST_ITEM_REMOVE_RE.search(text_clean) and not is_video_ask:
            existing_list = _extract_existing_checklist(req.session_id)
            if existing_list:
                ex_id, ex_title, ex_items = existing_list
                return spawn_widget_stream(
                    "checklist", "checklist",
                    config_builder=lambda: build_list_remove_config(
                        req.message, ex_title, ex_items),
                    status=f"updating “{ex_title}”...",
                    widget_id=ex_id)

        # CLOSE ALL — one server call, no agent. Guarded so a "…from the list" item
        # edit (which can contain "all") never triggers a full wipe.
        if CLEAR_ALL_RE.search(text_clean) and not LIST_ITEM_REMOVE_RE.search(text_clean):
            return _stream_clear_canvas()

        # FAST WIDGET REMOVAL — "close cnn live news", "close video", "remove clock"
        # Deterministically decomposes matching widget(s) in ~50ms without an agent turn.
        if wants_removal and not LIST_ITEM_REMOVE_RE.search(text_clean):
            return _stream_remove_widget()

        # THEME / SETTINGS — a UI-control action (like CLEAR_ALL). A pure look
        # change or a settings request flips instantly via the singleton panel,
        # no agent spin-up. Anything phrased oddly still reaches the agent, which
        # knows the same settings route. Guarded against media asks so "dark
        # ambient music" / "night lofi" never trips it.
        #
        # ALSO guarded against widget color edits: "make it green" / "change the
        # line to orange" after a chart matches the verb+color alternation, and
        # this intercept runs BEFORE follow-up targeting — so the whole dashboard
        # got repainted instead of the widget being edited. When the client says
        # the question came FROM a widget (focus_widget_id) and the ask names no
        # look-noun (theme/mode/appearance/…), the color word belongs to that
        # widget — let follow-up routing have it.
        _widget_color_edit = bool(
            req.focus_widget_id and req.focus_widget_id != "settings-panel"
            and re.search(r'\b(make|set|change|switch|turn)\b', text_clean)
            and not re.search(
                r'\b(theme|themes|appearance|palette|colou?r ?scheme|colou?rway|'
                r'skin|mode|settings|background|dashboard|canvas|everything)\b',
                text_clean))
        if (THEME_INTENT_RE.search(text_clean)
                and not _widget_color_edit
                and not re.search(r'\b(music|radio|song|playlist|video|watch)\b', text_clean)):
            return _stream_settings()

        # APP HUB — the launcher grid of the user's own services, from the live
        # portal-service inventory. A UI ask like settings: deterministic, zero
        # agent latency.
        if APP_HUB_INTENT_RE.search(text_clean) and not is_video_ask:
            return spawn_widget_stream(
                "app_grid", "app-hub",
                config_builder=lambda: build_app_grid_config(req.message),
                status="fetching your apps from portal…")

        # OPEN AN APP — deterministic fast lane, and the FIRST thing an
        # open-imperative is checked against. The catalog fetch is ~15ms
        # (measured 2026-08-16); routing this through the agent spent 15-40s
        # of LLM to fire it, and the agent has no catalog of its own.
        #
        # Precedence (the fix for "open the music player" spawning the
        # mini-player widget instead of the music-player SITE):
        #   1. EXACT id/name/alias match → open it. An app's own name always
        #      beats a widget that happens to share those words.
        #   2. all-words PARTIAL → open only when the ask carries an explicit
        #      app marker ("…app", "…in a new tab") or names no widget noun,
        #      so "open my notes" still reaches the notepad widget.
        #   3. still ambiguous (client-vs-client) → instant candidate reply,
        #      no LLM. Backends never compete: prefer_clients() drops them.
        #   4. no match → fall through untouched to the widget lanes/agent.
        if not wants_removal:
            _open_target = extract_open_app_target(text_clean)
            # A BARE app name counts as an open ask. Without this, "music
            # player" fell to the music widget lane (never opened the app at
            # all) and "trading bot" fell to the agent — 17s, measured — even
            # though both name an app exactly. Whole-name match only, so
            # ordinary prose can never launch a tab.
            if not _open_target:
                _bare = extract_bare_app_name(text_clean)
                if _bare:
                    _bare_hub = await get_portal_apps()
                    _bare_app, _ = resolve_portal_app(
                        _bare, _bare_hub["apps"], strict=True, exact_only=True)
                    if _bare_app and _bare_app.get("launch_url"):
                        return _stream_open_app(_bare_app)
            if _open_target:
                _open_name, _explicit, _has_widget_noun = _open_target
                _hub = await get_portal_apps()
                _open_app, _open_cands = resolve_portal_app(
                    _open_name, _hub["apps"], strict=True)
                # An exact hit is unconditional; a partial defers to widgets
                # when the phrasing was widget-flavoured.
                _exact = bool(_open_app) and _norm_key(_open_name) in (
                    {_norm_key(_open_app["id"]), _norm_key(_open_app["name"])}
                    | {_norm_key(x) for x in (_open_app.get("aliases") or [])})
                if _open_app and _open_app.get("launch_url") and (
                        _exact or _explicit or not _has_widget_noun):
                    return _stream_open_app(_open_app)
                # Ambiguity is only worth a prompt when the ask was clearly
                # about an app — otherwise let the widget lanes have it.
                if (_open_cands and len(_open_cands) > 1
                        and (_explicit or not _has_widget_noun)):
                    return _stream_open_candidates(_open_cands)

        # REMINDER / alarm — checked before the converter (a reminder can carry
        # a time that looks numeric) and before the clock timer branch.
        if REMINDER_INTENT_RE.search(text_clean):
            # Reuse the open reminder when one exists: "actually make that 30
            # minutes" must retarget the countdown, not stack a second alarm
            # that also eventually fires.
            return spawn_widget_stream(
                "reminder", "reminder",
                config=build_reminder_config(req.message),
                status="setting a reminder...",
                widget_id=find_existing_widget(req.session_id, "reminder"))

        # CALC / CONVERT — instant interactive widget, no agent. The stock-compare
        # guard ("NVDA vs SPY" is a chart, not a conversion) now lives inside
        # is_conversion_ask, along with the veto that keeps a numeric QUESTION
        # ("how long should I cook 5 lb in the oven" — which satisfies the loose
        # "<n> <unit> in <word>" arm) out of the calculator.
        if is_conversion_ask(text_clean):
            return spawn_widget_stream(
                "converter", "converter",
                config=build_converter_config(req.message),
                status="opening the converter...")

        # LIST RESTORE — "bring back my grocery list": restore saved items instead
        # of regenerating. Guarded against edits/removals so those still route
        # normally; only fires when a matching saved list actually exists.
        if (LIST_RESTORE_RE.search(text_clean)
                and not LIST_EDIT_RE.search(text_clean)
                and not LIST_ITEM_REMOVE_RE.search(text_clean)
                and not is_video_ask and not is_data_ask):
            restored = _resolve_restorable_list(req.message)
            if restored and restored.get("items"):
                return spawn_widget_stream(
                    "checklist", "checklist",
                    config={"title": restored.get("title") or "Checklist",
                            "items": restored["items"]},
                    status="bringing your list back...")

        # ── CRYPTO / ON-CHAIN — ALWAYS ON (prism mode included) ──────────────
        # Deliberately OUTSIDE the prism-mode guard below, like the canvas-control
        # intercepts: these are fully deterministic (regex + keyless on-chain APIs)
        # and need NO LLM. In prism mode a crypto ask would otherwise depend on the
        # LLM router's classify pass — which silently fails to the agent whenever
        # the fast-LLM backend is down (observed after a power outage). The holder
        # graph is also something the agent literally cannot build. Conservative
        # gating (a named coin / crypto word, a $CASHTAG, or a 0x contract); softer
        # asks still fall through. Builds are fast + cached, so we PRE-RESOLVE and
        # only spawn on success, else fall through instead of an empty shell.
        _has_addr = bool(EVM_ADDR_RE_MAIN.search(req.message))
        # Soft "<name> token/coin" only counts on a short lookup-shaped query, so a
        # long sentence that merely mentions "tokens" doesn't trigger a lookup.
        _soft_crypto = (CRYPTO_SOFT_RE.search(text_clean)
                        and len(text_clean.split()) <= 4)
        _crypto_ctx = bool(CRYPTO_WORD_RE.search(text_clean)
                           or CASHTAG_RE.search(req.message) or _has_addr
                           or _soft_crypto)
        if _crypto_ctx and not wants_removal and not is_video_ask and not wants_music:
            # 1. HOLDER GRAPH — "who holds PEPE", "$BONK whales", "is X a fair
            #    launch", "pump and dump wallets". The headline feature.
            if WALLET_GRAPH_RE.search(text_clean):
                g_cfg = await build_wallet_graph_config(req.message)
                if g_cfg:
                    return spawn_widget_stream("wallet_graph", "wallet-graph", config=g_cfg)
                # Token resolved to a chain we can't graph → price card; else fall through.
                c_cfg = await build_crypto_card_config(req.message)
                if c_cfg:
                    return spawn_widget_stream("crypto_card", "crypto", config=c_cfg)
            # 2. WALLET INSPECTOR — an address + holdings framing ("what does
            #    0x… hold", "balance of this wallet").
            elif _has_addr and WALLET_INSPECT_RE.search(text_clean):
                w_cfg = await build_wallet_config(req.message)
                if w_cfg:
                    return spawn_widget_stream("data_card", "wallet", config=w_cfg)
            # 3. CRYPTO REPORT — deep-dive / scam-check framing. Uses the local LLM
            #    for synthesis; if that's down the builder still returns a numbers
            #    -only brief, so it degrades rather than falling through.
            elif CRYPTO_REPORT_RE.search(text_clean):
                return spawn_widget_stream(
                    "data_card", "crypto-report",
                    config_builder=lambda: build_crypto_report_config(req.message),
                    status="researching the token — price, on-chain distribution and news...")
            # 4. CRYPTO PRICE CARD — the default for a strong crypto signal that
            #    isn't a graph/wallet/report ask. Skip a clear news ask.
            elif not NEWS_ASK_RE.search(text_clean):
                c_cfg = await build_crypto_card_config(req.message)
                if c_cfg:
                    return spawn_widget_stream("crypto_card", "crypto", config=c_cfg)

        # PRISM MODE (default): everything below is the "go around prism to
        # save latency" fast-path cascade. It is SKIPPED so every content ask
        # runs through the prism agent + lazy-tool-service MCP tools. The
        # router below is gated the same way. use_lazy_agent=True restores it.
        if req.use_lazy_agent:

            # 0. "THIS ONE SUCKS, FIND ANOTHER" — swap the current video and remember
            #    the dislike forever. Checked before every other video path so a
            #    "find me another video" isn't misread as a fresh search for the
            #    literal words. Only fires when a video is actually on screen.
            current_vid = _session_current_video.get(req.session_id)
            if (current_vid and not wants_removal and not wants_music
                    and (ANOTHER_VIDEO_RE.search(text_clean) or CHANNEL_DISLIKE_RE.search(text_clean))):
                vquery = current_vid.get("query") or clean_video_query(req.message)
                disliked_channel = current_vid.get("channel")
                if CHANNEL_DISLIKE_RE.search(text_clean) and disliked_channel:
                    block_channel(disliked_channel, reason=f"disliked via: {req.message[:80]}")
                    status = f"got it — never showing {disliked_channel} again. finding another..."
                    logger.info(f"[VIDEO PREF] blocked channel {disliked_channel!r}")
                else:
                    block_video(current_vid.get("video_id"), reason=f"disliked via: {req.message[:80]}")
                    status = "finding you a different one..."
                    logger.info(f"[VIDEO PREF] blocked video {current_vid.get('video_id')!r}")

                async def _find_another():
                    # The replacement inherits the ORIGINAL ask's time constraint
                    # (it lives in the remembered query): "another one" after
                    # "a new video about X" must stay new.
                    hits = await search_youtube_videos(
                        vquery, limit=12, freshness=parse_freshness(vquery),
                        form=parse_video_form(req.message) or _stashed_turn_form())
                    hits = filter_blocked_videos(hits)
                    top, cands = pick_best_video(hits, exclude_ids=_shown_video_ids(req.session_id))
                    if not top:
                        return None
                    _remember_current_video(req.session_id, top, vquery)
                    return {
                        "video_id": top["video_id"],
                        "title": top.get("title") or vquery,
                        "query": vquery,
                        "candidates": cands,
                    }
                return spawn_widget_stream("youtube_player", "video",
                                           config_builder=_find_another, status=status)

            # 1. SPORTS SCORES — "fifa scores", "ufc card", "who's playing in the nba".
            #    A scoreboard is structured data, not prose: a text card of headlines
            #    cannot show you who is playing whom.
            if (league and not wants_removal and not is_video_ask
                    and (SCORE_ASK_RE.search(text_clean) or text_clean in SPORTS_LEAGUES)):
                # Resolved before returning so an off-season/empty league falls through
                # to the agent instead of spawning an empty scoreboard.
                board = await sports_scores(league)
                if not board.get("is_error"):
                    return spawn_widget_stream("scoreboard", "scores", board,
                                               status=f"pulling {board.get('title', 'scores')}...")
                # Off-season / no fixtures today: ESPN has nothing to put on a
                # scoreboard, so rather than silently falling through to the slow
                # agent loop (which for "fifa game current stats" rendered NOTHING),
                # synthesize an answer card — standings, upcoming fixtures, recent
                # results — from a web search. The user always gets something.
                return spawn_widget_stream(
                    "data_card", "sports-answer",
                    config_builder=lambda: build_answer_config(req.message),
                    status="the league is between games — finding the latest...")

            # 2. NEWS VIDEO — "fifa news video". Searched by relevance this returns the
            #    most-watched clip for those words (a years-old recap); news means the
            #    NEWEST upload, so sort by date and drop the medium words from the query.
            fresh_ask = parse_freshness(req.message)
            # Fires for ANY video ask, not just one carrying a recency word:
            # naming a channel is itself a request for that channel's newest
            # upload. The picker returns None when no channel binds, so topic
            # asks ("a cookie recipe video") fall through to normal search.
            if (is_video_ask and not is_compound_ask
                    and not wants_removal and not LIVE_ASK_RE.search(text_clean)):
                # Channel- and date-verified: "fox news video newest about the
                # stock market" must come from the FOX News uploads feed, not
                # whatever a date-sorted keyword search surfaces (live failure:
                # a 40-view unrelated clip). parse_freshness also carries an
                # explicit window ("this week") into the search. Falls through
                # to the generic video branch below when the picker finds nothing.
                rcfg = await _recency_video_pick(req.message, req.session_id,
                                                 freshness=fresh_ask)
                if rcfg:
                    return spawn_widget_stream(
                        "youtube_player", "news-video", rcfg,
                        status=f"finding the latest '{rcfg.get('query') or clean_video_query(req.message)}' video...")

            # 3. LIVE STREAMS — checked before the data guard, because "cnn live news"
            #    contains "news" and was being sent down the data_card path (a text
            #    list of headlines) when the user wanted something to watch. "live
            #    music"/"live radio" still belongs to the music player, not YouTube.
            if LIVE_ASK_RE.search(text_clean) and not wants_removal and not wants_music:
                # Search the CLEANED subject, not the raw message: "dunkey livestreams"
                # must search "dunkey" under YouTube's live filter (the filter, not the
                # word, is what finds a stream). Searching the literal "dunkey
                # livestreams" returned nothing, so live asks silently died while the
                # plain "dunkey videos" path worked.
                lquery = clean_video_query(req.message)
                live_hits = filter_blocked_videos(
                    await search_youtube_videos(lquery, limit=6, order="live"))
                if live_hits:
                    top = live_hits[0]
                    _remember_current_video(req.session_id, top, lquery)
                    return spawn_widget_stream("youtube_player", "live", {
                        "video_id": top["video_id"],
                        "title": top.get("title") or lquery,
                        "query": lquery,
                        # Label-owned/geo-blocked streams refuse to embed; the player
                        # hops through these on an embed error.
                        "candidates": [v["video_id"] for v in live_hits[1:] if v.get("video_id")],
                    }, status=f"finding a '{lquery}' live stream...")
                # Nobody's live right now: fall back to a regular (varied) video of the
                # same subject rather than falling through to the agent (which broke).
                # "dunkey livestreams" when dunkey is offline → a dunkey video.
                vod_hits = filter_blocked_videos(
                    await search_youtube_videos(lquery, limit=10,
                                                form=parse_video_form(req.message)))
                top, cands = pick_best_video(vod_hits, exclude_ids=_shown_video_ids(req.session_id))
                if top:
                    _remember_current_video(req.session_id, top, lquery)
                    return spawn_widget_stream("youtube_player", "video", {
                        "video_id": top["video_id"],
                        "title": top.get("title") or lquery,
                        "query": lquery,
                        "candidates": cands,
                    }, status=f"no live stream right now — pulling a '{lquery}' video...")

            # 3b. GENERAL VIDEO — a plain "show me a video of X" / "X video" that is
            #     neither a live stream (handled above, deterministic — bloomberg live
            #     is always THE stream) nor a dated news clip. Broad topic asks like
            #     "a cookie recipe video" used to fall through to the agent, which
            #     picked the #1 hit every time, so a repeat ask replayed the identical
            #     video. Fast-path it AND vary among the top handful so it stays
            #     interesting. Music videos keep going to the player, not here.
            if is_video_ask and not is_compound_ask and not wants_removal and not wants_music:
                vquery = clean_video_query(req.message)
                vhits = await search_youtube_videos(vquery, limit=10, rerank=True,
                                                    freshness=fresh_ask,
                                                    form=parse_video_form(req.message))
                vhits = filter_blocked_videos(vhits)
                top, cands = pick_best_video(vhits, exclude_ids=_shown_video_ids(req.session_id))
                if top:
                    _remember_current_video(req.session_id, top, vquery)
                    return spawn_widget_stream("youtube_player", "video", {
                        "video_id": top["video_id"],
                        "title": top.get("title") or vquery,
                        "query": vquery,
                        "candidates": cands,
                    }, status=f"finding a '{vquery}' video...")

            # 4. WEATHER — "weather in Tokyo", "forecast for London", "sf weather".
            #    A real Open-Meteo pull (keyless) rendered as the dedicated weather
            #    widget, not a text card of search results about weather.
            if WEATHER_ASK_RE.search(text_clean) and not wants_removal and not is_video_ask:
                weather = await get_weather(extract_location(req.message))
                if not weather.get("is_error"):
                    return spawn_widget_stream(
                        "weather", "weather", weather,
                        status=f"checking the weather in {weather.get('location', '')}...",
                        widget_id=find_existing_widget(req.session_id, "weather"))
                # An unresolved place falls through to the agent instead of a dead card.

            # 5-pre0. COMPREHENSIVE STOCK REPORT — "full report on NVDA", "deep dive
            #    tesla stock", "analyze apple stock". Synthesizes quotes+fundamentals+
            #    technicals+full-text news+finnews+YouTube-transcript commentary into
            #    one report card. Gated on a stock context (stock word OR an all-caps
            #    ticker token) so "analyze this photo" or "full breakdown of the plot"
            #    don't hijack it. Checked BEFORE the news/price branches so "report"
            #    wins over "news"/"stock".
            # (Crypto / on-chain intercepts run ABOVE the prism-mode guard — they
            #  are deterministic and must work even when the LLM classifier is down.)

            # 4-pre. DEEP MARKET RESEARCH — "research the market", "deep dive on the
            #   stock market", "in-depth market analysis". Drives the shared
            #   DEEP_RESEARCH agent (lazy-tool-service) to fan out across sources and
            #   synthesize a brief, via the lazycat.research SDK helper. Gated to the
            #   GENERAL market: fires only when a research word + a market word are
            #   present AND no specific company/ticker is named — so "deep dive on
            #   NVDA" still falls through to the single-ticker report below.
            if (MARKET_RESEARCH_RE.search(text_clean)
                    and (MARKET_WORD_RE.search(text_clean) or STOCK_WORD_RE.search(text_clean))
                    and not wants_removal and not is_video_ask
                    and not re.search(r'\b[A-Z]{2,5}\b', req.message)):
                # Strip research/market filler; if no specific subject remains, it's a
                # general-market ask. A leftover noun ("apple") → let stock-report
                # resolve it as a single-ticker deep dive instead.
                _subj = re.sub(
                    r'\b(deep|dive|research|depth|in|full|report|analysis|analyz\w*|'
                    r'comprehensive|thorough\w*|breakdown|rundown|picture|overview|dig|'
                    r'into|what\'?s?|going|happening|moving|on|of|the|a|an|about|for|me|'
                    r'please|give|do|can|you|show|get|tell|today\w*|now|current\w*|'
                    r'latest|recent\w*|stock\w*|market\w*|share\w*|equit\w*|wall|street|'
                    r'econom\w*|trading|financ\w*|sector\w*|news|update\w*|'
                    # common stopwords so a trailing "and whats moving it" doesn't read
                    # as a specific subject and drop the ask to the single-ticker path.
                    r'and|or|it|its|us|why|how|are|is|was|to|with|whats?|going|right)\b',
                    ' ', text_clean, flags=re.I)
                if not re.search(r'[a-z]{3,}', _subj):
                    return spawn_widget_stream(
                        "data_card", "market-research",
                        config_builder=lambda: build_market_research_config(req.message),
                        status="researching the market across multiple sources — this takes a minute...")

            if (STOCK_REPORT_RE.search(text_clean) and not wants_removal and not is_video_ask
                    and (STOCK_WORD_RE.search(text_clean) or MARKET_WORD_RE.search(text_clean)
                         or re.search(r'\b[A-Z]{2,5}\b', req.message))):
                return spawn_widget_stream(
                    "data_card", "stock-report",
                    config_builder=lambda: build_stock_report_config(req.message),
                    status="building a full report — quotes, news, and analyst commentary...")

            # 5-pre2. SYNTHESIZED NEWS BRIEF — informational framing on a news ask
            #    ("tell me about the stock market news", "what's happening in the
            #    markets", "summarize today's news", "catch me up") wants a WRITTEN
            #    brief, not a wall of headline links. Route to grounded_research
            #    synthesis (finance or general) — an `answer` card with real content +
            #    sources. MUST come before the link-list news branches below, which
            #    otherwise grab any query containing "news" first. A plain "stock market
            #    news" (no informational framing) still gets the fast link card.
            # Also catches "what's happening in the markets" (informational + a general
            # MARKET word, no specific ticker) even without the literal word "news".
            # Gated to markets?/stock market + no ticker so it never steals a single-
            # ticker ask ("what's happening with AAPL" -> stock card, handled elsewhere).
            _market_general = bool(re.search(r'\b(stock )?markets?\b', text_clean)
                                   and not re.search(r'\b[A-Z]{2,5}\b', req.message))
            if (_NEWS_SYNTH_RE.search(text_clean)
                    and (NEWS_ASK_RE.search(text_clean) or _market_general)
                    and not wants_removal and not is_video_ask and not LIVE_ASK_RE.search(text_clean)):
                _finance = bool(STOCK_WORD_RE.search(text_clean) or MARKET_WORD_RE.search(text_clean))
                return spawn_widget_stream(
                    "data_card", "news-brief",
                    config_builder=lambda: build_news_brief_config(req.message, finance=_finance),
                    status="researching and writing your news brief...")

            # 5-pre. STOCK/MARKET NEWS — branch 5 deliberately excludes stock words,
            #    which used to drop these asks to the agent, whose stock_news tool
            #    returns bare title+publisher rows → a wall of links. Same
            #    gather→summarize treatment as general news, sourced from Yahoo
            #    finance search instead of GDELT.
            if (NEWS_ASK_RE.search(text_clean)
                    and (STOCK_WORD_RE.search(text_clean) or MARKET_WORD_RE.search(text_clean))
                    and not wants_removal and not is_video_ask):
                return spawn_widget_stream(
                    "data_card", "stock-news",
                    config_builder=lambda: build_stock_news_config(req.message),
                    status="gathering and summarizing market news...")

            # 5. NEWS — "news about X", "latest headlines". Search + one summarizing
            #    LLM pass so each item reads as a story, not a bare link. Stock news
            #    keeps its own dedicated path.
            if (NEWS_ASK_RE.search(text_clean) and not wants_removal
                    and not is_video_ask and not STOCK_WORD_RE.search(text_clean)
                    and not LIVE_ASK_RE.search(text_clean)):
                return spawn_widget_stream(
                    "data_card", "news",
                    config_builder=lambda: build_news_config(req.message),
                    status="gathering and summarizing the news...")

            # 5a. WIKIPEDIA — "open a random wikipedia page", "wikipedia about X".
            #     Rendered as a data_card from the REST summary API, NOT an iframe:
            #     en.wikipedia.org sends X-Frame-Options and refuses to embed, which
            #     rendered a solid-black "App Window".
            if (WIKI_ASK_RE.search(text_clean)
                    and not wants_removal and not is_video_ask):
                return spawn_widget_stream(
                    "data_card", "wikipedia",
                    config_builder=lambda: build_wikipedia_config(req.message),
                    status="opening a Wikipedia article...")

            # 5b. DIRECTIONS / TRAVEL-TIME — "how long to the airport", "traffic to
            #     downtown", "directions to X". No routing widget yet, so synthesise a
            #     real answer card instead of a blank map. Checked BEFORE the map fast
            #     path (a travel question isn't a "where is X" marker map) and OUTSIDE
            #     the is_data_ask gate so a "weather AND how long to..." still resolves.
            if (DIRECTIONS_ASK_RE.search(text_clean)
                    and not EAT_MAP_RE.search(text_clean)
                    and not wants_removal and not is_video_ask):
                # "traffic in X", "directions to Y", "from A to B" → a real Google Maps
                # embed (keyless, client-side, traffic-aware) instead of a text card.
                # A pure travel-TIME ask ("how long to the airport") keeps the answer
                # card, which gives the actual minutes.
                if TRAFFIC_MAP_RE.search(text_clean):
                    # Gated by TRAFFIC_MAP_RE above, so intent is already settled.
                    traffic_widget, traffic_cfg = await build_traffic_widget(
                        req.message, force_traffic=True)
                    if traffic_cfg:
                        # Reuse is handled centrally by the "traffic" id prefix —
                        # see SINGLETON_ROLE_PREFIXES — so both this fast path and
                        # the router branch collapse onto the same open widget.
                        return spawn_widget_stream(
                            traffic_widget, "traffic", config=traffic_cfg,
                            status="pulling up the map and live traffic...")
                return spawn_widget_stream(
                    "data_card", "directions",
                    config_builder=lambda: build_answer_config(req.message),
                    status="checking the route and travel time...")

            # 5c. TRIP PLANNING — "plan a trip to Japan", "3 days in Rome". Builds a real
            #     day-by-day itinerary card AND a map pinned with the actual places it
            #     names (via build_trip_widgets). Checked BEFORE the map branch so
            #     "trip to japan" isn't grabbed as a bare place map, and reuses
            #     spawn_router_stream so both widgets land in one commit.
            if (TRIP_ASK_RE.search(text_clean)
                    and not wants_removal and not is_video_ask and not is_list_ask):
                return spawn_router_stream(
                    [{"type": "trip", "query": req.message}], reason="planning your trip")

            # 5d. SHOPPING / PRODUCT PICKS — "good outdoor shoes", "best budget laptop".
            #     A grid of reference-photo cards, each linking to its source, instead of
            #     a wall of links. Checked before the map/POI branch (a product ask names
            #     no place) and outside the is_data_ask gate.
            if (req.use_lazy_agent and SHOP_ASK_RE.search(text_clean)
                    and not wants_removal and not is_video_ask and not is_list_ask):
                # PRISM MODE skips this: a shopping ask is real RESEARCH, so it falls
                # through to the prism agent (web_search + read_page harnesses → a
                # synthesised, sourced pick list) instead of a local search-scrape grid.
                return spawn_widget_stream(
                    "products", "products",
                    config_builder=lambda: build_products_config(req.message),
                    status="finding recommendations with photos...")

            # 6. MAP — geo/location queries, and business/POI asks ("coffee shops in
            #    Seattle") which have NO map/where token so MAP_ASK_RE misses them and
            #    they fell through to the agent (a wall-of-links data_card). Pulled OUT
            #    of the is_data_ask gate so "weather in X and a map of Y" still draws a
            #    map; the POI branch stays gated so "restaurant news" still goes to news.
            if ((MAP_ASK_RE.search(text_clean)
                 or ((POI_MAP_RE.search(text_clean) or EAT_MAP_RE.search(text_clean))
                     and not is_data_ask and not is_list_ask))
                    and not wants_removal and not is_video_ask):
                # A POI/eat ask ("where can I get food", "coffee near me") with no place
                # named and no saved city would otherwise map the SERVER's region. Ask
                # the user where they are instead of guessing. Hazard/"where is X" asks
                # (MAP_ASK_RE only) name their own places and skip this.
                _is_poi = bool((POI_MAP_RE.search(text_clean) or EAT_MAP_RE.search(text_clean))
                               and not _NON_POI_GEO_RE.search(text_clean))
                if _is_poi and not poi_query_has_location(req.message):
                    return spawn_widget_stream(
                        "data_card", "askloc",
                        config=build_location_prompt_config(req.message),
                        status="one sec — which city are you in?")
                return spawn_widget_stream(
                    "map", "map",
                    config_builder=lambda: build_map_config(req.message),
                    status="finding the places and building your map...",
                    widget_id=find_existing_widget(req.session_id, "map"))

            # 6b. LIST EDIT — "add greek salad to the grocery list", "also add milk".
            #     Merge into the EXISTING checklist (reuse its widget_id) rather than
            #     spawning a second list. Only fires when a checklist is actually on
            #     the canvas; otherwise falls through to the normal create path.
            if (LIST_EDIT_RE.search(text_clean)
                    and not wants_removal and not is_video_ask):
                existing_list = _extract_existing_checklist(req.session_id)
                if existing_list:
                    ex_id, ex_title, ex_items = existing_list
                    return spawn_widget_stream(
                        "checklist", "checklist",
                        config_builder=lambda: build_list_add_config(
                            req.message, ex_title, ex_items),
                        status=f"adding to “{ex_title}”...",
                        widget_id=ex_id)

            if not wants_removal and not is_video_ask and not is_data_ask:
                is_searching = bool(re.search(r'\b(search|find|look for)\b', text_clean))
                topic = extract_topic(req.message)

                # 2. TIME / CLOCK / TIMER — broadened from bare "clock", which missed
                #    every "time"-phrased ask ("what time is it", "time in tokyo") so
                #    they fell to the agent and "just broke". Also handles timers and
                #    stopwatches, and resolves a place name to an IANA timezone.
                if (re.search(r'\bclock\b|\bwhat\'?s? ?(the )?time\b|\b(the|current) time\b'
                              r'|\btime in\b|\btimer\b|\bcountdown\b|\bstopwatch\b|\bpomodoro\b',
                              text_clean) and not is_searching):
                    if re.search(r'\b(timer|countdown|pomodoro)\b', text_clean):
                        secs = _parse_duration_seconds(req.message) or (
                            25 * 60 if "pomodoro" in text_clean else 60)
                        return spawn_widget_stream("clock", "clock",
                            {"mode": "countdown", "duration_seconds": secs},
                            status=f"starting a {secs//60 or secs}-{'min' if secs>=60 else 'sec'} timer...")
                    if "stopwatch" in text_clean:
                        return spawn_widget_stream("clock", "clock", {"mode": "stopwatch"})
                    tz = _resolve_timezone(req.message)
                    return spawn_widget_stream("clock", "clock",
                                               {"timezone": tz} if tz else {})

                # 3. Music — asking for a music widget always means "start playing
                # it", so autoplay unconditionally instead of requiring the word
                # "play" in the request.
                has_custom_url = "http" in text_clean or "www" in text_clean
                if re.search(r'\b(music|player|radio)\b', text_clean) and not has_custom_url:
                    genre = extract_music_genre(req.message) or "lofi"
                    # "X music/radio" phrasing is genre-shaped ("jungle music"
                    # means the genre, not the band Jungle) — default the mix
                    # pipeline to genre. Named acts come through the LLM router,
                    # which can set kind=artist; a wrong guess here self-corrects
                    # via the widget's genre→artist failover.
                    return spawn_widget_stream("mini_music_player", "music",
                                               {"genre": genre, "kind": "genre", "autoplay": True})

                # 4. LISTS — checked BEFORE notes, so "notes for a grocery list" is a
                #    list, not a blank notepad. A list always gets real items written
                #    into it; an empty checklist is never what anyone asked for.
                if LIST_INTENT_RE.search(text_clean) and not is_searching:
                    return spawn_widget_stream(
                        "checklist", "checklist",
                        config_builder=lambda: build_list_config(req.message),
                        status="writing your list...",
                    )

                # 5. NOTES — a bare "notes"/"notepad" means a blank surface to type on.
                #    "notes about X" means the note should already say something.
                if NOTES_INTENT_RE.search(text_clean) and not is_searching:
                    if not topic:
                        return spawn_widget_stream("notes", "notes", {})
                    return spawn_widget_stream(
                        "notes", "notes",
                        config_builder=lambda: build_notes_config(req.message),
                        status="writing your note...",
                    )

                # (MAP now runs earlier, outside the is_data_ask gate — see above.)

                # 6.9 COMPOSE — a BROAD "tell me about X / give me the rundown on X" ask
                #     deserves the whole picture: an explanation PLUS supporting media
                #     (image, video, recent news), not a lone text card. Plan the
                #     modalities and fan them out as ONE atomic multi-widget commit.
                #     Falls through to the single-widget answer/router when planning
                #     yields <2 modalities (i.e. it's really a narrow ask).
                if (req.use_lazy_agent and (COMPOSE_ASK_RE.search(text_clean) or is_compound_ask)
                        and not wants_removal):
                    plan = await build_composition_plan(req.message)
                    if len(plan) >= 2:
                        logger.info(f"[COMPOSE] {len(plan)} modalities for {req.message[:60]!r}: "
                                    f"{[w['type'] for w in plan]}")
                        return spawn_router_stream(plan, reason="composed answer")

                # 7. ANSWER — recipes, how-tos, definitions, "what/who/when is X".
                #    Synthesised into a readable answer card (Markdown answer + demoted
                #    sources) instead of dumping the user into the ~30-60s agent loop
                #    that returns a wall of links.
                if req.use_lazy_agent and ANSWER_ASK_RE.search(text_clean):
                    # PRISM MODE skips this too: a fact/how-to/definition ask is research —
                    # the prism agent searches + reads pages + synthesises, rather than the
                    # local one-shot answer builder.
                    return spawn_widget_stream(
                        "data_card", "answer",
                        config_builder=lambda: build_answer_config(req.message),
                        status="researching and writing your answer...",
                    )

        # Adopting the client's snapshot is _run_turn's job now (it only does so
        # when no other turn is in flight, so a concurrent turn's stale snapshot
        # can't undo a widget that just landed).
        # The shared awareness bundle (recent turns + canvas inventory + focus)
        # feeds BOTH the router and the agent, so every tier reasons about the
        # conversation thread the same way.
        turn_ctx = build_turn_context(req.session_id, req.current_canvas or "")
        # Where a refining follow-up should land: topical match beats the
        # recency focus when the message names a subject ("tell me more about
        # the costco deals" must not rewrite the sandals card built last turn).
        # Resolved ONCE here so the directive and the message rewrite below
        # can never disagree about the target.
        followup_target = (
            _followup_target_id(req.session_id, turn_ctx.get("focus_id"), req.message)
            if _is_refining_followup(req.message) else None)

        # ── AGENTIC ROUTER (steps 2 & 3) ─────────────────────────────────────
        # Nothing in the fast lane matched. Before dropping into the ~30-60s agent
        # loop (whose miss mode is a wall-of-links card), try a cheap LLM classify
        # → server-built widget(s). Skipped for removals/DOM edits, which need the
        # agent's canvas_modify_dom; the router's own {"defer": true} sends note
        # dictation, custom widgets and small talk to the agent too. A None result
        # (model hiccup) also falls through — the router is never a hard gate.
        # DETERMINISTIC VIDEO/LIVE OVERRIDE — runs BEFORE the classifier.
        #
        # "watch", "video", "live stream" is an unambiguous request to WATCH
        # something; there is nothing for a research agent to add. Moving news to
        # tier 3 made this fragile: the LLM router saw the word "news" in "cnn
        # live news" and classified it as news -> research -> a card of LINKS,
        # when the user plainly asked for a live stream. Worse, it was
        # non-deterministic — "cnn news live video" routed to video while "cnn
        # live news" went to the agent, so the same intent behaved differently
        # run to run. A watch request must never depend on a coin flip.
        #
        # Sports and traffic are excluded because they own the word "live" for
        # their own widgets ("live scores" is a scoreboard, "live traffic" is a
        # map), and music is excluded so "play live jazz" still reaches the
        # player — the same guards the old fast-path branch used.
        if (not wants_removal and not wants_music
                and (is_video_ask
                     or (LIVE_ASK_RE.search(text_clean)
                         and not league
                         and not TRAFFIC_MAP_RE.search(text_clean)))):
            _live = bool(LIVE_ASK_RE.search(text_clean))
            logger.info(f"[ROUTER] tier2-local ['video'] — deterministic "
                        f"{'live-' if _live else ''}video override")
            return spawn_router_stream(
                [{"type": "video", "query": req.message,
                  "modifiers": {"live": _live}}],
                reason="live stream" if _live else "video request")

        # TIER 2 — the classifier backstop. One ~1s classify pass decides whether
        # this ask is a DETERMINISTIC single-source widget (weather, a ticker, a
        # timer, scores, a map, music: exactly one right answer from one API) or
        # RESEARCH. Deterministic ones build locally in ~1-2s; research defers to
        # the prism agent below.
        #
        # This ran only in legacy mode after the bypass removal, so EVERY ask —
        # including "set a 5 minute timer" — paid a 60-90s agent turn. An agent
        # adds latency and a hallucination surface to a weather lookup and buys
        # nothing: the TOOL is what makes it correct, not the planner. Planning
        # only earns its cost when several tools must be sequenced and synthesised
        # (search -> read pages -> write it up), which is what _AGENT_RESEARCH_TYPES
        # captures. A refining follow-up is left to the agent (or to the router's
        # own in-place reuse) so it still updates the open widget rather than
        # spawning a duplicate.
        # Tier 2's verdict has to SURVIVE into tier 3. Bound at FUNCTION scope,
        # not inside the branch below: the SYSTEM_PROMPT build reads these, and
        # the debug event inside the agent proxy closes over them — on the
        # wants_removal path the classifier never runs, so a name bound only
        # inside `if not wants_removal:` would NameError at stream time, i.e. on
        # every "remove the clock".
        router_plan: Optional[dict] = None
        router_specs: list = []          # the plan handed to the agent as a prior
        router_checks: dict = {}         # the pre-flight self-check answers
        router_status = "skipped-removal"   # local | deferred | defer | none
        if not wants_removal:
            router_plan = await route_with_llm(req.message, turn_ctx["context_block"])
            router_checks = (router_plan or {}).get("checks") or {}
            if router_plan and not router_plan.get("defer") and router_plan.get("widgets"):
                widgets = router_plan["widgets"]
                research = [w["type"] for w in widgets if w["type"] in _AGENT_RESEARCH_TYPES]
                if req.use_lazy_agent or not research:
                    router_status = "local"
                    logger.info(f"[ROUTER] tier2-local {[w['type'] for w in widgets]} "
                                f"q={[w['query'][:40] for w in widgets]} "
                                f"— {router_plan.get('reason','')}")
                    return spawn_router_stream(widgets, router_plan.get("reason"))
                # The classification is USABLE and we are about to hand the ask to
                # the agent, which re-derives intent from the raw message with no
                # hint. Carry it. The WHOLE plan, not just the research half: a
                # composite ("weather + answer") defers entirely, so the
                # non-research specs were being dropped here silently too.
                router_specs = widgets
                router_status = "deferred"
                logger.info(f"[ROUTER] tier3-agent: deferring research {research} "
                            f"(of {[w['type'] for w in widgets]}) to the prism agent "
                            f"— hint={[(w['type'], w['query'][:40]) for w in widgets]} "
                            f"checks={router_checks or '{}'}")
            elif router_plan and router_plan.get("defer"):
                router_status = "defer"
                logger.info(f"[ROUTER] tier3-agent: classifier deferred "
                            f"({router_plan.get('reason','')})")
            else:
                router_status = "none"
                logger.info("[ROUTER] tier3-agent: no plan (classifier returned nothing)")

        # ONE dict feeding both the log line and the browser debug event, so the
        # console and the server can never tell different stories about the same
        # turn. Assigned unconditionally here — before every `return` that reaches
        # the agent — because the proxy closes over it.
        router_debug = {
            "status": router_status,
            "widgets": [w["type"] for w in (router_plan or {}).get("widgets") or []],
            "queries": [w["query"] for w in (router_plan or {}).get("widgets") or []],
            "targets": [w.get("target") for w in (router_plan or {}).get("widgets") or []],
            "reason": (router_plan or {}).get("reason", ""),
            "checks": router_checks,
            "hint": bool(router_specs),
        }

        # Start loading history
        history = database.get_session_messages(req.session_id)

        # The user's live app inventory for the prompt (cached ~15ms; "" on a
        # portal outage so a turn is never blocked by it).
        apps_block = await build_apps_prompt_block()
        # What the canvas can DO to those apps (local file read, no network).
        actions_block = await build_actions_prompt_block()

        # Build system prompt with canvas context.
        #
        # Kept deliberately short and free of internal contradictions. The previous
        # version was ~9.3k chars and opened with "NEVER output any conversational
        # text ... the system will crash", then closed by requiring "ONE short
        # sentence saying what you added" — a model asked to reconcile those two
        # spends the turn deciding whether it is allowed to speak, and often resolves
        # it by talking. Instruction-following also decays with instruction count
        # (arXiv 2507.11538), and what gets dropped is whatever sits in the middle,
        # so the act-now rules go first and the per-widget config contracts have been
        # moved into the canvas_add_widget tool description, where the model reads
        # them at the point of use instead of carrying them through the whole turn.
        SYSTEM_PROMPT = (
            "You run a live dashboard canvas. You act by calling tools. You never describe what you would do.\n\n"
            "HOW TO ACT\n"
            "1. Call the tool immediately. Write no preamble, no plan, no 'let me...' before a tool call.\n"
            "2. Never ask for clarification. Take the most reasonable reading and go — 'pull up a video' with no topic means pick one and search.\n"
            "3. Fetch the data before you render it. The config you pass IS the finished content: it renders server-side, so never write 'Loading...' and never write JavaScript that fetches.\n"
            "4. Stop when the widget is up. canvas_add_widget returning success means it is already on the user's screen — do not call it again, do not verify with canvas_read_dom, do not re-plan.\n"
            "5. Then write ONE sentence (max 25 words) that ANSWERS the question — the single most useful thing you found, with the specific number, name or verdict in it. That sentence is the only prose you write all turn, and it is READ ALOUD, so it must stand on its own to someone not looking at the screen.\n"
            "   Say the finding, never the filing. 'Traffic to San Jose is clear, about 35 minutes via 880.' NOT 'Added a traffic map.' 'The Seattle Rain Hat wins on waterproofing; the Sunday Afternoons is cheaper.' NOT 'Here is a card comparing hats.'\n"
            "   Never mention widgets, cards, canvas, or that you added anything — the user can see the screen. If you genuinely found nothing, say what you couldn't find and why, in one sentence.\n"
            "   When the ask was a JUDGEMENT with numbers in it, that sentence carries the verdict AND the number: 'About 6 more minutes — pull it at 165F.' NOT 'Cooking times depend on thickness.' Answer the question that was asked.\n"
            "   Everything you write BEFORE your first tool call is discarded, so do your deciding there freely — but the user only ever sees the sentence you write AFTER the widget is up. Make that one count.\n"
            "6. EVERY turn ends in a canvas mutation. You have NOT finished until canvas_add_widget (or canvas_modify_dom) has succeeded. Never end a turn having only searched, read or reasoned — if a tool fails, render what you already have rather than retrying forever or giving up silently.\n"
            "7. FOLLOW-UPS UPDATE, THEY DON'T STACK. The CANVAS section below lists what is already on screen, each with its id. If this ask REFINES what is already there — filtering it ('only show waterproof ones'), narrowing it, changing or adding to it, or is a bare comparative/pronoun ask ('what about the cheaper ones', 'make it a table') — call canvas_add_widget with that widget's EXISTING id so the server rewrites it IN PLACE. Only mint a new id when the ask opens a genuinely NEW subject.\n\n"
            "ROUTING — pick one and execute it:\n"
            "- stock, share price, ticker, crypto → mcp__lazy-tool-service__html_notes_stock_history, then canvas_add_widget(widget_type='stock_card')\n"
            "- COMPARE tickers ('NVDA vs SPY vs TSM', 'which performed better') → ONE widget, never a stock_card per ticker: canvas_add_widget(widget_type='chart', config={'compare_symbols': ['NVDA','SPY','TSM'], 'range': '6mo'}). The server fetches every ticker and draws a single normalized %-change chart with a legend. Ranges: 1d,5d,1mo,3mo,6mo,1y,5y,10y,max. To add a ticker to an existing comparison, call it again with the SAME widget_id and the full new symbol list.\n"
            "- stock/company/market NEWS, or 'find me stocks' (no specific ticker yet) → RESEARCH IT, same discipline as general news:\n"
            "    1. mcp__lazy-tool-service__html_notes_stock_news(query='<company or ticker>') — its 'matches' array also gives you tickers to feed into html_notes_stock_history.\n"
            "    2. mcp__lazy-tool-service__html_notes_read_page on the 2-3 stories that actually move the thesis.\n"
            "    3. canvas_add_widget(widget_type='data_card', config={'stock_news_query': '<the SAME query>', 'answer': '<your brief>'}) — the server re-pulls the stories and writes a summary per story; do NOT hand-build items from raw title+link rows. Your 'answer' sits on top.\n"
            "    The brief must separate FACT from EXPECTATION: what was actually reported/filed/announced (with dates and figures) vs what analysts merely predict. Name the outlet on any contested or single-sourced claim, and say plainly when the move has no clear reported cause rather than inventing one. Never present a price move as explained when the sources do not explain it.\n"
            "    Never use html_notes_stock_history for news (prices only) or html_notes_web_search for stock news (this is cleaner).\n"
            "- sports scores, fixtures, standings → mcp__lazy-tool-service__html_notes_sports_scores, then canvas_add_widget(widget_type='scoreboard')\n"
            "- video, watch, clip, live stream → mcp__lazy-tool-service__html_notes_youtube_search, then canvas_add_widget(widget_type='youtube_player'). order='live' for a live stream, order='date' for latest news. If the user constrains WHEN ('new', 'newest', 'this week', 'yesterday', 'latest'), copy that time phrase VERBATIM into the tool's freshness parameter and keep it in the query — never drop or paraphrase time words. A plain 'video' ask means a REGULAR video, never a Short: only pass format='short' when the user actually says short/shorts, and pass format='long' when they say 'full video' or 'not a short'. 'cnn live news' is a video request, not headlines.\n"
            "- weather, forecast, temperature → mcp__lazy-tool-service__html_notes_get_weather(location='<city>'), then canvas_add_widget(widget_type='weather', config={'location':'<city>'}) — config is JUST the location; the server fills in the conditions and 5-day forecast. Never render weather as a data_card and never web-search for it.\n"
            "- news, headlines, 'what's happening with X', 'top stories' → RESEARCH IT. Do not stop at a list of headlines:\n"
            "    1. mcp__lazy-tool-service__html_notes_news(topic='<topic>', or topic='' for top stories) — returns current stories, each already carrying a photo and a summary.\n"
            "    2. mcp__lazy-tool-service__html_notes_read_page on the 2-3 MOST IMPORTANT stories. Never write the brief from headlines alone: headlines are where outlets differ most and detail is thinnest.\n"
            "    3. canvas_add_widget(widget_type='data_card', config={'news_topic': '<the SAME topic>', 'answer': '<your brief>'}). Passing news_topic makes the server attach the sourced stories with their photos — do NOT re-type them. Your 'answer' is the value you add ON TOP of them.\n"
            "    The brief is GitHub-flavored Markdown, ~120-200 words, and MUST: lead with WHAT HAPPENED and WHEN (absolute dates, not 'today'); say what MULTIPLE outlets agree on; explicitly flag where they DISAGREE, where a claim is single-sourced, or where something is still unconfirmed; attribute contested claims ('Reuters reports…'); and close with what to watch next. Never state as settled what only one outlet claims, and never invent a detail that was not in what you actually read.\n"
            "    Do NOT use html_notes_web_search for news (it returns news-site homepages, not stories, and no photos).\n"
            "- facts, recipes, how-tos, 'what/who/when is X', comparisons, product picks ('best X under $Y') → mcp__lazy-tool-service__html_notes_web_search(query='<the question>'), then canvas_add_widget(widget_type='data_card', config={'search_query': '<the SAME query>'}). The server reads the top pages, WRITES a summarised Markdown answer (a recipe becomes ingredients+steps, a definition a short paragraph, a comparison a Markdown table) and attaches each page as a source WITH ITS PHOTO. Do NOT hand-build items and do NOT re-type the results — just pass the query back.\n"
            "  Use 'search_query' for these, never 'news_topic'. news_topic is ONLY for current-events asks you researched with html_notes_news; on anything else it costs you the sources and the pictures.\n"
            "- WHERE something is / a map / locations ('where are the fires in California', 'map of X') → canvas_add_widget(widget_type='map', config={'map_query': '<the query>'}). The server web-searches, geocodes the places and drops the markers — do NOT type coordinates.\n"
            "- picture of X → canvas_add_widget(widget_type='image', config={'image_query': '<what to show>'}). You have NO image tool and CANNOT know real image URLs — NEVER write 'url' or 'images' entries; a URL you produce is fabricated and renders as the wrong picture or a broken frame. The server searches, vision-checks and captions real pictures from image_query. 'Show me X vs Y' is still ONE image widget: image_query='X vs Y'.\n"
            "- COMPARE / contrast / 'which is better' between 2-4 NAMED things → research them, then ONE canvas_add_widget(widget_type='versus_card', config={'title':'X vs Y', 'entities':[{'name':'X'},{'name':'Y'}], 'rows':[{'label':'<metric>', 'values':['<X value>','<Y value>'], 'winner':<0-based index of the better value, or null>}], 'verdict':'<one-sentence call>'}) — aligned columns with the winner highlighted per row. An open-ended comparison with no clear entities → data_card with config={'search_query': '<X vs Y>'}. If the user explicitly asked to SEE them, ALSO add ONE image widget with image_query naming both subjects. Never answer a comparison with images alone.\n"
            "- COMPARE non-ticker SERIES over the same x-axis ('rainfall in Seattle vs Portland by month', 'GDP growth of US vs China') → canvas_add_widget(widget_type='multi_chart', config={'title':…, 'labels':[<shared x labels>], 'series':[{'label':'Seattle','values':[…]}, {'label':'Portland','values':[…]}], 'unit':'<y unit>'}) — ONE chart, never one chart per thing. Tickers keep the compare_symbols chart above.\n"
            "- structured / ranked rows ('top 10 EVs by range and price', specs, standings, any 5+ row listing with columns) → canvas_add_widget(widget_type='table', config={'title':…, 'columns':[{'key':'model','label':'Model'},{'key':'price','label':'Price','format':'currency'}], 'rows':[{'model':…, 'price':…}], 'sort':{'key':'price','dir':'asc'}}) — right-aligned formatted numbers; never cram a big table into a data_card.\n"
            "- a few HEADLINE NUMBERS ('how is the US economy doing', 'key stats for Tesla's quarter', before/after) → canvas_add_widget(widget_type='kpi_row', config={'title':…, 'metrics':[{'label':'CPI YoY','value':'2.7','unit':'%','delta':'-0.3 vs May','good':'down'}]}) — 2-6 big-number tiles with colored deltas ('good' says which direction is healthy).\n"
            "- 'timeline of X' / 'how did X unfold' / 'what led up to X' → canvas_add_widget(widget_type='timeline', config={'timeline_query':'<the topic>'}). The server researches the news and builds dated events with sources — do NOT hand-build events unless you already read the pages; then pass config={'events':[{'date':'YYYY-MM-DD','title':…,'description':…,'url':…}]} with absolute dates and NEVER an image url.\n"
            "- 'who is X' / 'tell me about <person/company/place>' → canvas_add_widget(widget_type='profile_card', config={'profile_query':'<the subject>'}). The server builds the portrait + facts infobox — never type an image url.\n"
            "- goals / tracking / percentage breakdowns ('savings goals', 'EV market share by maker') → canvas_add_widget(widget_type='progress', config={'title':…, 'items':[{'label':…, 'value':8200, 'target':10000, 'unit':'$'} or {'label':…, 'pct':48}]}).\n"
            "- clock, checklist, notes → canvas_add_widget with that widget_type; music/radio → widget_type='mini_music_player' (config={'genre':…}); embed a site/app → widget_type='iframe_app' (config={'url':…})\n"
            "- the user's OWN apps/services ('show my apps', 'app hub', 'what's running') → canvas_add_widget(widget_type='app_grid', config={}) — the server fills the live app list from portal-service; never hand-build it. mcp__lazy-tool-service__html_notes_list_services shows what exists; html_notes_curate_app(app_id, hidden=…/pinned=…) hides or pins a tile on the hub.\n"
            "- OPEN / LAUNCH / 'pull up' / 'go to' SOMETHING THAT NAMES OR RESEMBLES AN APP IN 'YOUR APPS' BELOW → mcp__lazy-tool-service__html_notes_open_app(app_id='<id or the name they said>') and NOTHING ELSE. Check YOUR APPS FIRST, before you consider any widget: those names are the user's own containers. 'open the trading bot', 'pull up the music player app', 'go to braindeadbot' are all app opens. NEVER spawn a widget for an app name, never web-search it, never hand-build a data_card about it, and never paste a URL expecting it to open — this tool is the only way. Prefer the CLIENT (`*-client`, or a standalone repo like music-player): a `*-service` backend is only ever opened when the user names it in full ('open trading service'). The tab opens by itself, so just confirm in ONE short sentence. If the result carries 'candidates', list them as markdown links and ask which — never guess.\n"
            "- A QUESTION THAT HAPPENS TO CONTAIN NUMBERS IS NOT A CALCULATION. 'how long until my 145F chicken hits 165 in a 400F oven', 'is 10 more minutes enough', 'what temp should I pull the brisket at', 'how long to drive to SF', 'is 3 drinks over the limit' — the user wants a JUDGEMENT that needs real-world knowledge, not arithmetic. Cooking times, doneness and food safety, dosages, travel times and legal limits are ALL research asks → mcp__lazy-tool-service__html_notes_web_search(query='<the question>'), then canvas_add_widget(widget_type='data_card', config={'search_query': '<the SAME query>'}). A converter cannot answer any of them.\n"
            "- CONVERT or CALCULATE — only when the ask IS the arithmetic and nothing else: an explicit 'convert'/'calculate' ('convert 10 kg to lb'), a bare expression ('what is 15*23', '(3+4)*2'), a percentage ('40% of 1250'), or a bare '<amount> <unit> to <unit>' ('5 miles in km', '20 usd to eur') → canvas_add_widget(widget_type='converter', config={'seed':'<the whole ask>'}). The widget does the math client-side and stays interactive — never compute it yourself in prose. NEVER pick converter because the message merely CONTAINS numbers, temperatures, weights, times or currency; if the ask is a question about what those numbers MEAN or what to DO, use the research route on the line above.\n"
            "- REMIND / alarm ('remind me in 20 min', 'remind me at 3pm to call mom', 'set an alarm for 7am') → canvas_add_widget(widget_type='reminder', config={'label':'<what to remind>', 'offset_seconds':<N for a relative time, else 0>, 'at_time':'<HH:MM 24h for an absolute time, else empty>'}). The widget counts down and alerts.\n"
            "- APPEARANCE / theme / colors ('dark mode', 'forest theme', 'make it pastel', 'egg colors'), OR settings/preferences → canvas_add_widget(widget_type='settings', config={'theme':'<what the user asked — e.g. dark, forest, pastel, egg, sunset, purple>'}). The server picks the CLOSEST palette from what you pass and applies it; omit 'theme' to just open settings without changing the look. This is the ONLY way to change the theme — never hand-edit colors. It's a singleton, so it updates in place.\n"
            "- timer, countdown, pomodoro → canvas_add_widget(widget_type='clock', config={'mode':'countdown','duration_seconds':N}); stopwatch → config={'mode':'stopwatch'}; 'time in <city>' → config={'mode':'clock','timezone':'<IANA tz>'}. NEVER spawn a plain clock for a timer request.\n"
            "- EDIT an existing widget (change a timer's duration, a clock's timezone, a chart's data, swap the stock) → call canvas_add_widget AGAIN with the SAME widget_id from CURRENT CANVAS and the full updated config. It re-renders that widget in place — no duplicate. This is the ONLY way to change a clock/timer/stock/scoreboard/chart: canvas_modify_dom CANNOT rebuild these (they are server-rendered) and will break them. Example: to set the timer #clock-1 to 30s → canvas_add_widget(widget_type='clock', widget_id='clock-1', config={'mode':'countdown','duration_seconds':30}).\n"
            "- REMOVE something, or tweak a hand-built custom widget → mcp__lazy-tool-service__canvas_modify_dom(css_selector='#<widget-id>', action='remove'|'replace') using an id from CURRENT CANVAS\n\n"
            "ANSWER FROM DATA, NEVER FROM MEMORY\n"
            "You know nothing current. If the answer is not already in this conversation, call html_notes_web_search before answering — never claim you cannot find or cannot access something without having searched first.\n"
            "For a data_card, prefer the search_query path above: pass config={'search_query': '<query>'} and let the server write the summarised answer with sources. Only hand-build config.items when you have specific structured rows that no search summary would capture — and then every item still needs a 'description' with the real information, never just a title and a link.\n\n"
            "WIDGETS COEXIST. Adding one never removes the others. The exceptions are youtube_player and mini_music_player: only one of each can play, so a new one automatically swaps out the old — just add it, do not remove first.\n\n"
            "FOLLOW-UPS UPDATE THE OPEN WIDGET. RECENT TURNS below shows what each "
            "widget already covers. If the user is refining one of them (\"what "
            "about the taco bell story?\", \"tell me more\", \"and the away team?\"), "
            "call canvas_add_widget with that widget's SAME id to rewrite it in "
            "place — do not add a near-duplicate card.\n\n"
            "NAMES RESOLVE AGAINST THE CONVERSATION FIRST. Before interpreting any "
            "name in the ask, scan CURRENT CANVAS and RECENT TURNS for it. If it "
            "appears there — a restaurant on a card, a product in a list, a team on "
            "a scoreboard — the user means THAT one, and the conversation's meaning "
            "BEATS the famous meaning: after a sushi card listing Miku, \"tell me "
            "more about Miku\" is the Vancouver restaurant, never the vocaloid. "
            "Only when the name appears nowhere in the context does its common "
            "meaning apply.\n\n"
            + _user_facts_prompt()
            # The user's OWN app inventory, live from portal-service. Sits in
            # the recency window (not the ROUTING list) for the same reason
            # the pre-flight block does: instruction-following decays with
            # count and the MIDDLE is what gets dropped. Without it the agent
            # had no way to know "trading bot" names a container of theirs —
            # it web-searched and rendered a junk card.
            + apps_block
            + actions_block
            + f"{turn_ctx['context_block']}"
            # TIER-2's VERDICT, placed in the recency window. The note above
            # (instruction-following decays with instruction count and what gets
            # dropped is the MIDDLE) is exactly why this is not spliced into the
            # ROUTING list ~60 lines up. It sits AFTER the canvas and history the
            # classifier itself read — context, then verdict, then target — and
            # BEFORE the follow-up directive, which keeps last position: that
            # directive is the more brittle of the two (a weaker, non-last
            # version measurably produced prose and zero tool calls), and the two
            # do not compete — this block says WHAT to fetch, the directive says
            # WHERE to write it.
            + _preflight_block(router_specs, router_checks)
            # A CONCRETE, last-position directive naming the actual widget id.
            # The generic "FOLLOW-UPS UPDATE THE OPEN WIDGET" paragraph above was
            # not enough: on a terse ask ("what about cheaper ones") the model
            # read it as conversation, emitted prose and called NO tools, so the
            # canvas never changed and the user had to refresh. Naming the id and
            # mandating a mutation is what actually lands — the same ask phrased
            # explicitly already worked.
            + (
                f"\n\nTHIS TURN IS A FOLLOW-UP. The widget #{followup_target}"
                + (f" (currently showing: {_widget_showing(req.session_id, followup_target, req.message)})"
                   if _widget_showing(req.session_id, followup_target, req.message) else "")
                + f" is already on screen and this ask REFINES it — it is a canvas "
                f"request, not conversation. Any name in the ask refers to that "
                f"widget's content, NOT to whatever the name means elsewhere. "
                f"Fetch any new data you need, then "
                f"call canvas_add_widget with widget_id='{followup_target}' "
                f"to rewrite that widget IN PLACE. Do not open a new widget and "
                f"do not answer in prose: you MUST end this turn with a canvas "
                f"mutation."
                if followup_target
                else ""
            )
        )

        # Observability: why the agent turn behaved the way it did. Without this
        # a "the follow-up did nothing" report is unfalsifiable — you cannot tell
        # a missing focus widget from an ignored instruction.
        logger.info(
            f"[AGENT TURN] focus_id={turn_ctx.get('focus_id')!r} "
            f"followup_target={followup_target!r} "
            f"refining_followup={_is_refining_followup(req.message)} "
            f"directive={'YES' if followup_target else 'no'} "
            f"ledger_widgets={len(turn_ctx.get('inventory') or '')>0} "
            f"msg={req.message[:60]!r}")

        # Ensure all possible tools are enabled
        enabled_tools = [
            "mcp__lazy-tool-service__html_notes_create_note",
            "mcp__lazy-tool-service__html_notes_update_note",
            "mcp__lazy-tool-service__html_notes_get_note",
            "mcp__lazy-tool-service__html_notes_search_notes",
            "mcp__lazy-tool-service__html_notes_link_notes",
            "mcp__lazy-tool-service__canvas_read_dom",
            "mcp__lazy-tool-service__canvas_add_widget",
            "mcp__lazy-tool-service__canvas_modify_dom",
            "mcp__lazy-tool-service__html_notes_youtube_search",
            "mcp__lazy-tool-service__create_widget",
            "mcp__lazy-tool-service__update_widget",
            "mcp__lazy-tool-service__validate_widget_html",
            "mcp__lazy-tool-service__list_widget_types",
            "mcp__lazy-tool-service__plan_widget",
            # Web research. These are served by HTML-Notes itself (via
            # scraper-service) because every tools-api data tool — search_web,
            # search_news, read_web_page, get_weather, get_time_in_timezone —
            # is registered in the gateway catalog with a null endpoint and its
            # python bridge has no interpreter in the image, so they all return
            # "Unknown tool". Enabling them just gave the model phantom tools to
            # fail against, which is why it kept answering "I couldn't get one".
            "mcp__lazy-tool-service__html_notes_web_search",
            "mcp__lazy-tool-service__html_notes_read_page",
            "mcp__lazy-tool-service__html_notes_news",
            "mcp__lazy-tool-service__html_notes_stock_history",
            "mcp__lazy-tool-service__html_notes_stock_news",
            "mcp__lazy-tool-service__html_notes_sports_scores",
            "mcp__lazy-tool-service__html_notes_get_weather",
            # App Hub: list the user's own services, open one in a new browser
            # tab (approved catalog ids only — never raw URLs), hide/pin tiles.
            "mcp__lazy-tool-service__html_notes_list_services",
            "mcp__lazy-tool-service__html_notes_open_app",
            "mcp__lazy-tool-service__html_notes_curate_app",
            # Control plane: run registered actions on the other containers.
            "mcp__lazy-tool-service__html_notes_app_action",
            "mcp__lazy-tool-service__html_notes_list_actions",
        ]

        # Build messages array — use system role at index 0.
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

        # Only include recent history to avoid context overflow (last 10 messages)
        recent_history = history[-3:]
        for h in recent_history:
            content = h["content"]

            # Compress large HTML chunks in history
            if h["role"] == "assistant":
                # Replace the canvas HTML with a FACTUAL summary naming each
                # widget and its id — never a bare prose placeholder.
                #
                # This used to substitute the literal string
                # "[Visual Component Rendered]". That reads to the model as a
                # perfectly good assistant reply, so on a follow-up it copied the
                # pattern: it emitted that exact text and called NO tools, leaving
                # the canvas untouched (the user had to refresh, and saw only the
                # previous turn's widget). Naming the widgets instead both kills
                # the mimicry and tells the model which id to reuse when the
                # follow-up refines something already on screen (prompt rule 7).
                content = re.sub(
                    r'<!--CANVAS_HTML_START-->.*?<!--CANVAS_HTML_END-->',
                    lambda m: _summarize_canvas_for_history(m.group(0)),
                    content, flags=re.DOTALL)
                # Fallback for old history: strip common classes
                content = re.sub(r'<div class="[^"]*(glass-card|canvas-element|rendered-component)[^"]*">.*?</div>', '[Component]', content, flags=re.DOTALL)
                
                # Truncate very long assistant messages just in case
                if len(content) > 2000:
                    content = content[:2000] + "... [truncated]"

            # Skip tool-only placeholder messages
            if content == "[tool-only turn]":
                continue

            messages.append({"role": h["role"], "content": content})

        # MODEL COMPLIANCE: rewrite a terse refinement into the explicit form.
        #
        # A system-prompt directive naming the widget id was measurably NOT
        # enough — the [AGENT TURN] log showed focus_id set, refinement detected
        # and directive=YES, and the model STILL answered "what about cheaper
        # ones" in prose with zero tool calls, leaving the canvas untouched. The
        # same request phrased explicitly ("update the existing widget to only
        # show waterproof ones") called canvas_add_widget correctly every time.
        # So we hand the model the phrasing it actually obeys. Only what the
        # AGENT sees is rewritten; the stored/displayed user message (already
        # saved above) is untouched, so the chat transcript still reads normally.
        if followup_target:
            # The anchor ("currently showing: ...") is what resolves an
            # ambiguous name in the ask against the THREAD instead of world
            # knowledge — id alone told the model where, not what about.
            _showing = _widget_showing(req.session_id, followup_target, req.message)
            for _i in range(len(messages) - 1, -1, -1):
                if messages[_i]["role"] == "user":
                    messages[_i]["content"] = (
                        f"Update the existing widget #{followup_target} IN PLACE."
                        + (f" It currently shows: {_showing}." if _showing else "")
                        + f" Fetch whatever new data is needed, then call "
                        f"canvas_add_widget with widget_id='{followup_target}' "
                        f"and the full updated config. Do not create a new widget "
                        f"and do not reply in prose. Names in the request refer "
                        f"to this widget's content. The change to make: "
                        f"{req.message}")
                    logger.info(f"[AGENT TURN] rewrote follow-up -> explicit update "
                                f"of #{followup_target}")
                    break

        # The lazy-tool-service gateway runs the agentic loop and executes the
        # mcp__lazy-tool-service__* widget tools; plain Prism has no such
        # catalog registered, so LazyAgent is the default.
        target_url = LAZY_AGENT_URL if req.use_lazy_agent else PRISM_URL

        # Build /agent payload — NO tools array (the gateway uses its own catalog)
        model_name = req.model
        if not model_name:
            # Discover a provider/model pair from the gateway's local catalog so
            # the provider instance and model always match (e.g. "vllm" serves a
            # different model than "vllm-2").
            try:
                # /config-local probes each vLLM instance and takes ~3s itself,
                # so the client timeout must sit well above that.
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(f"{target_url}/config-local")
                    if resp.status_code == 200:
                        local_models = resp.json().get("models", {})
                        for provider_id, provider_models in local_models.items():
                            for m in provider_models:
                                if m.get("modelType") == "conversation" and "Tool Calling" in (m.get("tools") or []):
                                    req.provider = provider_id
                                    model_name = m.get("name")
                                    break
                            if model_name:
                                break
            except Exception as e:
                logger.warning(f"Failed to fetch model catalog from {target_url}: {e}")
        if not model_name:
            # VLLM_URL is Gold Spark (10.0.0.141), registered as provider
            # "vllm-2" in the gateway — the pair must match or the gateway
            # 404s with "model does not exist" on the wrong instance.
            try:
                with httpx.Client(timeout=2.0) as client:
                    resp = client.get(f"{VLLM_URL}/v1/models")
                    if resp.status_code == 200:
                        models_data = resp.json().get("data", [])
                        if models_data:
                            model_name = models_data[0].get("id")
                            req.provider = "vllm-2"
            except Exception as e:
                logger.warning(f"Failed to fetch dynamic model from {VLLM_URL}: {e}")
            if not model_name:
                # Last resort: the Jetson instance/model pair.
                req.provider = "vllm"
                model_name = "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"
        # REMOVED: a PUT {target_url}/settings that rewrote the gateway's GLOBAL
        # memory.extractionModel on EVERY turn. Three problems: (1) a client has no
        # business mutating a shared gateway's global config — it raced every other
        # project on prism; (2) it was sent with no x-project/x-username headers, so
        # it landed in prism's "default" project and helped make that bucket
        # unattributable; (3) it used a BLOCKING httpx.Client inside this async
        # handler, stalling the event loop up to 1s per turn. We send
        # memoryEnabled=False anyway, so nothing here needs the extractor.

        payload = {
            "provider": req.provider,
            "model": model_name,
            # Belt and braces with the persona's thinkingDefault: a widget
            # router gains nothing from chain-of-thought and the user is
            # watching a spinner while it streams.
            "thinkingEnabled": False,
            "workspaceRoot": "/home/lazycat/github/projects/sun/HTML-Notes",
            "workspaceEnabled": False,
            "enabledTools": enabled_tools,
            "messages": messages,
            "maxTokens": 4096,
            # Neither of these was set, so the gateway used its defaults: temperature
            # 0.7 and 25 iterations. A canvas task is not a creative one — it is
            # "pick the right tool, fill in the args" — and a warm model deliberates
            # its way there, then keeps narrating afterwards. Measured: the first
            # tool call landed at ~8s but the turn ran 70s, with 91-189 reasoning
            # events, most of them AFTER the widget was already on screen.
            "temperature": 0.15,
            # 6 was sized for "one data tool, then render". A tier-3 research turn
            # is now search -> read 2-3 pages -> render, which is 5-6 calls before
            # the model has said anything — at 6 it could burn the budget mid-read
            # and finish with no canvas mutation. 9 leaves headroom for one failed
            # tool call (the MCP layer does intermittently error) without the turn
            # ending empty. The proxy still stops reading at the first canvas
            # commit, so this raises the ceiling, not the typical cost.
            "maxIterations": 9,
            "project": AGENT_PROJECT,
            "username": AGENT_USERNAME,
            "skipConversation": True,
            "autoApprove": True,
            "memoryEnabled": False
        }
        # The HTML_NOTES persona (personas/clients/HtmlNotesPersona.ts — scopes the
        # run to the widget tool set, thinking off) ships ONLY in the :5591 fork.
        # Canonical prism (:7777) 404s on it ("unknown agent: html_notes"). When we
        # target prism we run persona-less: the explicit SYSTEM_PROMPT + enabledTools
        # already scope the turn, and the connected lazy-tool-service MCP server
        # supplies the same mcp__lazy-tool-service__* research tools (verified live).
        #
        # 2026-08-16: the gateway is ALSO persona-less for now. The newly ported
        # agentic-loop harness returns an EMPTY stream on iteration 1 whenever
        # `agent` names a persona (bisected live: identical payload with the
        # field dropped works and completes the open_app flow; with
        # agent='HTML_NOTES' → 0 input tokens, no shim POST, "Empty model
        # output"). Persona-less loses nothing here: enabledTools still scopes
        # the run (AgenticToolResolver honours it) and SYSTEM_PROMPT carries
        # the routing rules. Re-add FORK_AGENT_ID once the harness persona
        # path is fixed (open item in lazy-agent-service docs).
        if not req.use_lazy_agent:
            payload["agent"] = PRISM_AGENT_ID

        async def proxy_prism_sse():
            """
            Stream Prism's /agent SSE events to the frontend.
            Prism handles the full agentic loop (tool calls, execution, re-prompting).
            We just proxy events and extract render_component results for the canvas.
            """
            final_text = ""
            # Only a mirror of the last commit, for the DB write at the end of the
            # turn. It is never used as the base for a mutation — commit_canvas
            # re-reads the live canvas under the lock, so a sibling turn's widget
            # can't be lost.
            all_rendered_html = get_session_canvas(req.session_id) or req.current_canvas or ""
            executed_active_tool = False
            executed_mutations: set = set()
            # Apps already opened this turn. The model routinely re-emits the
            # same html_notes_open_app after its ack (same behaviour as the
            # canvas_add_widget re-emit) — without this, one intent opened TWO
            # tabs (observed live 2026-08-16: two open_url frames for one ask).
            emitted_open_apps: set = set()

            # Once the widget is on screen the turn is, from the user's point of
            # view, over. Measured: the widget landed at ~8s and the model then
            # spent another 60s reasoning and re-adding it. So we stop reading the
            # agent's stream as soon as a canvas mutation commits.
            #
            # The exception is a request that wants more than one widget: an explicit
            # conjunction ("a clock AND a chart"), OR a broad/compositional ask ("tell
            # me about X") where an explanation + supporting media serve it better than
            # a lone card. Those keep the loop open — but a hard cap stops a runaway
            # model from stacking widgets forever.
            wants_multiple = bool(re.search(r'\band\b|\balso\b|,|\bthen\b', text_clean)) \
                or bool(COMPOSE_ASK_RE.search(text_clean))
            _MAX_AGENT_WIDGETS = 4
            widgets_committed = 0
            # What the most recent commit actually rendered, recorded by
            # execute_mutation: (widget_type, config, resolved_widget_id).
            # The id is resolved INSIDE that helper, so the model's raw
            # tool args are not a reliable source for it.
            last_committed = None
            canvas_settled = False
            # Tool names prism handed the model that we have no canvas handler for.
            # Collected so a turn that commits nothing can say WHY in the logs.
            unhandled_tools: List[str] = []
            # Provisional widgets: real tool results committed to the canvas the
            # moment the tool finishes, so the user sees the articles while the
            # agent is still composing. Deliberately INVISIBLE to all turn
            # settlement (widgets_committed / canvas_settled / executed_mutations)
            # — a provisional commit must never end the turn or suppress the
            # agent's final canvas_add_widget. widget_id -> {"topic", "config"};
            # topic -> widget_id for routing the final commit onto the same node.
            provisional_widgets: Dict[str, dict] = {}
            provisional_by_topic: Dict[str, str] = {}
            # Repeat ledger, keyed on (tool, args). Pure insurance: a healthy
            # research turn measures 3 tool calls with 1 repeat, so these
            # thresholds sit far above normal. They exist because a single broken
            # tool once produced 18 identical calls over 280s — the tool was
            # returning "retry with a shorter query" while its backend was
            # unreachable, and nothing capped the damage. Never tune these DOWN
            # toward normal; a research turn legitimately repeats a search once.
            tool_repeats: Dict[str, int] = {}
            research_calls = 0

            nonlocal_last_committed = {"v": None}
            # Whether the LAST execute_mutation call actually produced a canvas
            # commit. The callers used to count every call as a committed widget
            # and end the turn — commit_canvas returning None (hallucinated
            # selector, swallowed render exception, no-op update) was reported to
            # the user as success ("Added it to your canvas." over an unchanged
            # canvas). Only a real component frame counts now.
            mutation_outcome = {"committed": False}

            async def _commit_provisional_from_tool(tool_name, tool_args, event):
                """A whitelisted research tool just finished — its result is a
                renderable config sitting in the tool cache (the tool executed
                inside THIS process via /internal/execute and cached before
                returning). Commit it to the canvas NOW, flagged provisional, so
                the user reads the articles while the agent composes.

                Bypasses execute_mutation on purpose: it must not register in
                executed_mutations (would dedupe-away the agent's final commit)
                nor count toward widgets_committed (would settle the turn)."""
                key_fn, wtype = _PROVISIONAL_TOOLS[tool_name]
                cache_key = key_fn(tool_args or {})
                topic = cache_key.split(":", 1)[1]
                if not topic:
                    return
                cfg = get_cached_tool_result(cache_key)
                if not isinstance(cfg, dict):
                    # Fallback: the result prism echoed on the wire. May be
                    # MCP-wrapped, so only accept a plain renderable dict.
                    wire = (event.get("tool") or {}).get("result")
                    cfg = wire if isinstance(wire, dict) else None
                if not (isinstance(cfg, dict) and cfg.get("items")
                        and not cfg.get("is_error")):
                    return  # error / empty fetch — nothing worth previewing
                tkey = topic.lower().strip()
                wid = provisional_by_topic.get(tkey)
                if not wid:
                    wid = _resolve_agent_widget_id(req.session_id, wtype, "",
                                                   req.message,
                                                   req.focus_widget_id or "")
                    if wid in provisional_widgets:
                        # Second distinct topic this turn resolved to the same
                        # node — give it its own deterministic id instead.
                        wid = f"news-{hashlib.md5(tkey.encode()).hexdigest()[:8]}"
                # NEVER mutate the cached dict — _resolve_news_topic_config
                # hands out the same object to the agent's final commit.
                pcfg = {**cfg, "provisional": True}

                def _place(soup, _wid=wid, _cfg=pcfg, _wt=wtype):
                    html = generate_widget_html(_wt, _wid, _cfg)
                    existing = soup.find(id=_wid)
                    if existing is not None:
                        existing.replace_with(BeautifulSoup(html, "html.parser"))
                    else:
                        grid = soup.select_one("#dashboard-grid") or soup
                        grid.append(BeautifulSoup(html, "html.parser"))

                evt = await commit_canvas(req.session_id, _place)
                if evt:
                    provisional_widgets[wid] = {"topic": tkey, "config": cfg}
                    provisional_by_topic[tkey] = wid
                    _remember_widget_config(req.session_id, wid, pcfg)
                    logger.info(f"[PROVISIONAL] {tool_name} -> #{wid} "
                                f"({len(cfg.get('items') or [])} items) while the "
                                f"agent composes")
                    yield evt
                    yield f'data: {json.dumps({"type": "status", "message": "articles ready — composing the full story…", "phase": _PHASE_COMPOSING})}\n\n'

            async def execute_mutation(tool_name, tool_args):
                nonlocal all_rendered_html
                mutation_outcome["committed"] = False
                # The model routinely re-emits the same canvas_add_widget a second
                # time after it has already succeeded. executed_active_tool resets
                # whenever a new tool starts, so the repeat used to run again —
                # rebuilding the same widget and paying another full iteration
                # (~13k-token prefill at a 0% KV-cache hit) for nothing.
                signature = json.dumps({"t": tool_name, "a": tool_args}, sort_keys=True, default=str)
                if signature in executed_mutations:
                    logger.info(f"[WIDGET INJECTOR] Skipping duplicate {tool_name}")
                    # The first execution already put this widget on the canvas —
                    # report "committed" so the caller settles the turn instead of
                    # surfacing a phantom failure.
                    mutation_outcome["committed"] = True
                    return
                executed_mutations.add(signature)

                logger.info(f"[WIDGET INJECTOR] Executing mutation for {tool_name} with args: {tool_args}")
                yield f'data: {json.dumps({"type": "status", "message": f"executing {tool_name}..."})}\n\n'

                async def emit(mutate):
                    """Commit against the LIVE canvas, not this turn's snapshot —
                    a sibling turn may have added a widget since we started."""
                    nonlocal all_rendered_html
                    event = await commit_canvas(req.session_id, mutate)
                    if event:
                        all_rendered_html = get_session_canvas(req.session_id)
                        mutation_outcome["committed"] = True
                    return event

                try:
                    if tool_name == "mcp__lazy-tool-service__canvas_modify_dom":
                        css_selector = tool_args.get("css_selector", "")
                        action = tool_args.get("action", "")
                        html_snippet = tool_args.get("html_snippet", "")

                        def _modify(soup):
                            target = soup.select_one(css_selector)
                            if not target:
                                return False
                            # Invalidate the containing widget's content signature
                            # BEFORE mutating (replace/remove may detach `target`).
                            # The sig is computed from type+config at render time
                            # only; leaving it unchanged made the client reconciler
                            # early-return on "unchanged" and silently drop this
                            # very edit — server said success, DB updated, screen
                            # frozen until a reload.
                            try:
                                classes = target.get("class") or []
                                root = (target if ("widget-container" in classes
                                                   or "glass-card" in classes)
                                        else (target.find_parent(class_="widget-container")
                                              or target.find_parent(class_="glass-card")))
                                if root is not None and root.get("data-sig"):
                                    root["data-sig"] = f"mod-{uuid.uuid4().hex[:12]}"
                            except Exception:
                                pass
                            # The tool schema advertises six actions and this
                            # committing path implemented three. prepend /
                            # insert_before / insert_after fell off the end and
                            # returned None — and commit_canvas only aborts on an
                            # explicit False, so it committed the UNCHANGED canvas,
                            # bumped the version and emitted a component frame.
                            # The model was told {"success": true} for a mutation
                            # that never happened ("put a header above the chart"
                            # → nothing, no error). The note-level sibling at
                            # html_notes_modify_dom has had all six all along.
                            snippet = lambda: BeautifulSoup(html_snippet, 'html.parser')
                            if action == "append":
                                target.append(snippet())
                            elif action == "prepend":
                                target.insert(0, snippet())
                            elif action == "insert_before":
                                target.insert_before(snippet())
                            elif action == "insert_after":
                                target.insert_after(snippet())
                            elif action == "replace":
                                target.replace_with(snippet())
                            elif action == "remove":
                                target.decompose()
                            else:
                                # Unknown action: abort rather than silently
                                # reporting success on a no-op.
                                logger.warning(f"[CANVAS] unknown modify action {action!r}")
                                return False

                        event = await emit(_modify)
                        if event:
                            yield event

                        logger.info("[FAST LOOP] Terminating early after canvas_modify_dom to save latency")

                    elif tool_name == "mcp__lazy-tool-service__canvas_add_widget":
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {}

                        widget_type = tool_args.get("widget_type", "")
                        # Never trust the model's id blind: a near-miss id used to
                        # silently append a NEW widget instead of updating the one
                        # the follow-up came from. Resolve against the real canvas.
                        widget_id = _resolve_agent_widget_id(
                            req.session_id, widget_type,
                            tool_args.get("widget_id", ""), req.message,
                            req.focus_widget_id or "")
                        nonlocal_last_committed["v"] = (widget_type, tool_args.get("config", {}),
                                                        widget_id)
                        config = tool_args.get("config", {})
                        # Some models pass config as a JSON string — normalize.
                        if isinstance(config, str):
                            try:
                                config = json.loads(config)
                            except Exception:
                                config = {}

                        # If this turn put up a provisional preview for the same
                        # topic, the final commit must land ON that node (id
                        # resolution usually finds it topically; this makes it
                        # deterministic). Final config carries no `provisional`
                        # key, so the re-render clears the composing badge.
                        if (widget_type == "data_card" and provisional_by_topic
                                and widget_id not in provisional_widgets
                                and isinstance(config, dict)):
                            _ptopic = str(config.get("news_topic")
                                          or config.get("topic") or "").lower().strip()
                            if _ptopic and _ptopic in provisional_by_topic:
                                widget_id = provisional_by_topic[_ptopic]
                                logger.info(f"[PROVISIONAL] final commit snapped to "
                                            f"preview widget #{widget_id}")
                        nonlocal_last_committed["v"] = (widget_type, config, widget_id)

                        # Settings panel: a singleton appearance/prefs surface.
                        # The server owns the theme catalog and resolves the
                        # user's phrasing ("dark mode", "forest vibe") to the
                        # closest palette; the widget applies it client-side.
                        if widget_type == "settings":
                            widget_id = "settings-panel"   # one panel, reused
                            requested = (config.get("theme") or config.get("name")
                                         or config.get("palette") or "")
                            apply = pick_theme(str(requested)) or pick_theme(req.message)
                            config = {
                                "themes": [{"name": t["name"], "label": t["label"],
                                            "swatch": t["swatch"]} for t in THEME_CATALOG],
                                "active": apply or "hud",
                                "apply": apply or "",
                            }
                            if apply:
                                logger.info(f"[WIDGET INJECTOR] Settings: applying theme {apply!r}")

                        # THE CONVERTER IS FOR ARITHMETIC, NOT FOR QUESTIONS THAT
                        # CONTAIN NUMBERS. Live failure 2026-07-31: "145F chicken
                        # breast ... how long to get to 165 ... 25 minutes in the
                        # oven at 400F" rendered a unit calculator. Three things
                        # pushed it there and no single one is fixable in prose:
                        # rule 6 forces a canvas mutation every turn, converter's
                        # `seed` invites "the whole ask", and build_converter_config's
                        # _UNIT_WORDS maps f/c/cup/tsp straight onto tabs, so cooking
                        # language reads as units. Decide it in CODE, the same way
                        # chart -> stock_card is decided.
                        #
                        # Conservative in both directions: the converter survives if
                        # EITHER the seed or the raw message reads as a conversion,
                        # so "20 usd to eur" is safe twice over. req.message is the
                        # backstop because build_converter_config truncates the seed
                        # to 120 chars and the model may abbreviate it.
                        _seed = str((config or {}).get("seed") or "").strip()
                        if widget_type == "converter" and not (
                                is_conversion_ask(_seed)
                                or is_conversion_ask(req.message)):
                            _ask = _seed or req.message
                            logger.info(f"[WIDGET INJECTOR] converter -> data_card "
                                        f"(not a conversion): {_ask[:80]!r}")
                            widget_type = "data_card"
                            # Re-mint the id: _resolve_agent_widget_id resolved it AS
                            # a converter and may have matched a real calculator
                            # already on the canvas, which the answer card would then
                            # overwrite.
                            widget_id = f"answer-{uuid.uuid4().hex[:8]}"
                            # Falls through to the data_card + search_query branch
                            # below, which already calls build_answer_config.
                            config = {"search_query": _ask}
                            # The fast loop cuts the model off before it speaks, so
                            # _spoken_summary reads from here — without this re-set
                            # the widget is right and the spoken line still describes
                            # a converter.
                            nonlocal_last_committed["v"] = (widget_type, config, widget_id)

                        # Rehydrate data-heavy widgets from the tool result the
                        # model just fetched. It only has to name the subject —
                        # {"symbol": "AMZN"} — instead of hand-typing the whole
                        # snapshot back to us, which was costing ~55s a turn.
                        if widget_type == "chart" and config.get("compare_symbols"):
                            # Multi-ticker comparison: the model names the
                            # tickers, the server fetches every series and
                            # builds the normalized chart. Model-supplied
                            # chart data (if any) is replaced — same policy
                            # as images: it can't have real market data.
                            cmp_cfg = await build_stock_compare_config(
                                config["compare_symbols"], config.get("range", "6mo"))
                            if cmp_cfg:
                                logger.info(f"[WIDGET INJECTOR] Built stock compare "
                                            f"chart: {cmp_cfg['compare_symbols']}")
                                config = cmp_cfg
                        elif widget_type == "stock_card" and not config.get("values"):
                            cached = get_cached_tool_result(f"stock:{config.get('symbol', '')}")
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated stock_card for {config.get('symbol')}")
                                config = {**cached, **{k: v for k, v in config.items() if v}}
                        elif widget_type == "scoreboard" and not config.get("events"):
                            cached = (get_cached_tool_result(f"scores:{config.get('league', '')}")
                                      or get_cached_tool_result(f"scores:{config.get('title', '')}"))
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated scoreboard for {config.get('league')}")
                                config = {**cached, **{k: v for k, v in config.items() if v}}
                        elif widget_type == "weather" and not config.get("current"):
                            cached = get_cached_tool_result(f"weather:{str(config.get('location', '')).lower()}")
                            if cached:
                                logger.info(f"[WIDGET INJECTOR] Rehydrated weather for {config.get('location')}")
                                # Cache wins: it carries the resolved place label + full forecast,
                                # config only carried the bare location string the model typed.
                                config = {**config, **cached}
                        elif widget_type == "app_grid" and not config.get("apps"):
                            # The model never types the app list — the server owns
                            # it (live portal-service inventory ⊕ curation).
                            hub_cfg = await build_app_grid_config()
                            config = {**hub_cfg,
                                      **{k: v for k, v in config.items()
                                         if v and k not in ("apps", "stale")}}
                            logger.info("[WIDGET INJECTOR] Rehydrated app_grid "
                                        f"({len(hub_cfg.get('apps') or [])} apps)")
                        elif (widget_type == "data_card" and not config.get("items")
                              and ("news_topic" in config or "topic" in config)):
                            config = await _resolve_news_topic_config(config)
                        elif (widget_type == "data_card" and not config.get("items")
                              and config.get("stock_news_query")):
                            # Stock/market news: the model names the query; the
                            # server re-pulls Yahoo headlines, reads the top pages
                            # and WRITES the per-story summaries — never a wall of
                            # raw title+link rows (the stock_news tool result has
                            # no snippets to summarize from).
                            sq = str(config.get("stock_news_query", "")).strip()
                            stock_cfg = await build_stock_news_config(sq)
                            logger.info(f"[WIDGET INJECTOR] Synthesised stock-news data_card for {sq!r}")
                            config = {**stock_cfg, **{k: v for k, v in config.items()
                                                      if v and k != "stock_news_query"}}
                        elif (widget_type == "data_card" and not config.get("answer")
                              and not config.get("items")
                              and (config.get("search_query") or config.get("answer_query"))):
                            # A general answer data_card: the model names the query,
                            # the server reads the top pages, WRITES a summarised
                            # Markdown answer and attaches the pages as sources — the
                            # model never hand-builds the wall-of-links card. Reuses
                            # the html_notes_web_search result cache when present.
                            aq = str(config.get("search_query") or config.get("answer_query") or "").strip()
                            cached_hits = get_cached_tool_result(f"search:{aq}")
                            answer_cfg = await build_answer_config(aq, results=cached_hits)
                            logger.info(f"[WIDGET INJECTOR] Synthesised answer data_card for {aq!r}")
                            config = {**answer_cfg, **{k: v for k, v in config.items()
                                                       if v and k not in ("search_query", "answer_query")}}
                        elif widget_type == "profile_card" and not config.get("facts"):
                            # Infobox from a subject name: Wikipedia portrait +
                            # structured facts, server-resolved — the model only
                            # names the subject and can never supply the image.
                            pq = str(config.get("profile_query")
                                     or config.get("query")
                                     or config.get("title", "")).strip()
                            if pq:
                                prof_cfg = await build_profile_config(pq)
                                logger.info(f"[WIDGET INJECTOR] Built profile_card for {pq!r}")
                                config = {**prof_cfg,
                                          **{k: v for k, v in config.items()
                                             if v and k not in ("profile_query", "query", "image")}}
                        elif widget_type == "timeline":
                            if not config.get("events") and (
                                    config.get("timeline_query") or config.get("query")):
                                tq = str(config.get("timeline_query")
                                         or config.get("query", "")).strip()
                                tl_cfg = await build_timeline_config(tq)
                                logger.info(f"[WIDGET INJECTOR] Built timeline for {tq!r} "
                                            f"({len(tl_cfg.get('events', []))} events)")
                                config = {**tl_cfg,
                                          **{k: v for k, v in config.items()
                                             if v and k not in ("timeline_query", "query", "events")}}
                            else:
                                # Hand-built events: same image policy as the
                                # image widget — a model-typed URL is fabricated
                                # until proven otherwise, so strip it; the
                                # renderer degrades to the date+text row.
                                for ev in (config.get("events") or []):
                                    if isinstance(ev, dict):
                                        ev.pop("image", None)
                                        ev.pop("thumbnail", None)
                        elif widget_type == "image":
                            # A picture ask. The prompt advertised
                            # widget_type='image' and "image" is in
                            # _AGENT_RESEARCH_TYPES, so in prism mode every image
                            # ask is DEFERRED to the agent — but the agent's
                            # 21-tool scope has no image-search tool. The model's
                            # only options were to invent a URL or scrape one, so
                            # its cards came out broken, empty, or WRONG.
                            #
                            # build_image_config does this properly (search ->
                            # og:image extraction -> the gemma vision gate that
                            # judges whether the picture actually shows the
                            # subject), binding each caption to the page its
                            # image came from.
                            #
                            # This branch deliberately runs EVEN WHEN the model
                            # supplied a `url` OR a populated `images` array. It
                            # used to skip on `images`, which inverted the guard:
                            # it stood down in exactly the situation it exists
                            # for. Live: "compare birkenstock shoes to other
                            # shoes" shipped a pasta photo captioned "Classic
                            # Birkenstock Arizona two-strap sandal" — both URLs
                            # loaded fine, so liveness checks can't catch this;
                            # the model paired its own captions with unrelated
                            # remembered URLs. Model-supplied pairs are never
                            # trusted: the server re-sources, and a model URL
                            # survives only as a last resort after verification.
                            model_imgs = [i for i in (config.get("images") or [])
                                          if isinstance(i, dict) and i.get("url")]
                            iq = str(config.get("image_query")
                                     or config.get("query")
                                     or config.get("title", "")
                                     or config.get("caption", "")).strip()
                            if not iq and model_imgs:
                                # No query anywhere, but the captions carry the
                                # intent ("Birkenstock Arizona" / "hiking
                                # sandal") — search for those instead.
                                iq = " and ".join(
                                    str(i.get("caption") or "").strip()
                                    for i in model_imgs if i.get("caption"))[:140].strip()
                            img_cfg = (await build_image_config(iq)) if iq else None
                            if img_cfg and img_cfg.get("images"):
                                logger.info(f"[WIDGET INJECTOR] Built image widget for {iq!r} "
                                            f"({len(img_cfg['images'])} images"
                                            f"{'; dropped model-supplied images' if model_imgs else ''})")
                                config = {**img_cfg, **{k: v for k, v in config.items()
                                                        if v and k not in ("image_query", "query", "images")}}
                            else:
                                # Builder found nothing. Keep only model images
                                # that actually resolve, vision-gated when we
                                # have a query to judge them against.
                                candidates = model_imgs or (
                                    [{"url": config["url"], "caption": config.get("caption", "")}]
                                    if config.get("url") else [])
                                kept = [c for c in candidates
                                        if await _image_url_loads(c.get("url", ""))]
                                if kept and iq:
                                    # The gate speaks 'image', the widget speaks
                                    # 'url' — translate both ways. Fails open.
                                    gated = await filter_images_by_relevance(
                                        iq, [],
                                        [{"image": c["url"], "caption": c.get("caption", "")}
                                         for c in kept])
                                    kept = [{"url": g["image"], "caption": g.get("caption", "")}
                                            for g in gated if g.get("image")]
                                if kept:
                                    logger.info(f"[WIDGET INJECTOR] Builder found nothing for "
                                                f"{iq!r}; kept {len(kept)}/{len(candidates)} "
                                                f"verified model images")
                                    config = {**config, "images": kept}
                                else:
                                    # No usable picture. Say so as a data_card
                                    # rather than shipping a broken image frame.
                                    logger.info(f"[WIDGET INJECTOR] No usable image for {iq!r} — "
                                                f"degrading to a data_card")
                                    widget_type = "data_card"
                                    config = {"title": (iq or "Image")[:60].title(), "icon": "image",
                                              "answer": f"I couldn't find a usable picture of **{iq or 'that'}**."}
                        elif (widget_type == "map" and not config.get("markers")
                              and config.get("map_query")):
                            # A map from a query: the server searches, geocodes the
                            # places the LLM pulled out, and bakes the markers — the
                            # model never hand-types coordinates.
                            mq = str(config.get("map_query", "")).strip()
                            map_cfg = await build_map_config(mq)
                            if map_cfg.get("prompt_for_location"):
                                # POI/eat ask with no place and no saved city — render
                                # the "which city?" card, not a blank server-region map.
                                logger.info(f"[WIDGET INJECTOR] Map for {mq!r} needs a location — asking")
                                widget_type = "data_card"
                                config = {k: v for k, v in map_cfg.items()
                                          if k != "prompt_for_location"}
                            else:
                                logger.info(f"[WIDGET INJECTOR] Built map for {mq!r} ({len(map_cfg.get('markers', []))} markers)")
                                config = {**map_cfg, **{k: v for k, v in config.items()
                                                        if v and k != "map_query"}}

                        # QUALITY FLOOR: the rehydration branches above only fire
                        # when the model OMITTED content. When it instead hand-builds
                        # a data_card that would render as a wall of bare links (items
                        # with no summaries) or a sourceless answer, none of them fire
                        # and the bad card slips straight through. Enrich it in place.
                        if widget_type == "data_card":
                            gap = _data_card_quality_gap(config)
                            if gap:
                                logger.info(f"[WIDGET INJECTOR] data_card quality gap ({gap}) — enriching")
                                config = await _ensure_data_card_quality(config, query_hint=req.message)

                        # Bake alternate video ids into youtube players so the
                        # widget can hop to an embeddable video when the first
                        # one blocks embedding ("Video unavailable").
                        if widget_type == "youtube_player":
                            search_q = config.get("query") or config.get("title") or ""
                            primary = config.get("video_id", "")
                            channel = None
                            try:
                                # Freshness parsed from the ORIGINAL user message —
                                # this block runs inside the chat request, so it is
                                # the final guarantee that the agent's pick honors
                                # the requested time window even when the tool-side
                                # signals were all lost.
                                fresh = parse_freshness(req.message)
                                form = parse_video_form(req.message)
                                # Fetched with form="any" so the primary can be
                                # FOUND here even when it is the wrong format —
                                # that is exactly the case this block has to
                                # catch. The swap candidates are filtered after.
                                pool = await search_youtube_videos(
                                    search_q, limit=8, freshness=fresh,
                                    form="any") if search_q else []
                                # Resolve the primary's channel/age/format from the
                                # search hits (the model doesn't supply them) so a
                                # later "this channel sucks" has something to block,
                                # the window check has an age to test and the format
                                # check has a Short flag to read.
                                primary_age = None
                                primary_is_short = False
                                for v in pool:
                                    if v.get("video_id") == primary:
                                        channel = v.get("channel")
                                        primary_age = v.get("age_days")
                                        primary_is_short = bool(v.get("is_short"))
                                        break
                                alts = filter_by_form(pool, form)
                                # The agent picked a Short for an ask that never
                                # said "short" (its tool query drops the word, and
                                # a Short is usually the freshest thing a channel
                                # posted). Treat it like a window violation.
                                # (and the mirror case: a Shorts ask answered with
                                # a 20-minute upload). Only swaps when a hit of the
                                # RIGHT format actually exists — never trade a
                                # watchable pick for nothing.
                                primary_found = any(v.get("video_id") == primary
                                                    for v in pool)
                                want_short = (form == "short")
                                wrong_form = bool(
                                    primary_found and primary_is_short != want_short
                                    and any(bool(v.get("is_short")) == want_short
                                            for v in alts))
                                if wrong_form:
                                    logger.info(
                                        f"[WIDGET INJECTOR] agent pick {primary} is "
                                        f"{'a Short' if primary_is_short else 'a full video'}"
                                        f" but the ask wanted "
                                        f"{'a Short' if want_short else 'a video'}"
                                        f" — swapping")
                                # Re-pick when: the pick is blocked, OR the query is
                                # broad/vague, OR this exact video was already shown
                                # this session, OR the pick violates the requested
                                # time window (an in-window alternative exists in
                                # alts, which was fetched under the same window).
                                # An unknown age is NOT treated as stale — only a
                                # measured violation triggers the swap.
                                seen = _shown_video_ids(req.session_id)
                                is_blocked = primary in _blocked_video_ids or (channel or "").lower() in _blocked_channels
                                vague = is_query_vague(search_q)
                                W = fresh.window_days if fresh else None
                                stale = bool(W and primary_age is not None
                                             and primary_age > W * 1.5 + 0.5
                                             and any(not a.get("stale_fallback") for a in alts))
                                # Never swap DOWNWARD: when the ask has a window and
                                # the current pick is inside it, the vague/seen
                                # variety re-pick is suppressed — a compliant pick
                                # must not be traded for an older one.
                                in_window = bool(W and primary_age is not None
                                                 and primary_age <= W * 1.5 + 0.5)
                                if stale:
                                    logger.info(f"[WIDGET INJECTOR] agent pick {primary} is "
                                                f"{primary_age:.1f}d old, outside the "
                                                f"~{W:g}d ask — swapping to a fresh hit")
                                if is_blocked or stale or wrong_form or not primary or (
                                        (vague or primary in seen) and not in_window):
                                    kept = filter_blocked_videos(alts)
                                    if fresh:
                                        alt_top, alt_cands = pick_best_video(kept, exclude_ids=seen)
                                    else:
                                        alt_top, alt_cands = pick_best_video(
                                            kept, exclude_ids=seen | {primary} if primary else seen)
                                    if alt_top:
                                        primary = alt_top["video_id"]
                                        channel = alt_top.get("channel")
                                        config["video_id"] = primary
                                        config["title"] = config.get("title") or alt_top.get("title")
                                        config["candidates"] = alt_cands
                                if not config.get("candidates") and alts:
                                    config["candidates"] = [v["video_id"] for v in alts
                                                            if v.get("video_id") and v["video_id"] != primary]
                                config.setdefault("query", search_q)
                            except Exception as se:
                                logger.warning(f"candidate enrichment failed: {se}")
                            _remember_current_video(
                                req.session_id, {"video_id": primary, "channel": channel}, search_q)

                        # A music widget is always meant to be listened to —
                        # don't rely on the model remembering to set autoplay.
                        if widget_type == "mini_music_player":
                            config["autoplay"] = True

                        # Same-widget follow-ups STACK (bounded) instead of
                        # hard-replacing — see _stack_data_card_update.
                        _updating_in_place = bool(
                            _widget_on_canvas(req.session_id, widget_id, widget_type))
                        config = _stack_data_card_update(
                            req.session_id, widget_id, widget_type, config,
                            _updating_in_place)
                        _remember_widget_config(req.session_id, widget_id, config)

                        def _add(soup):
                            replaced = False
                            # Media widgets (video, music) are players: a new one
                            # replaces whatever's already playing instead of stacking
                            # up a second player — but request-order-safe, so a slower
                            # older ask can't overwrite a newer one.
                            if _place_media_widget(soup, widget_type, widget_id, config, req_seq):
                                replaced = True

                            # A retried tool call with the same widget_id must not
                            # duplicate the widget — replace it in place instead.
                            if not replaced:
                                existing = soup.find(id=widget_id)
                                if existing is not None:
                                    existing.replace_with(BeautifulSoup(
                                        render_widget(widget_type, widget_id, config), 'html.parser'))
                                    replaced = True

                            if replaced:
                                logger.info("[WIDGET INJECTOR] Replaced existing widget in-place")
                                return

                            snippet = BeautifulSoup(
                                render_widget(widget_type, widget_id, config), 'html.parser')
                            _stamp_media_seq(snippet, widget_type, req_seq)
                            target = soup.select_one('#dashboard-grid')
                            if target:
                                target.append(snippet)
                            else:
                                soup.append(snippet)
                            logger.info(f"[WIDGET INJECTOR] Appended new {widget_type} widget")

                        event = await emit(_add)
                        if event:
                            # The final version now owns this node — stop
                            # tracking it as provisional (and don't re-promote
                            # it at turn end).
                            if provisional_widgets.pop(widget_id, None):
                                for _t, _w in list(provisional_by_topic.items()):
                                    if _w == widget_id:
                                        provisional_by_topic.pop(_t, None)
                            record_turn(req.session_id, req.message, f"agent:{widget_type}",
                                        [(widget_id, widget_type,
                                          config.get("title", "") or req.message,
                                          _widget_detail(config))])
                            yield event

                        logger.info("[FAST LOOP] Terminating early after canvas_add_widget to save latency")
                    elif tool_name == "mcp__lazy-tool-service__create_widget":
                        widget_type = tool_args.get("widgetType", "custom")
                        title = tool_args.get("title", "Widget")
                        html_content = tool_args.get("htmlContent", "")
                        css_content = tool_args.get("cssContent", "")
                        js_content = tool_args.get("jsContent", "")

                        # Guardrails on the one path that used to interpolate
                        # model output RAW into live markup + a live <script>.
                        # Title is plain text — escape it (a title like
                        # '</div><script>…' broke out of the header). htmlContent
                        # goes through the same audit the notes path has always
                        # had (html_notes_create_note → audit_html_fragment);
                        # a fragment that fails the audit is rendered as escaped
                        # text instead of markup. jsContent still executes (it IS
                        # the custom-widget feature) but it stays inside the
                        # audited container and can no longer be smuggled in via
                        # title/htmlContent breakouts.
                        title = html_lib.escape(str(title or "Widget"))
                        if html_content:
                            try:
                                from app.agents.auditor import audit_html_fragment
                                audit_res = audit_html_fragment(html_content)
                                if not audit_res.get("is_valid"):
                                    logger.warning(
                                        f"[WIDGET INJECTOR] create_widget htmlContent failed audit "
                                        f"({audit_res.get('errors')}) — rendering as text")
                                    html_content = (
                                        f'<pre style="white-space:pre-wrap">'
                                        f'{html_lib.escape(str(html_content))}</pre>')
                            except Exception as ae:
                                logger.warning(f"[WIDGET INJECTOR] htmlContent audit unavailable: {ae}")
                        
                        # Generate widget ID
                        widget_id = f"widget-{uuid.uuid4().hex[:8]}"
                        
                        # Scope CSS
                        scoped_css = ""
                        if css_content:
                            rules = []
                            for rule in css_content.split("}"):
                                if "{" in rule:
                                    sel, body = rule.split("{", 1)
                                    sel = sel.strip()
                                    if sel and not sel.startswith("@"):
                                        scoped_sel = ", ".join([f"#{widget_id} {s.strip()}" for s in sel.split(",")])
                                        rules.append(f"{scoped_sel} {{{body}}}")
                                    else:
                                        rules.append(rule + "}")
                            scoped_css = "\n".join(rules)
                            
                        # Wrap content
                        html_snippet = f"""
<div id="{widget_id}" class="glass-card canvas-widget" data-widget-type="{widget_type}">
    <div class="glass-card-title">{title}</div>
    <style>{scoped_css}</style>
    <div class="widget-body">{html_content}</div>
    <script>
    (function() {{
        const container = document.getElementById('{widget_id}');
        {js_content}
    }})();
    </script>
</div>
"""
                        def _create(soup):
                            snippet = BeautifulSoup(html_snippet, 'html.parser')
                            target = soup.select_one('#dashboard-grid')
                            if target:
                                target.append(snippet)
                            else:
                                soup.append(snippet)

                        event = await emit(_create)
                        if event:
                            yield event
                        logger.info(f"[WIDGET INJECTOR] Created and appended new {widget_type} widget")
                        logger.info("[FAST LOOP] Terminating early after create_widget to save latency")
                    elif tool_name == "mcp__lazy-tool-service__update_widget":
                        widget_id = tool_args.get("widgetId")
                        title = tool_args.get("title")
                        html_content = tool_args.get("htmlContent")
                        css_content = tool_args.get("cssContent")
                        js_content = tool_args.get("jsContent")
                        
                        def _update(soup):
                            widget_div = soup.find(id=widget_id)
                            if not widget_div:
                                return False
                            # update_widget only understands create_widget's
                            # hand-built anatomy (.glass-card-title/.widget-body/
                            # <style>/<script>). A factory-rendered widget has
                            # NONE of those, so every selector below missed and
                            # this returned None — commit_canvas then committed
                            # the byte-identical canvas and told the model
                            # {"success": true} for a mutation that never
                            # happened (the exact bug class fixed for
                            # canvas_modify_dom at the comment above _modify).
                            # Reject factory types outright and track whether
                            # anything matched.
                            stamped = (widget_div.get("data-widget-type") or "").strip()
                            from app.widgets.factory import WIDGET_RENDERERS as _WR
                            if stamped in _WR:
                                logger.warning(
                                    f"[WIDGET INJECTOR] update_widget on factory widget "
                                    f"#{widget_id} ({stamped}) — rejected; the model must "
                                    f"use canvas_add_widget with the same id instead")
                                return False
                            touched = False
                            if title is not None:
                                title_el = widget_div.select_one(".glass-card-title")
                                if title_el:
                                    title_el.string = title
                                    touched = True
                            if html_content is not None:
                                body_el = widget_div.select_one(".widget-body")
                                if body_el:
                                    body_el.clear()
                                    body_el.append(BeautifulSoup(html_content, 'html.parser'))
                                    touched = True
                            if css_content is not None:
                                style_el = widget_div.select_one("style")
                                if style_el:
                                    touched = True
                                    rules = []
                                    for rule in css_content.split("}"):
                                        if "{" in rule:
                                            sel, body = rule.split("{", 1)
                                            sel = sel.strip()
                                            if sel and not sel.startswith("@"):
                                                scoped_sel = ", ".join([f"#{widget_id} {s.strip()}" for s in sel.split(",")])
                                                rules.append(f"{scoped_sel} {{{body}}}")
                                            else:
                                                rules.append(rule + "}")
                                    style_el.string = "\n".join(rules)
                            if js_content is not None:
                                script_el = widget_div.select_one("script")
                                if script_el:
                                    touched = True
                                    script_el.string = f"""
                                    (function() {{
                                        const container = document.getElementById('{widget_id}');
                                        {js_content}
                                    }})();
                                    """
                            if not touched:
                                # Nothing matched — abort so commit_canvas
                                # doesn't report success on a no-op.
                                logger.warning(f"[WIDGET INJECTOR] update_widget #{widget_id}: no updatable element matched — aborting")
                                return False
                            # The content changed under a stale data-sig; bump it
                            # so the client reconciler repaints this widget.
                            if widget_div.get("data-sig"):
                                widget_div["data-sig"] = f"mod-{uuid.uuid4().hex[:12]}"

                        event = await emit(_update)
                        if event:
                            yield event
                            logger.info(f"[WIDGET INJECTOR] Updated widget {widget_id} in-place")
                        logger.info("[FAST LOOP] Terminating early after update_widget to save latency")
                except Exception as ex:
                    logger.error(f"Failed to execute canvas mutation: {ex}")

            try:
                yield ('data: ' + json.dumps({
                    "type": "debug", "path": "agent",
                    "note": "no fast-path matched — falling back to the LLM agent",
                    # The classifier's verdict travels WITH the deferral now, so a
                    # misroute is diagnosable from DevTools alone. Before this the
                    # console said only "path: agent" and the one field that names
                    # the cause — what tier 2 thought the ask WAS — lived in a
                    # server log the browser cannot see.
                    "router": router_debug,
                    "followup_target": followup_target,
                    "focus_id": turn_ctx.get("focus_id"),
                    "query": req.message}) + '\n\n')
                yield f'data: {json.dumps({"type": "status", "message": "connecting to agent...", "phase": _PHASE_ROUTING})}\n\n'

                async with httpx.AsyncClient(timeout=600.0) as client:
                    async with client.stream(
                        "POST",
                        f"{target_url}/agent",
                        json=payload,
                        # prism scopes a request by the x-project / x-username
                        # HEADERS, not the body fields — without them every
                        # html-notes turn was attributed to "anonymous" and so never
                        # showed up under admin in prism-client. Body fields are kept
                        # in sync below for services that read them instead.
                        headers={"Accept": "text/event-stream",
                                 "x-project": AGENT_PROJECT,
                                 "x-username": AGENT_USERNAME}
                    ) as resp:
                        if resp.status_code != 200:
                            error_body = ""
                            async for chunk in resp.aiter_text():
                                error_body += chunk
                            yield f'data: {json.dumps({"type": "error", "message": f"Prism error {resp.status_code}: {error_body[:500]}"})}\n\n'
                            return

                        buffer = ""
                        active_tool_name = None
                        active_tool_args = {}
                        # PRE-TOOL PROSE IS DELIBERATION, NOT AN ANSWER. The model
                        # has to think in tokens to decide which tool fits, but
                        # rule 1 forbids preamble and rule 5 says the ONE sentence
                        # comes AFTER the widget is up — so anything arriving
                        # before the first tool call is working-out, and it was
                        # being streamed straight into the chat pane and read
                        # aloud by TTS. Hold it instead, and only release it if
                        # the turn ends having called no tool at all (genuine
                        # small talk). final_text still accumulates every token,
                        # so the DB record and the prose->data_card fallback are
                        # unaffected.
                        pretool_buffer = ""
                        saw_tool_call = False

                        async for chunk in resp.aiter_text():
                            buffer += chunk
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()

                                if not line.startswith("data: "):
                                    continue

                                if canvas_settled:
                                    break

                                try:
                                    event = json.loads(line[6:])
                                except json.JSONDecodeError:
                                    continue

                                event_type = event.get("type", "")
                                logger.info(f"[SSE_PROXY] Received event_type: '{event_type}'")

                                if event_type in ("chunk", "done") and active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget"):
                                    if not executed_active_tool and is_valid_tool_args(active_tool_name, active_tool_args):
                                        failed_tool = active_tool_name
                                        async for evt in execute_mutation(active_tool_name, active_tool_args):
                                            yield evt
                                        executed_active_tool = True
                                        active_tool_name = None
                                        active_tool_args = {}
                                        if mutation_outcome["committed"]:
                                            # Remember WHAT was committed: the fast loop
                                            # cuts the model off before it describes it,
                                            # so this config is all we have to speak from.
                                            last_committed = nonlocal_last_committed["v"]
                                            widgets_committed += 1
                                            if not wants_multiple or widgets_committed >= _MAX_AGENT_WIDGETS:
                                                canvas_settled = True
                                                break
                                        else:
                                            # The mutation was a no-op (selector
                                            # matched nothing / render failed).
                                            # Do NOT count it or settle the turn —
                                            # the prose fallback below still fires
                                            # if nothing ever lands.
                                            logger.warning(f"[AGENT] {failed_tool} produced no canvas change — not counted as committed")
                                            yield f'data: {json.dumps({"type": "status", "message": "that edit did not match anything on the canvas"})}\n\n'

                                if event_type == "chunk":
                                    # Text token from LLM
                                    token = event.get("content", "")
                                    if saw_tool_call:
                                        final_text += token
                                        yield f'data: {json.dumps({"type": "chunk", "content": token})}\n\n'
                                    else:
                                        # Deliberation — hold it (see pretool_buffer).
                                        # Deliberately NOT added to final_text: that
                                        # variable is "what the user was told", and
                                        # everything downstream reads it that way —
                                        # the empty-bubble check below fires on it,
                                        # and the prose->data_card safety net turns
                                        # it into a card. Letting working-out in
                                        # there would suppress the spoken summary
                                        # AND render the working-out as the answer.
                                        pretool_buffer += token

                                elif event_type == "tool_execution":
                                    status = event.get("status", "")
                                    tool_info = event.get("tool", {})
                                    tool_name = tool_info.get("name", "unknown")
                                    args = tool_info.get("args", {})

                                    if active_tool_name != tool_name:
                                        # The turn is acting, so everything said
                                        # before this was working-out. Drop it.
                                        if not saw_tool_call and pretool_buffer.strip():
                                            logger.info(
                                                f"[AGENT] suppressed {len(pretool_buffer)} chars of "
                                                f"pre-tool narration: {pretool_buffer.strip()[:80]!r}")
                                        saw_tool_call = True
                                        pretool_buffer = ""
                                        active_tool_name = tool_name
                                        active_tool_args = {}
                                        executed_active_tool = False
                                        # Args ride along: the name alone says
                                        # "html_notes_web_search", which tells a
                                        # watching user nothing about WHAT is
                                        # being searched — and the browser has
                                        # always read this field.
                                        tool_phase = _phase_for_tool(tool_name)
                                        yield f'data: {json.dumps({"type": "tool_call", "tool": tool_name, "args": _summarize_tool_args(args), "phase": tool_phase})}\n\n'
                                        yield f'data: {json.dumps({"type": "status", "message": f"preparing {tool_name}...", "phase": tool_phase})}\n\n'
                                    
                                    active_tool_args = args

                                    # DESTRUCTIVE ACTION: the tool parked it and
                                    # returned a pending_id; put the confirm card
                                    # on the canvas so the user's click is the
                                    # only thing that can fire it. Rendered here
                                    # (not by canvas_add_widget) so the model
                                    # cannot skip it or invent its own config.
                                    if (active_tool_name == "mcp__lazy-tool-service__html_notes_app_action"
                                            and not executed_active_tool
                                            and status in ("done", "success")):
                                        _act_app = str((args or {}).get("app_id") or "").strip()
                                        _act_name = str((args or {}).get("action") or "").strip()
                                        _act_spec = get_action_spec(_act_app, _act_name)
                                        if _act_spec and _act_spec.get("destructive"):
                                            executed_active_tool = True
                                            _act_params = (args or {}).get("params") or {}
                                            if isinstance(_act_params, str):
                                                try:
                                                    _act_params = json.loads(_act_params)
                                                except Exception:
                                                    _act_params = {}
                                            _pid = park_pending_action(_act_app, _act_name, _act_params)
                                            _cfg = build_action_confirm_config(
                                                _act_app, _act_name, _act_params, _pid)

                                            def _place_confirm(soup, _cfg=_cfg):
                                                grid = soup.select_one('#dashboard-grid')
                                                if grid is None:
                                                    soup.append(BeautifulSoup(
                                                        '<div id="dashboard-grid" class="dashboard-grid"></div>',
                                                        'html.parser'))
                                                    grid = soup.select_one('#dashboard-grid')
                                                grid.append(BeautifulSoup(render_widget(
                                                    "action_confirm",
                                                    f"confirm-{uuid.uuid4().hex[:8]}", _cfg),
                                                    'html.parser'))

                                            _evt = await commit_canvas(req.session_id, _place_confirm)
                                            if _evt:
                                                logger.info(f"[ACTIONS] confirm card for {_act_app}.{_act_name}")
                                                yield _evt

                                    # BROWSER OPEN: html_notes_open_app names an
                                    # approved catalog app — resolve it server-side
                                    # and hand the client its URL as a dedicated SSE
                                    # frame (window.open + clickable-toast fallback,
                                    # since an SSE callback has no user gesture).
                                    # Emitted once per call; never a raw model URL.
                                    if (active_tool_name == "mcp__lazy-tool-service__html_notes_open_app"
                                            and not executed_active_tool
                                            and status in ("calling", "done", "success")):
                                        open_q = str((args or {}).get("app_id")
                                                     or (args or {}).get("query") or "").strip()
                                        if open_q:
                                            executed_active_tool = True
                                            hub_data = await get_portal_apps()
                                            open_app, _ = resolve_portal_app(open_q, hub_data["apps"])
                                            if (open_app and open_app.get("launch_url")
                                                    and open_app["id"] not in emitted_open_apps):
                                                emitted_open_apps.add(open_app["id"])
                                                logger.info(f"[APP HUB] open_url → {open_app['id']}")
                                                yield f'data: {json.dumps({"type": "open_url", "url": open_app["launch_url"], "name": open_app["name"]})}\n\n'

                                    # FAST PATH: Execute immediately when arguments are available!
                                    if active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget", "mcp__lazy-tool-service__create_widget", "mcp__lazy-tool-service__update_widget"):
                                        if not executed_active_tool and is_valid_tool_args(active_tool_name, active_tool_args) and status in ("calling", "done", "success"):
                                            failed_tool = active_tool_name
                                            async for evt in execute_mutation(active_tool_name, active_tool_args):
                                                yield evt
                                            executed_active_tool = True
                                            active_tool_name = None
                                            active_tool_args = {}
                                            if mutation_outcome["committed"]:
                                                # Same capture as the other commit site:
                                                # this is the only record of what we
                                                # rendered once the args are cleared.
                                                last_committed = nonlocal_last_committed["v"]
                                                widgets_committed += 1
                                                if not wants_multiple or widgets_committed >= _MAX_AGENT_WIDGETS:
                                                    canvas_settled = True
                                                    break
                                            else:
                                                logger.warning(f"[AGENT] {failed_tool} produced no canvas change — not counted as committed")
                                                yield f'data: {json.dumps({"type": "status", "message": "that edit did not match anything on the canvas"})}\n\n'
                                        elif status in ("calling", "done", "success", "error"):
                                            active_tool_name = None
                                            active_tool_args = {}
                                    elif status == "error":
                                        error_msg = event.get("result", "Unknown tool error")
                                        yield f'data: {json.dumps({"type": "status", "message": f"tool error: {tool_name}: {str(error_msg)[:200]}"})}\n\n'
                                    elif status in ("calling", "done", "success"):
                                        # Early preview: a whitelisted data tool just
                                        # finished — put its articles on the canvas as
                                        # a provisional widget before the model has
                                        # even started composing. The runaway/budget
                                        # accounting below still runs for this call.
                                        if (status in ("done", "success")
                                                and tool_name in _PROVISIONAL_TOOLS):
                                            try:
                                                async for evt in _commit_provisional_from_tool(
                                                        tool_name, args, event):
                                                    yield evt
                                            except Exception as pe:
                                                logger.warning(f"[PROVISIONAL] preview commit failed: {pe}")
                                        # A tool we do not handle. Prism forces its
                                        # core/system tools (create_artifact,
                                        # execute_python, search_web…) into the set
                                        # regardless of the enabledTools allowlist we
                                        # send: coreToolsLocked defaults true and a
                                        # CUSTOM agent's persona can't override it. So
                                        # the model can and does pick create_artifact
                                        # over canvas_add_widget on a research ask.
                                        #
                                        # This used to be a TOTAL silent no-op: the
                                        # user saw a tool spinner, no component event
                                        # ever fired, and active_tool_name was never
                                        # reset — which also wedged the deferred-flush
                                        # check for the rest of the turn. Log it, count
                                        # it, and reset so the next tool is clean.
                                        unhandled_tools.append(tool_name)
                                        # Two very different cases, worth telling
                                        # apart in the log: OUR research tools not
                                        # mutating the canvas is EXPECTED (they
                                        # gather data, then the model is supposed to
                                        # call canvas_add_widget), whereas a prism
                                        # core tool means the allowlist was bypassed.
                                        is_ours = tool_name.startswith("mcp__lazy-tool-service__")
                                        if not is_ours:
                                            logger.warning(
                                                f"[AGENT] prism core tool {tool_name!r} — outside our "
                                                f"allowlist and cannot touch the canvas "
                                                f"(coreToolsLocked is unreachable for CUSTOM agents)")
                                        elif unhandled_tools.count(tool_name) in (1, 5, 10):
                                            logger.info(
                                                f"[AGENT] research tool {tool_name!r} "
                                                f"(call #{unhandled_tools.count(tool_name)}) — "
                                                f"no canvas mutation yet")
                                        active_tool_name = None
                                        active_tool_args = {}

                                        # Runaway guard. We cannot stop prism from
                                        # running a tool — we only observe — so the
                                        # only lever is to stop consuming the stream,
                                        # which drops through to the fallback card
                                        # with whatever the turn produced. Better a
                                        # card in 60s than a spinner for 5 minutes.
                                        research_calls += 1
                                        # The research budget is the one denominator
                                        # this proxy always knows. Prism's own
                                        # iteration_progress is better (it counts the
                                        # agentic loop, not just research) and wins
                                        # below when it arrives — but it does not
                                        # arrive on every turn, and a bar with no
                                        # denominator is the fake creep we are trying
                                        # to stop showing.
                                        yield f'data: {json.dumps({"type": "progress", "step": research_calls, "of": _MAX_RESEARCH_CALLS, "source": "research"})}\n\n'
                                        key = _tool_repeat_key(tool_name, args)
                                        tool_repeats[key] = tool_repeats.get(key, 0) + 1
                                        if tool_repeats[key] >= _MAX_IDENTICAL_TOOL_CALLS:
                                            logger.error(
                                                f"[AGENT] RUNAWAY: {tool_name!r} called "
                                                f"{tool_repeats[key]}× with identical args — "
                                                f"cutting the turn short. A tool is almost "
                                                f"certainly failing while telling the model "
                                                f"to retry; check /health/app search status.")
                                            yield f'data: {json.dumps({"type": "status", "message": "search is repeating itself — building from what I have", "phase": _PHASE_COMPOSING})}\n\n'
                                            break
                                        if research_calls >= _MAX_RESEARCH_CALLS:
                                            logger.warning(
                                                f"[AGENT] research budget spent "
                                                f"({research_calls} calls) — cutting the turn "
                                                f"short and rendering what we have")
                                            yield f'data: {json.dumps({"type": "status", "message": "enough research — building the card", "phase": _PHASE_COMPOSING})}\n\n'
                                            break

                                elif event_type == "status":
                                    # Prism's own telemetry. There was no branch
                                    # here at all, so the ONE event carrying a real
                                    # completion fraction was dropped on the floor
                                    # and the browser drew a fake asymptotic creep
                                    # instead. Forward that fraction and ignore the
                                    # rest of the vocabulary (generation_progress,
                                    # compaction, tool_set_changed, …) — this is a
                                    # curated progress channel, not a firehose.
                                    if event.get("message") == "iteration_progress":
                                        step = event.get("iteration")
                                        of = event.get("maxIterations")
                                        if isinstance(step, int) and isinstance(of, int) and of > 0:
                                            yield f'data: {json.dumps({"type": "progress", "step": step, "of": of, "source": "iteration"})}\n\n'

                                elif event_type == "thinking":
                                    # Deliberately NOT tagged with a phase: thinking
                                    # happens *within* whatever phase the turn is in,
                                    # and claiming a phase here would bounce the card
                                    # backwards between "reading" and "researching".
                                    yield f'data: {json.dumps({"type": "status", "message": "reasoning..."})}\n\n'

                                elif event_type == "done":
                                    # Prism finished the full agentic loop
                                    pass

                                elif event_type == "error":
                                    yield f'data: {json.dumps({"type": "error", "message": event.get("message", "Agent error")})}\n\n'

                            # The `break` above only escapes the inner line loop —
                            # without this the outer chunk loop keeps pulling the
                            # agent's stream and the turn runs to completion anyway.
                            if canvas_settled:
                                break

            except Exception as e:
                logger.error(f"Prism SSE proxy error: {e}")
                yield f'data: {json.dumps({"type": "error", "message": f"Connection error: {str(e)}"})}\n\n'

            # The turn called NO tool at all — so what we held back was never
            # deliberation, it was the whole reply (small talk, a clarification,
            # a refusal). Release it, or the user gets silence.
            if not saw_tool_call and pretool_buffer.strip():
                final_text += pretool_buffer
                yield f'data: {json.dumps({"type": "chunk", "content": pretool_buffer})}\n\n'
                pretool_buffer = ""

            if canvas_settled:
                logger.info("[FAST LOOP] Closed agent stream after canvas commit")
                # We cut the model off before it wrote its closing line, so the chat
                # bubble would otherwise be empty. The widget IS the answer here.
                if not final_text.strip():
                    # Speak what the widget SHOWS, not that a widget exists. The
                    # canned "Added it to your canvas." was read aloud and told a
                    # user who wasn't looking at the screen nothing at all.
                    spoken, spoken_id = "", ""
                    if last_committed:
                        try:
                            spoken = _spoken_summary(last_committed[0], last_committed[1],
                                                     req.message)
                            spoken_id = last_committed[2] if len(last_committed) > 2 else ""
                        except Exception as e:
                            logger.warning(f"[TTS] spoken summary failed: {e}")
                    final_text = spoken or "Added it to your canvas."
                    # NB: not named `payload` — that name is already read earlier
                    # in this function, and assigning it here would make Python
                    # treat it as local for the WHOLE function, so the earlier read
                    # raised "cannot access local variable 'payload'". Caught by
                    # tests/test_sse_duplication.py.
                    spoken_payload = {"type": "chunk", "content": final_text}
                    if spoken_id:
                        spoken_payload["widget_id"] = spoken_id
                    yield f'data: {json.dumps(spoken_payload)}\n\n'

            # PROMOTION: the tool's preview made it to the canvas but the agent
            # never committed a matching widget (answered in prose, picked a
            # different widget type, or the turn was cut short). Re-commit the
            # same content WITHOUT the provisional flag so the "composing…"
            # badge doesn't spin forever — and when the turn produced prose but
            # no widget at all, fold that prose in as the card's answer (which
            # also suppresses the separate text-answer fallback card below).
            for _pwid, _pinfo in list(provisional_widgets.items()):
                try:
                    final_cfg = dict(_pinfo["config"])
                    final_cfg.pop("provisional", None)
                    if widgets_committed == 0 and final_text.strip():
                        final_cfg["answer"] = final_text.strip()

                    def _promote(soup, _wid=_pwid, _cfg=final_cfg):
                        html = generate_widget_html("data_card", _wid, _cfg)
                        existing = soup.find(id=_wid)
                        if existing is not None:
                            existing.replace_with(BeautifulSoup(html, "html.parser"))
                        else:
                            grid = soup.select_one("#dashboard-grid") or soup
                            grid.append(BeautifulSoup(html, "html.parser"))

                    evt = await commit_canvas(req.session_id, _promote)
                    if evt:
                        widgets_committed += 1
                        provisional_widgets.pop(_pwid, None)
                        _remember_widget_config(req.session_id, _pwid, final_cfg)
                        record_turn(req.session_id, req.message,
                                    "agent:provisional-promoted",
                                    [(_pwid, "data_card",
                                      final_cfg.get("title", "") or req.message,
                                      _widget_detail(final_cfg))])
                        logger.info(f"[PROVISIONAL] promoted #{_pwid} to final "
                                    f"(agent committed no matching widget)")
                        yield evt
                except Exception as pe:
                    logger.error(f"[PROVISIONAL] promotion failed for #{_pwid}: {pe}")

            # SAFETY NET: the turn answered in prose but wrote nothing to the canvas.
            # Before this, that turn was invisible — the answer was spoken by TTS and
            # streamed into the chat, while the canvas stayed empty and the user was
            # left thinking the widget "failed to appear". It happens whenever the
            # model picks one of prism's forced core tools (create_artifact,
            # execute_python) over canvas_add_widget, which we cannot prevent from
            # this side: coreToolsLocked defaults true and a CUSTOM agent's persona
            # can't override it.
            #
            # Render the answer we DO have as a data_card. A text card is a far better
            # outcome than a blank canvas, and it keeps the invariant the whole UI is
            # built on: every turn that produces an answer puts it on the canvas.
            #
            # LAST RESORT: the turn called a tool, said nothing after it, and
            # committed nothing — so final_text is empty and the only text we
            # hold is the pre-tool working-out we suppressed. A blank canvas is
            # worse than a card built from that, and _text_answer_card_config
            # runs it through _strip_agent_narration first.
            _fallback_text = final_text.strip() or pretool_buffer.strip()
            if not canvas_settled and widgets_committed == 0 and _fallback_text:
                try:
                    if not final_text.strip():
                        logger.info("[AGENT] no post-tool prose — falling back to the "
                                    "suppressed pre-tool text for the answer card")
                    cfg = _text_answer_card_config(req.message, _fallback_text)
                    rid = (_resolve_agent_widget_id(req.session_id, "data_card", "",
                                                    req.message, req.focus_widget_id or "")
                           or f"answer-{uuid.uuid4().hex[:8]}")

                    def _fallback_mutate(soup, _rid=rid, _cfg=cfg):
                        html = generate_widget_html("data_card", _rid, _cfg)
                        existing = soup.find(id=_rid)
                        if existing:
                            existing.replace_with(BeautifulSoup(html, "html.parser"))
                        else:
                            grid = soup.find(id="dashboard-grid") or soup
                            grid.append(BeautifulSoup(html, "html.parser"))

                    evt = await commit_canvas(req.session_id, _fallback_mutate)
                    if evt:
                        logger.warning(
                            f"[AGENT] turn committed no widget "
                            f"(unhandled tools: {unhandled_tools or 'none'}) — "
                            f"rendered the text answer as data_card #{rid}")
                        widgets_committed += 1
                        yield evt
                except Exception as e:
                    logger.error(f"[AGENT] text-answer fallback failed: {e}")

            # Save assistant response to DB. Persist the LIVE canvas, not this
            # turn's mirror — a sibling turn may have committed after our last
            # mutation, and writing a stale snapshot here would resurrect the
            # pre-sibling canvas on the next page reload.
            asst_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
            saved_content = final_text
            live_canvas = get_session_canvas(req.session_id) or all_rendered_html
            if live_canvas:
                saved_content += f"\n\n<!--CANVAS_HTML_START-->\n{live_canvas}\n<!--CANVAS_HTML_END-->"

            if not saved_content.strip():
                saved_content = "[tool-only turn]"

            database.save_chat_message(
                message_id=asst_msg_id,
                session_id=req.session_id,
                role="assistant",
                content=saved_content
            )

            _clear_turn_freshness()
            yield 'data: {"type": "done"}\n\n'

        # Stash the ORIGINAL message's time constraint for /internal/execute —
        # the agent's rewritten tool query regularly drops "new"/"this week".
        # Overwritten (or cleared) by every tier-3 turn and TTL-bounded, so an
        # aborted stream can't leak a stale bias past 180s.
        _stash_turn_freshness(req.message)
        return StreamingResponse(
            _run_turn(req.session_id, req.current_canvas or "", proxy_prism_sse, req.canvas_version),
            media_type="text/event-stream",
        )

    except Exception as e:
        logger.error(f"Error processing session message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/session/transcribe")
async def transcribe_audio(req: TranscribeRequest):
    """
    Proxies base64 audio transcription to Prism service STT endpoint.
    """
    try:
        url = f"{PRISM_URL}/audio-to-text"
        payload = {
            "provider": "openai",
            "audio": req.audio,
            "skipConversation": True,
            "project": AGENT_PROJECT,
            "username": AGENT_USERNAME
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            # Same header contract as the /agent call: prism attributes by the
            # x-project / x-username HEADERS, so without them voice transcription
            # was also being filed under the unattributable "default" project.
            res = await client.post(url, json=payload,
                                    headers={"x-project": AGENT_PROJECT,
                                             "x-username": AGENT_USERNAME})
            if res.status_code != 200:
                logger.error(f"Prism STT failed with code {res.status_code}: {res.text}")
                raise HTTPException(status_code=500, detail=f"Prism transcription failed: {res.text}")
            return res.json()
    except Exception as e:
        logger.error(f"Failed proxying to STT service: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription service unavailable: {str(e)}")


@router.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    try:
        history = database.get_session_messages(session_id)
        # Persisted assistant messages embed the canvas snapshot; split it into
        # canvas_html so the client shows only text in the chat panel.
        messages = []
        for h in history:
            msg = dict(h)
            content = msg.get("content") or ""
            m = CANVAS_BLOCK_RE.search(content)
            if m:
                msg["canvas_html"] = m.group(1).strip()
                msg["content"] = CANVAS_BLOCK_RE.sub("", content).strip()
            messages.append(msg)
        # Seed the client's canvasVersion so its next request's snapshot isn't
        # judged stale against commits it has in fact seen (via this restore).
        return {"messages": messages,
                "canvas_version": _session_canvas_version.get(session_id, 0)}
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


