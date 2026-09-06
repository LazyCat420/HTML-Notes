import time

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


# The search probe is a REAL DuckDuckGo query, and docker-compose healthchecks
# /health/app every 30s — roughly 2,880 live searches a day purely to answer
# "is search up", which floods the log with `[SEARCH] ddg-lite served 'test'`
# and is a good way to earn a rate-limit on the backend the app depends on.
# Cache it: the probe's value is catching a sustained outage, and a 5-minute
# resolution does that just as well as a 30-second one.
_SEARCH_PROBE_TTL = 300.0
_search_probe_cache: dict = {"at": 0.0, "result": None}


async def _search_health(force: bool = False) -> dict:
    now = time.monotonic()
    cached = _search_probe_cache["result"]
    if cached is not None and not force and (now - _search_probe_cache["at"]) < _SEARCH_PROBE_TTL:
        return dict(cached, cached=True)
    try:
        hits, engines_down = await web_search_ex("test", 3)
        result = {"ok": not engines_down, "hits": len(hits),
                  "engines": [n for n, _ in _SEARCH_ENGINES]}
        if engines_down:
            result["error"] = "every search backend unreachable"
    except Exception as e:
        result = {"ok": False, "error": f"probe raised: {e}"}
    _search_probe_cache.update({"at": now, "result": result})
    return result


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

    503s when the tool path is dead, so a monitor sees it. Separate from
    /health/app precisely because the right response to this failing is to go
    look at Prism or lazy-tool-service, never to restart html-notes.
    """
    agent = await _agent_dependency_status()
    if not agent.get("ok"):
        response.status_code = 503
    return {"status": "ok" if agent.get("ok") else "unavailable", "agent": agent}


