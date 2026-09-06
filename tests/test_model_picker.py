"""The model picker in the UI — what it offers, and what it selects by default.

The browser sends `provider` and `model` in the /session/message body, and the
server's selection cascade begins `model_name = req.model`. So whatever the
dropdown has selected SHORT-CIRCUITS the entire server-side preference: a
client-persisted or client-defaulted choice outranks the server's.

Observed 2026-09-05: the UI auto-selected GLM-5.3-FLASH-EXL3 on Gold Spark —
a box that was head-of-line blocked — while the server had been changed to
prefer the Jetson. The server fix was real and had no effect on a real browser
turn, because the client never let it run.

It also offered EMBEDDINGGEMMA as a selectable chat model. That endpoint returns
404 on /v1/chat/completions; picking it cannot do anything but fail.
"""
import pathlib

import pytest

from app import main as m

JS = (pathlib.Path(__file__).resolve().parent.parent
      / "app" / "static" / "index.js").read_text()


def test_client_default_provider_is_the_jetson():
    """`let provider = "vllm-2"` hardcoded Gold Spark as the fallback provider."""
    assert 'let provider = "vllm-2"' not in JS, (
        "the client still hardcodes vllm-2 (Gold Spark) as its default provider")
    assert 'let provider = "vllm"' in JS


def test_model_dropdown_prefers_the_jetson_over_gold_spark():
    """fetchModels picked the FIRST vllm-2 option, falling back to vllm — so the
    dropdown landed on GLM whenever Gold Spark was in the catalog at all."""
    start = JS.index("async function fetchModels()")
    body = JS[start:start + 3000]
    v2 = body.find('"vllm-2"')
    v1 = body.find('"vllm"')
    assert v1 != -1, "fetchModels no longer looks for the vllm provider"
    if v2 != -1:
        assert v1 < v2, (
            "vllm-2 is still checked before vllm — the dropdown will keep "
            "auto-selecting Gold Spark whenever it is present")


def test_models_endpoint_hides_models_that_cannot_chat():
    """A model the user cannot successfully pick must not be in the list."""
    from tests._sources import MESSAGE_SRC
    start = MESSAGE_SRC.index("async def get_models()")
    body = MESSAGE_SRC[start:MESSAGE_SRC.index("\n@router", start + 10)]
    assert "_is_chat_capable_model" in body, (
        "/models still offers embedding models as selectable chat models")


@pytest.mark.asyncio
async def test_models_endpoint_filters_embeddinggemma(patch_server):
    """Drive the endpoint against the catalog shape the gateway really returns."""
    catalog = {"textToText": {"models": {
        "vllm": [{"name": "nemotron35", "label": "nemotron35"}],
        "vllm-2": [{"name": "GLM-5.3-Flash-EXL3", "label": "GLM-5.3-Flash-EXL3"}],
        "vllm-3": [{"name": "embeddinggemma", "label": "embeddinggemma"}],
    }}}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            class _R:
                status_code = 200
                @staticmethod
                def json(): return catalog
            return _R()

    import app.routes.message as msg
    patch_server("httpx", type("_hx", (), {"AsyncClient": _Client})())
    resp = await msg.get_models()
    import json
    names = [m_["model"] for m_ in json.loads(resp.body)["models"]]
    assert "embeddinggemma" not in names, f"embedding model still offered: {names}"
    assert "nemotron35" in names
    assert names[0] == "nemotron35", (
        f"the Jetson's model must be listed FIRST so the dropdown lands on it: {names}")
