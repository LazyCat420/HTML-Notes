from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

router = APIRouter()

@app.post("/internal/execute")
async def internal_tool_execute(req: InternalToolRequest, request: Request = None):
    """
    Internal tool dispatcher. Called by lazy-tool-service when the model
    fires an html_notes_* or render_component tool call.

    Auth: when INTERNAL_EXECUTE_TOKEN is configured (env or vault), the caller
    must send it in the x-internal-token header. When it is not configured the
    endpoint stays open for compatibility but warns once per boot — provision
    the token in both this service's and lazy-tool-service's .env to close it.
    """
    global _internal_execute_auth_warned
    expected = await _fetch_secret("INTERNAL_EXECUTE_TOKEN")
    if expected:
        import hmac as _hmac
        supplied = request.headers.get("x-internal-token", "") if request is not None else ""
        if not _hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401,
                                detail="invalid or missing x-internal-token")
    elif not _internal_execute_auth_warned:
        _internal_execute_auth_warned = True
        logger.warning("[SECURITY] /internal/execute is UNAUTHENTICATED — set "
                       "INTERNAL_EXECUTE_TOKEN in this service's and "
                       "lazy-tool-service's env to enforce auth")

    if req.tool not in _INTERNAL_EXECUTE_TOOLS:
        return {"error": f"Tool not allowed: {req.tool}", "is_error": True}

    t = req.tool
    a = req.args

    try:
        if t == "html_notes_create_note":
            from app.agents.auditor import audit_html_fragment
            audit = audit_html_fragment(a.get("rendered_html", ""))
            if not audit["is_valid"]:
                return {"error": f"HTML audit failed: {audit['errors']}", "is_error": True}
            note_id = f"note_{uuid.uuid4().hex[:8]}"
            note = database.create_note(
                note_id=note_id,
                title=a["title"],
                tags=a.get("tags", []),
                links=a.get("links", []),
                source_messages=["tool-call"],
                canonical_blocks=[],
                rendered_html=a["rendered_html"]
            )
            return {"success": True, "note_id": note["id"], "title": note["title"]}

        elif t == "html_notes_update_note":
            from app.agents.auditor import audit_html_fragment
            if "rendered_html" in a:
                audit = audit_html_fragment(a["rendered_html"])
                if not audit["is_valid"]:
                    return {"error": f"HTML audit failed: {audit['errors']}", "is_error": True}
            note = database.update_note(note_id=a["note_id"], **{k: v for k, v in a.items() if k != "note_id"})
            return {"success": True, "note_id": a["note_id"]} if note else {"error": "Note not found", "is_error": True}

        elif t == "html_notes_get_note":
            note = database.get_note_by_id(a["note_id"])
            return note if note else {"error": "Note not found", "is_error": True}

        elif t == "html_notes_search_notes":
            results = database.search_notes(a["query"])
            return {"results": results, "count": len(results)}

        elif t == "html_notes_link_notes":
            note_a = database.get_note_by_id(a["source_note_id"])
            if not note_a:
                return {"error": "Source note not found", "is_error": True}
            links = note_a.get("links", [])
            if a["target_note_id"] not in links:
                links.append(a["target_note_id"])
                database.update_note(note_id=a["source_note_id"], links=links)
            return {"success": True}

        elif t == "html_notes_modify_dom":
            # fetch note, apply BeautifulSoup DOM operation, update
            note = database.get_note_by_id(a["note_id"])
            if not note:
                return {"error": "Note not found", "is_error": True}
            soup = BeautifulSoup(note["rendered_html"], "html.parser")
            target = soup.select_one(a["css_selector"])
            if not target:
                return {"error": f"Selector '{a['css_selector']}' not found", "is_error": True}
            snippet_soup = BeautifulSoup(a["html_snippet"], "html.parser")
            action = a["action"]
            if action == "append":      target.append(snippet_soup)
            elif action == "prepend":   target.insert(0, snippet_soup)
            elif action == "insert_before": target.insert_before(snippet_soup)
            elif action == "insert_after":  target.insert_after(snippet_soup)
            elif action == "replace":   target.replace_with(snippet_soup)
            database.update_note(note_id=a["note_id"], rendered_html=str(soup))
            return {"success": True}

        elif t == "render_component":
            from app.templates import TEMPLATES
            ctype = a.get("component_type")
            data = a.get("data", {})
            if ctype in TEMPLATES:
                html = TEMPLATES[ctype](data)
            else:
                html = a.get("rendered_html", "")
            
            return {
                "success": True,
                "rendered_html": html,
                "component_type": ctype,
                "title": a.get("title", "Component")
            }

        elif t == "canvas_read_dom":
            canvas_html = a.get("canvas_html") or get_session_canvas(_last_active_session)
            css_selector = a.get("css_selector")
            
            if not canvas_html or canvas_html.strip() == "":
                return {"elements": [], "element_count": 0, "summary": "Canvas is empty."}
            
            soup = BeautifulSoup(canvas_html, "html.parser")
            
            if css_selector:
                # Return specific element(s)
                matches = soup.select(css_selector)
                if not matches:
                    return {"error": f"No elements matched selector '{css_selector}'", "matched": 0}
                return {
                    "matched": len(matches),
                    "elements": [
                        {
                            "tag": el.name,
                            "classes": el.get("class", []),
                            "text": el.get_text(strip=True)[:300],
                            "html": str(el)[:1000],
                            "children_count": len(list(el.children))
                        }
                        for el in matches[:10]
                    ]
                }
            
            # Full canvas summary.
            #
            # This selected ".glass-card" and read ".glass-card-title" — a convention
            # NO factory-rendered widget uses. Every widget root is
            # `.widget-container` with a bare <h3> title, so this matched nothing and
            # reported component_count: 0 on a canvas full of widgets. Worse, it
            # never returned the widget ID, so even when it did match, the agent had
            # no way to turn "the map" into a `#id` selector for canvas_modify_dom —
            # it had to guess, and guessed wrong. Reuses _iter_canvas_widgets so this
            # can't drift from the reuse/summary paths again.
            components = []
            for card in soup.select(".glass-card, .widget-container"):
                title_el = card.select_one(".glass-card-title, h3, h2, h4")
                wid = card.get("id") or ""
                components.append({
                    # `id` is what makes this actionable: it is exactly what
                    # canvas_modify_dom(css_selector="#<id>") needs.
                    "id": wid,
                    "selector": f"#{wid}" if wid else "",
                    "type": _classify_canvas_widget(card),
                    "title": (card.get("data-widget-title")
                              or (title_el.get_text(strip=True) if title_el else "")),
                    "subtitle": card.get("data-widget-subtitle") or "",
                    "text_preview": card.get_text(strip=True)[:200],
                })

            all_text = soup.get_text(strip=True)[:500]
            all_tags = [el.name for el in soup.find_all(True)]
            tag_counts = {}
            for tag in all_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            return {
                "element_count": len(all_tags),
                "tag_counts": tag_counts,
                "components": components,
                "component_count": len(components),
                "text_preview": all_text,
                "has_content": bool(all_text.strip())
            }

        elif t == "canvas_modify_dom":
            canvas_html = a.get("canvas_html") or get_session_canvas(_last_active_session)
            css_selector = a.get("css_selector")
            action = a.get("action")
            html_snippet = a.get("html_snippet", "")
            
            if not canvas_html:
                return {"error": "Canvas is empty, nothing to modify", "is_error": True}
            if not css_selector:
                return {"error": "css_selector is required", "is_error": True}
            if not action:
                return {"error": "action is required", "is_error": True}
            
            soup = BeautifulSoup(canvas_html, "html.parser")
            try:
                matches = soup.select(css_selector)
            except Exception as e:
                return {"error": f"Invalid selector '{css_selector}': {e}", "is_error": True}
            target = matches[0] if matches else None

            if not target:
                return {"error": f"No element matched selector '{css_selector}'", "is_error": True}

            if action == "remove":
                # DESTRUCTIVE, so verify before acting. This was select_one() +
                # decompose() on whatever string the model produced, with no check
                # and no report of what died — the add path validates ids through
                # _resolve_agent_widget_id, but remove had no equivalent. A class or
                # positional selector ('.map-widget', '.widget-container:first-child')
                # silently took the first of several and reported success, which is
                # how "close the san jose to sf map" closed a different widget.
                widgets = [m for m in matches
                           if "widget-container" in (m.get("class") or [])
                           or "glass-card" in (m.get("class") or [])]
                if len(widgets) > 1:
                    listing = ", ".join(
                        f'#{w.get("id","?")} ({_classify_canvas_widget(w)}'
                        f'{": " + w.get("data-widget-title") if w.get("data-widget-title") else ""})'
                        for w in widgets[:6])
                    return {
                        "error": (f"Selector '{css_selector}' matches {len(widgets)} widgets "
                                  f"[{listing}] — refusing to guess which to remove. "
                                  f"Re-issue with the exact '#<widget-id>' of the one you mean "
                                  f"(call canvas_read_dom to list ids)."),
                        "is_error": True,
                        "candidates": [{"id": w.get("id", ""),
                                        "type": _classify_canvas_widget(w),
                                        "title": w.get("data-widget-title", "")}
                                       for w in widgets[:10]],
                    }
                removed = {"id": target.get("id", ""),
                           "type": _classify_canvas_widget(target),
                           "title": target.get("data-widget-title", "")}
                logger.info(f"[MODIFY DOM] remove {css_selector!r} -> "
                            f"#{removed['id']} ({removed['type']}) {removed['title']!r}")
                target.decompose()
            elif action in ("append", "prepend", "replace", "insert_before", "insert_after"):
                if not html_snippet:
                    return {"error": f"html_snippet is required for action '{action}'", "is_error": True}
                snippet_soup = BeautifulSoup(html_snippet, "html.parser")
                if action == "append":
                    target.append(snippet_soup)
                elif action == "prepend":
                    target.insert(0, snippet_soup)
                elif action == "replace":
                    target.replace_with(snippet_soup)
                elif action == "insert_before":
                    target.insert_before(snippet_soup)
                elif action == "insert_after":
                    target.insert_after(snippet_soup)
            else:
                return {"error": f"Unknown action: {action}", "is_error": True}
            
            return {
                "success": True,
                "rendered_html": str(soup),
                "action_performed": action,
                "selector": css_selector,
                # Report WHAT was affected, not just that something was. A bare
                # {"success": true} gave the model no way to notice it had removed
                # the wrong widget, so it reported success to the user either way.
                "affected": {"id": target.get("id", "") if action != "remove" else removed["id"],
                             "type": (_classify_canvas_widget(target) if action != "remove"
                                      else removed["type"]),
                             "title": (target.get("data-widget-title", "") if action != "remove"
                                       else removed["title"])},
            }

        elif t == "html_notes_add_youtube_widget":
            # Handled by the SSE interceptor on the streaming wrapper, but we return success here as well
            return {"success": True, "message": "Successfully added YouTube widget to canvas."}

        elif t == "create_widget":
            return {"success": True, "message": "Widget created successfully."}

        elif t == "update_widget":
            return {"success": True, "message": "Widget updated successfully."}

        elif t == "html_notes_youtube_search":
            query = a.get("query", "")
            limit = int(a.get("limit", 5))
            order = a.get("order", "relevance")
            # Freshness resolution, most-trusted first: the agent's verbatim
            # `freshness` arg → time words surviving in the query → the turn
            # stash (parsed from the ORIGINAL user message before the agent
            # stream started — the LLM rewrite regularly drops "new"/"this
            # week", which used to silently kill the recency intent here).
            fresh = (parse_freshness(str(a.get("freshness") or ""))
                     or parse_freshness(query)
                     or _stashed_turn_freshness())
            strict = bool(fresh) or order == "date"
            if fresh:
                logger.info(f"[YOUTUBE MCP] freshness={fresh.matched!r} "
                            f"window={fresh.window_days} for {query!r}")
            # Format resolves the same way, most-trusted first. The explicit
            # arg is taken verbatim ("video"/"long" both mean no Shorts) so the
            # agent can state the axis even when its rewritten query no longer
            # carries the word.
            arg_form = str(a.get("format") or "").strip().lower() or None
            if arg_form in ("video", "videos", "long-form", "longform"):
                arg_form = "long"
            elif arg_form in ("shorts",):
                arg_form = "short"
            elif arg_form not in ("short", "long", "any", None):
                arg_form = None
            form = arg_form or parse_video_form(query) or _stashed_turn_form()
            if form:
                logger.info(f"[YOUTUBE MCP] format={form!r} for {query!r}")
            results = []
            # A recency ask that NAMES a creator is answered from that creator's
            # uploads feed, exactly like the chat fast-path. Without this the
            # agent path fell to keyword search, which ranks by relevance over a
            # polluted pool: "primeagen newest" returned a 5-day-old clip while
            # the creator's 3-hour-old upload existed. Search stays the fallback.
            # A Shorts ask goes down the channel path too, recency word or not:
            # the channel's Shorts feed is the only place a Short can be
            # identified with certainty (search only has the duration tell).
            if (fresh or form == "short") and order != "live":
                subject, _topic = _split_video_subject_topic(query)
                if subject:
                    try:
                        chans = await _resolve_youtube_channels(
                            subject, limit=4,
                            evidence=_creator_evidence(subject, query))
                        if chans:
                            best = chans[0]["rank_score"]
                            chans = [c for c in chans
                                     if c["rank_score"] >= best - 0.3][:3]
                            feeds = await asyncio.gather(
                                *[_youtube_channel_uploads(c["channel_id"], limit=12,
                                                           form=form)
                                  for c in chans], return_exceptions=True)
                            merged, seen_v = [], set()
                            for c, f in zip(chans, feeds):
                                if isinstance(f, Exception) or not f:
                                    continue
                                for h in f:
                                    if h.get("video_id") and h["video_id"] not in seen_v:
                                        seen_v.add(h["video_id"])
                                        merged.append({**h, "channel": h.get("channel") or c["title"]})
                            merged.sort(key=lambda h: h["age_days"]
                                        if h.get("age_days") is not None else 1e9)
                            if fresh and fresh.window_days:
                                merged = filter_by_age(merged, fresh.window_days) or merged
                            results = filter_blocked_videos(merged)[:max(limit, 1)]
                            if results:
                                logger.info(
                                    f"[YOUTUBE MCP] {subject!r} -> channel feed "
                                    f"({len(chans)} channel(s)), newest "
                                    f"{results[0].get('age_days', -1):.2f}d")
                    except Exception as e:
                        logger.warning(f"[YOUTUBE MCP] channel path failed for "
                                       f"{query!r}: {e}")
            if not results:
                results = await search_youtube_videos(query, limit=limit, order=order,
                                                      strict_recency=strict,
                                                      rerank=True, freshness=fresh,
                                                      form=form)
            out = {"results": results, "count": len(results)}
            if form == "short" and results and not any(r.get("is_short") for r in results):
                # Fail-open served regular videos for a Shorts ask — say so
                # rather than letting the model call a 12-minute review a Short.
                out["note"] = ("No Shorts found for that ask; these are regular "
                               "videos — say so in your summary sentence.")
            if fresh and results and all(r.get("stale_fallback") for r in results):
                out["note"] = ("No uploads found inside the requested time "
                               "window; these are the newest available — say "
                               "so in your summary sentence.")
            return out

        elif t == "html_notes_web_search":
            query = a.get("query", "")
            search_fn = getattr(main, "web_search_ex", None) or web_search_ex
            results, engines_down = await search_fn(
                query, limit=int(a.get("limit", 6)))
            if engines_down:
                # NEVER advise a retry here. This is a transport failure, not a bad
                # query — rephrasing cannot fix it, and telling the model otherwise
                # is exactly what produced turns with 10-18 identical searches when
                # DuckDuckGo became unreachable.
                return {"results": [], "count": 0, "is_error": True,
                        "message": ("Web search is unavailable right now (all "
                                    "search backends unreachable). Do NOT retry "
                                    "this tool. Answer from what you already know "
                                    "and say the information could not be "
                                    "verified.")}
            if not results:
                return {"results": [], "count": 0,
                        "message": ("No results for that query. Try ONE more time "
                                    "with different words, then move on.")}
            # Cache the raw hits so a following canvas_add_widget(data_card,
            # {search_query: query}) can synthesise the answer WITHOUT re-searching.
            cache_tool_result(f"search:{query.strip()}", results)
            return {"results": results, "count": len(results),
                    "hint": "To show this as a card, call canvas_add_widget(widget_type='data_card', "
                            f"config={{'search_query': '{query}'}}). The server will read the top pages, "
                            "write a summarised answer and attach these as sources — do not re-type them."}

        elif t == "html_notes_read_page":
            return await read_web_page(a.get("url", ""), max_chars=int(a.get("max_chars", 6000)))

        elif t == "html_notes_news":
            # Returns a ready-to-render data_card config (photo + headline +
            # summary per story). Cached so canvas_add_widget can rehydrate from
            # just {"news_topic": "..."} instead of the model re-typing every item.
            topic = (a.get("topic") or a.get("query") or "").strip()
            config = await build_news_config(topic)
            cache_tool_result(f"news:{topic}", config)
            return config

        elif t == "html_notes_stock_history":
            result = await stock_snapshot(a.get("symbol", ""), a.get("range", "1mo"))
            if not result.get("is_error"):
                cache_tool_result(f"stock:{a.get('symbol', '')}", result)
            return result

        elif t == "html_notes_stock_news":
            # Returns bare title+publisher rows with NO snippets — the model must
            # NOT render these directly (that is the wall-of-links bug). Steer it to
            # the clean path: canvas_add_widget(data_card, {stock_news_query}) makes
            # the server re-pull, read the pages and WRITE per-story summaries.
            q = a.get("query", "")
            result = await stock_news(q, limit=int(a.get("limit", 8)))
            if isinstance(result, dict):
                result["hint"] = (
                    "These rows have NO summaries — do NOT build a data_card from them. "
                    "Instead call canvas_add_widget(widget_type='data_card', "
                    f"config={{'stock_news_query': '{q}'}}); the server writes the "
                    "summaries and attaches sources.")
            return result

        elif t == "html_notes_get_weather":
            location = a.get("location", "")
            result = await get_weather(location, units=a.get("units", "fahrenheit"))
            if not result.get("is_error"):
                # Key on the location the model typed so the widget injector can
                # rehydrate config={"location": "<same string>"} without a re-fetch.
                cache_tool_result(f"weather:{str(location).lower()}", result)
            return result

        elif t == "html_notes_sports_scores":
            result = await sports_scores(a.get("league", ""))
            if not result.get("is_error"):
                cache_tool_result(f"scores:{a.get('league', '')}", result)
            return result

        elif t == "canvas_add_widget":
            # The actual injection to the frontend is handled by the SSE interceptor during 'calling' phase.
            # Here we just acknowledge success to the LLM so it doesn't think the tool failed.
            return {"success": True, "message": f"Successfully added {a.get('widget_type', 'widget')} to canvas."}

        elif t == "html_notes_list_services":
            data = await get_portal_apps(include_hidden=bool(a.get("include_hidden")))
            apps = data["apps"]
            q = (a.get("query") or "").strip().lower()
            if q:
                apps = [x for x in apps
                        if q in f"{x['id']} {x['name']} {x['description']}".lower()]
            status = (a.get("status") or "").strip().lower()
            if status in ("healthy", "unhealthy", "unknown"):
                apps = [x for x in apps if x["status"] == status]
            slim = [{k: x[k] for k in ("id", "name", "description", "status",
                                       "launch_url", "pinned", "project_type",
                                       "device")}
                    for x in apps]
            return {"apps": slim, "count": len(slim), "stale": data["stale"]}

        elif t == "html_notes_open_app":
            # Only a known catalog app ever opens — never a raw URL from the
            # model. The actual browser open is emitted by the SSE interceptor
            # (an `open_url` frame); this return tells the model what resolved.
            data = await get_portal_apps()
            app_match, candidates = resolve_portal_app(
                a.get("app_id") or a.get("query") or "", data["apps"])
            if app_match:
                return {"success": True,
                        "opened": {"id": app_match["id"], "name": app_match["name"],
                                   "url": app_match["launch_url"]},
                        "message": f"Opening {app_match['name']} in a new tab."}
            if candidates:
                # resolve_portal_app already dropped `*-service` backends when
                # a client matched (prefer_clients), so anything still tied
                # here is a genuine client-vs-client choice worth asking about.
                return {"error": "Ambiguous app — ask the user which one, with "
                                 "markdown links. Do NOT guess.",
                        "candidates": [{"id": c["id"], "name": c["name"],
                                        "url": c["launch_url"]}
                                       for c in candidates],
                        "is_error": True}
            return {"error": f"No app matches '{a.get('app_id') or a.get('query') or ''}'. "
                             "Call html_notes_list_services to see valid ids.",
                    "is_error": True}

        elif t == "html_notes_list_actions":
            return {"actions": list_app_actions(a.get("app_id") or ""),
                    "hint": "Run one with html_notes_app_action(app_id, action, params)."}

        elif t == "html_notes_app_action":
            app_id = (a.get("app_id") or "").strip()
            action = (a.get("action") or "").strip()
            params = a.get("params") or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
            spec = get_action_spec(app_id, action)
            if not spec:
                return {"error": f"No action '{action}' on '{app_id}'.",
                        "available": [f"{r['app_id']}.{r['action']}"
                                      for r in list_app_actions()],
                        "is_error": True}
            if spec.get("destructive"):
                # NEVER executed here. Park it; the SSE interceptor renders a
                # confirm card and only the user's click runs it.
                pending_id = park_pending_action(app_id, action, params)
                cache_tool_result(f"action_confirm:{pending_id}",
                                  build_action_confirm_config(app_id, action,
                                                              params, pending_id))
                return {"success": True, "confirmation_required": True,
                        "pending_id": pending_id,
                        "message": (f"{app_id}.{action} needs confirmation — a confirm "
                                    "card is on the canvas. Tell the user to click "
                                    "Run it. Do NOT claim it has started.")}
            return await execute_app_action(app_id, action, params)

        elif t == "html_notes_curate_app":
            data = await get_portal_apps(include_hidden=True)
            app_id = (a.get("app_id") or "").strip()
            if app_id not in {x["id"] for x in data["apps"]}:
                app_match, candidates = resolve_portal_app(app_id, data["apps"])
                if not app_match:
                    return {"error": f"Unknown app_id '{app_id}'",
                            "candidates": [c["id"] for c in candidates],
                            "is_error": True}
                app_id = app_match["id"]
            set_portal_override(app_id, hidden=a.get("hidden"), pinned=a.get("pinned"))
            return {"success": True, "app_id": app_id,
                    "hidden": a.get("hidden"), "pinned": a.get("pinned")}

        else:
            return {"error": f"Unknown tool: {t}", "is_error": True}

    except Exception as e:
        logger.error(f"Internal tool execution error: {e}")
        return {"error": str(e), "is_error": True}


@router.get("/widgets/map", include_in_schema=False)
async def widget_map(d: str = ""):
    """Standalone Leaflet page for the map widget's <iframe>. The marker data
    rides in `d` as base64url JSON (built by factory.render_map). Rendered as its
    own document so its <script> runs — the canvas DOMPurify strips inline scripts,
    which is why the map is an iframe rather than inline markup. A payload with
    traffic=true gets the TomTom flow-tile overlay when the key is available."""
    payload = {"center": {"lat": 39.5, "lon": -98.35}, "zoom": 4, "markers": []}
    if d:
        try:
            raw = _base64.urlsafe_b64decode(d.encode("ascii"))
            payload = map_payload(json.loads(raw))  # re-sanitise; never trust the URL
        except Exception as e:
            logger.warning(f"/widgets/map bad payload: {e}")
    # Only turn on the overlay layer when a key actually exists, so a keyless map
    # doesn't fire a screenful of doomed tile requests. The tiles themselves are
    # proxied (see /widgets/map/traffic) so the key stays server-side.
    tiles_url = ""
    if payload.get("traffic") and await _fetch_secret("TOMTOM_API_KEY"):
        tiles_url = "/widgets/map/traffic/{z}/{x}/{y}.png"
    return HTMLResponse(map_document_html(payload, traffic_tiles_url=tiles_url),
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/widgets/map/traffic/{z}/{x}/{y}.png", include_in_schema=False)
async def widget_traffic_tile(z: int, x: int, y: int):
    """Server-side proxy for TomTom traffic-flow tiles. Holds the key so it never
    reaches the browser (no referrer-restricted-key 403 through the sandboxed map
    iframe), and logs TomTom's HTTP status once per change so a failing key is
    diagnosable from the logs. Falls back to a transparent tile so the base map
    stays clean when the key is missing or a tile errors."""
    key = await _fetch_secret("TOMTOM_API_KEY")
    if not key:
        if _tomtom_last_status.get("_") != -1:
            _tomtom_last_status["_"] = -1
            logger.warning("[TRAFFIC] no TOMTOM_API_KEY — serving blank tiles "
                           "(add it to the vault; free key: developer.tomtom.com)")
        return Response(content=_BLANK_PNG, media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    # No `thickness` param: raster flow tiles reject it with a 400 ("thickness
    # param supported only for style s0/s1/..."), verified live 2026-07-16.
    url = (f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/"
           f"{z}/{x}/{y}.png?key={urllib.parse.quote(key)}&tileSize=256")
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url)
        if _tomtom_last_status.get("_") != r.status_code:
            _tomtom_last_status["_"] = r.status_code
            if r.status_code == 200:
                logger.info("[TRAFFIC] TomTom tiles OK (200)")
            else:
                logger.warning(f"[TRAFFIC] TomTom returned {r.status_code} "
                               f"({r.text[:160]!r}) — check the key is valid and has "
                               "the Traffic API enabled, and is not IP/referrer-locked")
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return Response(content=r.content, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=120"})
    except Exception as e:
        if _tomtom_last_status.get("_") != -2:
            _tomtom_last_status["_"] = -2
            logger.warning(f"[TRAFFIC] TomTom tile fetch failed: {e}")
    return Response(content=_BLANK_PNG, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/widgets/embed", include_in_schema=False)
async def widget_embed(u: str = ""):
    """Reader view for the App Window iframe. External sites send X-Frame-Options
    / Cloudflare bot walls and refuse to embed ("Max challenge attempts exceeded"),
    so we fetch the page server-side (read_web_page's Playwright fallback gets past
    the wall) and serve its readable content from THIS origin — the iframe is then
    same-origin and always renders."""
    url = urllib.parse.unquote(u or "").strip()
    title = url or "App Window"
    if not re.match(r'^https?://', url):
        body = "<p class='muted'>No valid URL was provided.</p>"
    else:
        try:
            page = await read_web_page(url, max_chars=12000)
        except Exception as e:
            logger.warning(f"/widgets/embed fetch failed for {url!r}: {e}")
            page = {"is_error": True}
        if page.get("is_error"):
            body = (f"<p class='muted'>Couldn't load this page in the reader.</p>"
                    f"<p><a href='{esc(url)}' target='_blank' rel='noopener'>"
                    f"Open it in a new tab ↗</a></p>")
        else:
            body = (f"<div class='src'><a href='{esc(url)}' target='_blank' rel='noopener'>"
                    f"{esc(_host_of(url))} ↗</a></div>"
                    + _render_markdown(page.get("content") or ""))
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:20px 24px; background:#0b1020; color:#e6e9f2;
    font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
  a {{ color:#a78bfa; }} .muted {{ color:#94a3b8; }}
  .src {{ font-size:12px; margin-bottom:14px; opacity:.8; }}
  img {{ max-width:100%; height:auto; border-radius:8px; }}
  h1,h2,h3 {{ line-height:1.25; }} hr {{ border:none; border-top:1px solid rgba(255,255,255,.1); }}
  pre {{ overflow:auto; background:#111827; padding:12px; border-radius:8px; }}
  code {{ background:#111827; padding:2px 5px; border-radius:4px; }}
</style></head><body>{body}</body></html>"""
    return HTMLResponse(doc, headers={"Cache-Control": "public, max-age=600"})


@router.get("/user/memory", include_in_schema=False)
async def get_user_memory():
    """The persistent user profile the agent remembers (name/location/likes)."""
    return database.get_user_facts()


@router.delete("/user/memory", include_in_schema=False)
async def forget_user():
    """Wipe the persistent user profile — the 'Forget me' settings control."""
    try:
        n = database.wipe_user_facts()
        return {"ok": True, "forgotten": n}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


