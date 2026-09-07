from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

router = APIRouter()

@router.get("/api/actions")
async def api_actions(app_id: str = ""):
    """Every registered container action. Backs the control plane's discovery
    (and is handy for checking the registry without a chat turn)."""
    return {"actions": list_app_actions(app_id)}


@router.post("/api/actions/run")
async def api_actions_run(request: Request):
    """Fire a PARKED destructive action. The agent can never reach this — it
    only parks; the user's click on the confirm card is what calls it."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    pending_id = (body.get("pending_id") or "").strip()
    if not pending_id:
        raise HTTPException(status_code=400, detail="pending_id required")
    return await run_pending_action(pending_id)


@router.post("/api/actions/cancel")
async def api_actions_cancel(request: Request):
    """Drop a parked action so it can never fire."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    _pending_actions.pop((body.get("pending_id") or "").strip(), None)
    return {"success": True}


@router.get("/session/{session_id}/events")
async def session_events(session_id: str):
    """The idle-tab channel: SSE lines pushed between turns (a watch firing,
    a refresh). Keepalive comments every 25 s; unsubscribes on disconnect."""
    from app import canvas_manager as _cm
    q = _cm.subscribe_session_events(session_id)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    line = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield line
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _cm.unsubscribe_session_events(session_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no"})


@router.get("/api/watches")
async def api_watches(session_id: str = ""):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    return {"watches": database.list_watches(session_id)}


@router.delete("/api/watches/{watch_id}")
async def api_watch_delete(watch_id: str, session_id: str = ""):
    if not session_id or not database.delete_watch(watch_id, session_id):
        raise HTTPException(status_code=404, detail="no such watch for this session")
    return {"ok": True, "id": watch_id}


@router.post("/api/widget/{session_id}/{widget_id}/refresh")
async def api_widget_refresh(session_id: str, widget_id: str):
    """Re-pull and re-render ONE live widget in place — no agent turn, no
    chat message. The liveWidget chrome (widgets.js) calls this on its TTL
    and on the ⟳ button; the client paints the returned canvas through the
    normal versioned reconciler, so only the changed widget's node moves."""
    from app import canvas_manager as _cm
    if not _cm.get_widget_recipe(session_id, widget_id):
        raise HTTPException(status_code=404, detail="no recipe for this widget")
    try:
        return await _cm.refresh_widget(session_id, widget_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="no recipe for this widget")
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/api/services")
async def api_services(include_hidden: bool = False):
    """The curated PortalApp list (portal-service inventory ⊕ registry file ⊕
    DB overlay). Backs the App Hub widget's 45s status poll, so a container
    going down flips its dot without an agent turn or a canvas repaint."""
    return await get_portal_apps(include_hidden=include_hidden)


@router.post("/api/services/{app_id}/override")
async def api_services_override(app_id: str, request: Request):
    """Runtime hide/pin from the widget's ✕/📌 buttons. Writes the DB overlay
    (survives restarts, no redeploy); git defaults live in portal_registry.json."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    hidden = body.get("hidden") if isinstance(body.get("hidden"), bool) else None
    pinned = body.get("pinned") if isinstance(body.get("pinned"), bool) else None
    if hidden is None and pinned is None:
        raise HTTPException(status_code=400,
                            detail="body must set boolean 'hidden' and/or 'pinned'")
    set_portal_override(app_id, hidden=hidden, pinned=pinned)
    return {"success": True, "app_id": app_id}


@router.get("/api/stock/{symbol}")
async def api_stock(symbol: str, range: str = "1mo"):
    """Backs the stock widget's range tabs — switching 1D/1M/1Y/10Y/MAX refetches
    here instead of going through the agent again."""
    return await stock_snapshot(symbol, range)


@router.get("/api/fx/{base}")
async def api_fx(base: str):
    """Backs the converter's currency tab — latest rates for `base` (keyless,
    cached). Empty {} degrades to 'Rates unavailable' client-side."""
    return await fetch_fx_rates(base)


@router.get("/api/crypto/{coin_id}")
async def api_crypto(coin_id: str, range: str = "30d"):
    """Backs the crypto card's range tabs — switching 1D/7D/30D/1Y/MAX refetches
    the price series here instead of going through the agent again."""
    return await _crypto_snapshot(coin_id, range)


@router.get("/api/youtube/candidates")
async def api_youtube_candidates(query: str, limit: int = 6,
                                 form: Optional[str] = None):
    """Multi-result YouTube search used by the player widget to recover from
    embed-blocked videos (it walks the list until one plays). `form` keeps the
    replacement the same KIND as what was playing (a Short hops to a Short)."""
    results = await search_youtube_videos(query, limit=min(limit, 12), form=form)
    return {"results": results, "count": len(results)}


@router.get("/api/youtube/search")
async def api_youtube_search(query: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MUSIC_PLAYER_URL}/api/youtube/search", params={"query": query}, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except Exception as e:
        logger.error(f"Failed to proxy YouTube search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


