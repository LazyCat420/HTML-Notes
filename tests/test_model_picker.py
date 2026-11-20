"""The model picker in the UI — what it offers, and what it selects by default.

/models filters non-chat models (e.g. embeddinggemma) and orders providers by
PREFERRED_AGENT_PROVIDERS (vllm-2, vllm-4, vllm-5, vllm). The browser trusts
that server-ranked order instead of hardcoding a legacy Jetson override.
"""
import pathlib

import pytest

from app import main as m

JS = (pathlib.Path(__file__).resolve().parent.parent
      / "app" / "static" / "index.js").read_text()


def test_client_default_provider_lets_server_choose():
    """When the dropdown has no selection, client delegates provider selection to server."""
    assert 'let provider = "vllm-2"' not in JS
    assert 'let provider = "vllm"' not in JS
    assert 'let provider = "";' in JS
    assert 'provider: provider || null,' in JS


def test_model_dropdown_trusts_server_provider_order():
    """fetchModels picks options[0] as ranked by /models instead of overriding with 'vllm'."""
    start = JS.index("async function fetchModels()")
    body = JS[start:start + 2200]
    assert 'elements.modelSelect.options[0].selected = true;' in body
    assert 'val.provider === "vllm"' not in body


def test_models_endpoint_hides_models_that_cannot_chat():
    """A model the user cannot successfully pick must not be in the list."""
    from tests._sources import MESSAGE_SRC
    start = MESSAGE_SRC.index("async def get_models()")
    body = MESSAGE_SRC[start:MESSAGE_SRC.index("\n@router", start + 10)]
    assert "_is_chat_capable_model" in body, (
        "/models still offers embedding models as selectable chat models")


@pytest.mark.asyncio
async def test_models_endpoint_filters_embeddinggemma(patch_server):
    """Drive the endpoint against the catalog shape the gateway returns."""
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
    assert names == ["GLM-5.3-Flash-EXL3", "nemotron35"], names
