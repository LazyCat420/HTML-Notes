from fastapi import APIRouter, Request, HTTPException, Response
import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

router = APIRouter()

@router.get("/health/model")
async def health_model():
    """
    Pings local vLLM health metrics endpoint.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{VLLM_URL}/health")
            if res.status_code == 200:
                return {"status": "ok", "vllm": "healthy"}
            return {"status": "unhealthy", "code": res.status_code}
    except Exception as e:
        return {"status": "offline", "detail": str(e)}


async def _search_health(force: bool = False) -> dict:
    """Search status for /health/app, without searching.

    docker-compose curls /health/app every 30 s. This used to run a real
    search for "test" against DuckDuckGo, cached for 5 minutes: about 288
    automated searches a day, from an IP the search engines were already
    bot-blocking. It now reports the outcome of the last real search (an ask,
    a tool call or a watch). `?fresh=1` still runs one live probe when a
    person asks."""
    engines = [n for n, _ in _SEARCH_ENGINES]
    if force:
        try:
            hits, engines_down = await web_search_ex("test", 3)
            result = {"ok": not engines_down, "hits": len(hits), "engines": engines, "probe": "live"}
            if engines_down:
                result["error"] = "every search backend unreachable"
        except Exception as e:
            result = {"ok": False, "error": f"probe raised: {e}", "engines": engines, "probe": "live"}
        return result
    return {"ok": LAST_SEARCH["ok"], "engines": engines, "probe": "none",
            "last_search_at": LAST_SEARCH["at"], "last_engine": LAST_SEARCH["engine"]}


@router.get("/health/app")
async def health_app(fresh: bool = False):
    """LIVENESS — is this process serving?

    Deliberately still 200 when the agent dependency is down. docker-compose
    healthchecks this with `curl -f`, and a non-2xx marks the container
    unhealthy and restarts it — which cannot fix a Prism-side outage and would
    just loop. The dependency is reported in the body, and /health/agent is the
    endpoint that actually fails when research is broken.
    """
    agent = await _agent_dependency_status()
    # Search is reported separately from MCP: they fail independently, and an
    # agent with live tools that all return nothing looks "ok" without this.
    # `?fresh=1` forces a live probe for when a human is actually asking.
    search = await _search_health(force=fresh)
    return {"status": "ok", "service": "html-notes",
            "agent": agent, "search": search}


@router.get("/health/agent")
async def health_agent(response: Response):
    """READINESS — can a research ask succeed?

    503s when the tool path is dead, so a monitor sees it.
    """
    from app.adapters.runtime.config import is_shared_runtime_enabled, check_runtime_readiness
    if is_shared_runtime_enabled():
        readiness = await check_runtime_readiness()
        if not readiness.is_ready:
            response.status_code = 503
            return {
                "status": "unavailable",
                "use_shared_runtime": True,
                "runtime_ready": readiness.is_ready,
                "error": readiness.error,
                "profile_id": readiness.profile_id,
                "details": readiness.details,
            }
        return {
            "status": "ok",
            "use_shared_runtime": True,
            "runtime_ready": readiness.is_ready,
            "contract_version": readiness.contract_version,
            "profile_id": readiness.profile_id,
            "details": readiness.details,
        }

    agent = await _agent_dependency_status()
    if not agent.get("ok"):
        response.status_code = 503
    return {"status": "ok" if agent.get("ok") else "unavailable", "agent": agent}


