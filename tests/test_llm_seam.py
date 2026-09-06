"""The shared LLM seam: reasoning control, token budget, endpoint pool.

Every server-side widget builder, the tier-2 router and the grounding pass all
funnel through `fast_llm_json`, so its per-call cost is multiplied across the
whole app. Measured against the live Jetson on 2026-09-05, on the REAL router
prompt:

    thinking ON   20.2s / 17.6s / 25.6s   -> the SAME routing decision
    thinking OFF   1.2s /  1.3s /  1.7s

and with reasoning on, a sane token budget yields NO CONTENT AT ALL — the
reasoning trace consumes the whole allowance and `content` comes back empty:

    max_tokens=550  -> completion_tokens=550,  content_len=0, finish=length
    max_tokens=4096 -> completion_tokens=4096, content_len=3498, 61.2s

That empty-content failure is why a `max(max_tokens, 4096)` floor was added,
which then made every call on every path pay for a 4096-token generation. These
guards pin the fix so neither half can quietly come back.
"""
import inspect

import pytest

from app import main as m
from app import config as cfg


# ── reasoning must be off for structured-JSON calls ──────────────────────────

@pytest.mark.asyncio
async def test_fast_llm_json_disables_reasoning(patch_server):
    """The one parameter worth 15x. `enable_thinking` is the spelling this box
    honours: measured, `thinking:false`, `reasoning_effort:"low"`, `/no_think`
    and 'detailed thinking off' all left the trace ON."""
    sent = {}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            return _Resp(200, {"data": [{"id": "nemotron35"}]})
        async def post(self, url, json=None, **kw):
            sent.update(json or {})
            return _Resp(200, {"choices": [{"message": {"content": '{"ok": 1}'}}]})

    class _Resp:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return self._b

    patch_server("httpx", _fake_httpx(_Client))
    out = await m.fast_llm_json("give me json", max_tokens=200)
    assert out == {"ok": 1}
    kwargs = sent.get("chat_template_kwargs") or {}
    assert kwargs.get("enable_thinking") is False, (
        f"fast_llm_json must disable reasoning; payload carried {sent!r}")


@pytest.mark.asyncio
async def test_caller_token_budget_is_honoured(patch_server):
    """A `max(max_tokens, 4096)` floor silently overrode every caller — the
    router asks for 550, the vision gate for 120. With reasoning off, the small
    budget is enough, and the floor only buys a slower generation."""
    sent = {}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            return _R(200, {"data": [{"id": "nemotron35"}]})
        async def post(self, url, json=None, **kw):
            sent.update(json or {})
            return _R(200, {"choices": [{"message": {"content": '{"ok": 1}'}}]})

    class _R:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return self._b

    patch_server("httpx", _fake_httpx(_Client))
    await m.fast_llm_json("x", max_tokens=550)
    assert sent.get("max_tokens") == 550, (
        f"caller asked for 550, payload sent {sent.get('max_tokens')!r}")


# ── the endpoint pool must not contain a box that cannot chat ────────────────

def test_chat_pool_excludes_the_embedding_port():
    """10.0.0.30:8001 serves embeddinggemma and has NO chat endpoint —
    /v1/chat/completions returns 404. It was in the rotation, where it could
    only ever cost a round-trip. HTML-Notes uses no embeddings at all."""
    pool = " ".join(cfg.VLLM_ENDPOINTS)
    assert "8001" not in pool, (
        f"the embeddings port is still in the CHAT pool: {cfg.VLLM_ENDPOINTS}")
    # Read the LIST, not the file: a comment explaining why :8001 was removed
    # must pass, while a resolvable endpoint must fail. Grepping the source
    # cannot tell those apart.
    import ast
    tree = ast.parse(inspect.getsource(m.fast_llm_json).strip())
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    live = [v for v in literals if v.startswith("http")]
    assert live, "no endpoint literals found — has the pool moved?"
    assert not any("8001" in v for v in live), (
        f"the hardcoded fallback pool still lists the embeddings port: {live}")


def test_jetson_is_the_declared_default():
    """Preference, not a pin: Gold Spark stays configured as a fallback so it
    rejoins on its own when it recovers."""
    assert "10.0.0.30:8000" in cfg.VLLM_URL, cfg.VLLM_URL
    assert cfg.VLLM_ENDPOINTS[0] == cfg.VLLM_URL
    assert any("10.0.0.141" in u for u in cfg.VLLM_ENDPOINTS), (
        "Gold Spark must remain a fallback — taking a box out is done by "
        "leaving its URL unset, never by deleting the fallback path")


def test_read_timeout_is_bounded():
    """A 90s read timeout across a 3-endpoint rotation is 270s of dead air. With
    reasoning off the p95 is under 2s; 90s only gives a hung box more rope. This
    is exactly how one measured turn reached 201s."""
    src = inspect.getsource(m.fast_llm_json)
    import re
    got = [float(x) for x in re.findall(r"httpx\.Timeout\(\s*([0-9.]+)", src)]
    assert got, "no httpx.Timeout(...) found in fast_llm_json"
    assert max(got) <= 30.0, f"read timeout still {got}"


def _fake_httpx(client_cls):
    """A stand-in httpx module exposing just what fast_llm_json touches."""
    import httpx as real

    class _M:
        AsyncClient = client_cls
        Timeout = real.Timeout
        HTTPError = real.HTTPError
    return _M()


# ── agent model selection: a DECLARED capability is not a measured one ───────

def test_agent_model_selection_rejects_embedding_models():
    """The gateway's /config-local advertises, verbatim on 2026-09-05:

        vllm    | nemotron35          | conversation | ['Tool Calling']
        vllm-2  | GLM-5.3-Flash-EXL3  | conversation | ['Thinking','Tool Calling']
        vllm-3  | embeddinggemma      | conversation | ['Tool Calling']

    embeddinggemma has no chat endpoint at all, so one of the three entries
    declares a capability it provably does not have. The selection loop took the
    FIRST match in dict order — it lands on nemotron today by luck, not policy.
    """
    from tests._sources import MESSAGE_SRC
    assert "_is_chat_capable_model" in MESSAGE_SRC, (
        "model selection must reject known-non-chat models rather than trusting "
        "the catalog's 'Tool Calling' claim")
    assert "PREFERRED_AGENT_PROVIDERS" in MESSAGE_SRC, (
        "the Jetson must be a declared preference, not dict-iteration order")


def test_agent_model_selection_does_not_block_the_event_loop():
    """`httpx.Client` (sync) inside the async handler stalled the whole event
    loop for up to 10s per agent turn — /config-local takes ~3s by its own
    comment, and that is serialised across every concurrent user."""
    from tests._sources import MESSAGE_SRC
    import re
    start = MESSAGE_SRC.index("target_url = LAZY_AGENT_URL")
    end = MESSAGE_SRC.index("payload = {", start)
    region = MESSAGE_SRC[start:end]
    assert not re.search(r"with httpx\.Client\(", region), (
        "blocking httpx.Client is back in the agent payload build")


def test_embeddinggemma_is_not_chat_capable():
    """The predicate itself, not just its call site."""
    assert m._is_chat_capable_model("embeddinggemma") is False
    assert m._is_chat_capable_model("embeddinggemma-300m") is False
    assert m._is_chat_capable_model("text-embedding-3-small") is False
    assert m._is_chat_capable_model("nemotron35") is True
    assert m._is_chat_capable_model("GLM-5.3-Flash-EXL3") is True


# ── the healthcheck must not hammer the search backend ──────────────────────

@pytest.mark.asyncio
async def test_health_search_probe_is_cached():
    """docker-compose curls /health/app every 30s, and the probe is a REAL
    DuckDuckGo query — ~2,880 live searches a day to answer "is search up",
    which floods the log and invites a rate-limit on the backend the app
    depends on."""
    import app.routes.health as h

    calls = {"n": 0}

    async def fake_search_ex(q, n):
        calls["n"] += 1
        return ([{"title": "t", "url": "http://x", "snippet": "s"}], False)

    h._search_probe_cache.update({"at": 0.0, "result": None})
    real = h.web_search_ex
    h.web_search_ex = fake_search_ex
    try:
        first = await h._search_health()
        for _ in range(5):
            again = await h._search_health()
        assert calls["n"] == 1, f"probe ran {calls['n']}x for 6 healthchecks"
        assert again.get("cached") is True
        assert first["ok"] is True
        # ...but a human asking explicitly still gets a live answer.
        await h._search_health(force=True)
        assert calls["n"] == 2
    finally:
        h.web_search_ex = real
        h._search_probe_cache.update({"at": 0.0, "result": None})
