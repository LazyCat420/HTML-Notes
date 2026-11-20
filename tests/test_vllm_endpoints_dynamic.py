"""Regression tests for TCK-1032: dynamic local endpoints and /v1/models resolution."""
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as m


@pytest.mark.asyncio
async def test_fast_llm_json_uses_only_configured_endpoints_never_30(patch_server):
    seen_urls: list[str] = []
    patch_server("VLLM_ENDPOINTS", ["http://10.0.0.37:8000", "http://10.0.0.131:8000"])
    patch_server("VLLM_URL", "http://10.0.0.37:8000")
    patch_server("_ENDPOINT_LAST_FAILURE", {})
    patch_server("_fast_model", {})

    class _Resp:
        def __init__(self, code, data):
            self.status_code = code
            self._data = data

        def json(self):
            return self._data

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            seen_urls.append(url)
            if "10.0.0.37" in url:
                return _Resp(503, {})
            return _Resp(200, {"data": [{"id": "deepseek-local-v3"}]})

        async def post(self, url, json=None, **kw):
            seen_urls.append(url)
            assert json["model"] == "deepseek-local-v3"
            return _Resp(200, {"choices": [{"message": {"content": '{"answer":"ok"}'}}]})

    patch_server("httpx", type("_H", (), {
        "AsyncClient": _Client,
        "Timeout": httpx.Timeout,
        "HTTPError": httpx.HTTPError,
    })())

    out = await m.fast_llm_json("return json", max_tokens=100)
    assert out == {"answer": "ok"}
    assert all("10.0.0.30" not in u for u in seen_urls), seen_urls
    assert any("10.0.0.131:8000/v1/models" in u for u in seen_urls), seen_urls


def test_send_message_resolves_dynamic_v1_models_without_hardcoded_nemotron(patch_server):
    import respx
    from unittest.mock import AsyncMock

    patch_server("VLLM_ENDPOINTS", ["http://10.0.0.131:8000"])
    patch_server("VLLM_URL", "http://10.0.0.131:8000")
    patch_server("route_with_llm", AsyncMock(return_value={"defer": True}))
    patch_server("build_apps_prompt_block", AsyncMock(return_value=""))
    patch_server("build_actions_prompt_block", AsyncMock(return_value=""))

    with respx.mock(assert_all_mocked=False) as mock:
        mock.get(f"{m.LAZY_AGENT_URL}/config-local").respond(503, json={})
        mock.get("http://10.0.0.131:8000/v1/models").respond(
            200, json={"data": [{"id": "qwen3-coder-32b"}]}
        )
        agent_route = mock.post(f"{m.LAZY_AGENT_URL}/agent").respond(
            200,
            headers={"content-type": "text/event-stream"},
            text=f'data: {json.dumps({"type": "done"})}\n\n',
        )

        res = TestClient(m.app).post(
            "/session/message",
            json={"session_id": "sess-dyn-v1", "message": "build me a custom widget that counts pushups"},
        )
    assert res.status_code == 200
    assert agent_route.called
    sent_payload = json.loads(agent_route.calls[0].request.content.decode("utf-8"))
    assert sent_payload["provider"] == "vllm-4"
    assert sent_payload["model"] == "qwen3-coder-32b"


def test_send_message_overrides_mismatched_client_provider_on_fallback(patch_server):
    import respx
    from unittest.mock import AsyncMock

    patch_server("VLLM_ENDPOINTS", ["http://10.0.0.141:8000"])
    patch_server("VLLM_URL", "http://10.0.0.141:8000")
    patch_server("route_with_llm", AsyncMock(return_value={"defer": True}))
    patch_server("build_apps_prompt_block", AsyncMock(return_value=""))
    patch_server("build_actions_prompt_block", AsyncMock(return_value=""))

    with respx.mock(assert_all_mocked=False) as mock:
        mock.get(f"{m.LAZY_AGENT_URL}/config-local").respond(503, json={})
        mock.get("http://10.0.0.141:8000/v1/models").respond(
            200, json={"data": [{"id": "DeepSeek-V4.1-Flash-1017"}]}
        )
        agent_route = mock.post(f"{m.LAZY_AGENT_URL}/agent").respond(
            200,
            headers={"content-type": "text/event-stream"},
            text=f'data: {json.dumps({"type": "done"})}\n\n',
        )
        res = TestClient(m.app).post(
            "/session/message",
            json={"session_id": "sess-pair-141", "provider": "vllm", "message": "build me a custom habit tracker"},
        )
    assert res.status_code == 200
    sent_payload = json.loads(agent_route.calls[0].request.content.decode("utf-8"))
    assert sent_payload["provider"] == "vllm-2"
    assert sent_payload["model"] == "DeepSeek-V4.1-Flash-1017"


def test_send_message_returns_error_sse_when_no_local_vllm_model_available(patch_server):
    import respx
    from unittest.mock import AsyncMock

    patch_server("VLLM_ENDPOINTS", ["http://10.0.0.131:8000"])
    patch_server("VLLM_URL", "http://10.0.0.131:8000")
    patch_server("route_with_llm", AsyncMock(return_value={"defer": True}))
    patch_server("build_apps_prompt_block", AsyncMock(return_value=""))
    patch_server("build_actions_prompt_block", AsyncMock(return_value=""))

    with respx.mock(assert_all_mocked=False) as mock:
        mock.get(f"{m.LAZY_AGENT_URL}/config-local").respond(503, json={})
        mock.get("http://10.0.0.131:8000/v1/models").respond(503, json={})

        res = TestClient(m.app).post(
            "/session/message",
            json={"session_id": "sess-dyn-down", "message": "build me a custom widget that counts pushups"},
        )
    assert res.status_code == 200
    assert "No local vLLM model is currently reachable." in res.text
    assert "nemotron35" not in res.text


def test_configurable_endpoint_providers_skips_unmapped_hosts(patch_server):
    import respx
    from unittest.mock import AsyncMock

    patch_server("VLLM_ENDPOINTS", ["http://10.0.0.250:8000", "http://10.0.0.99:8000"])
    patch_server("VLLM_URL", "")
    patch_server("VLLM_ENDPOINT_PROVIDERS", {"http://10.0.0.99:8000": "vllm-9"})
    patch_server("route_with_llm", AsyncMock(return_value={"defer": True}))
    patch_server("build_apps_prompt_block", AsyncMock(return_value=""))
    patch_server("build_actions_prompt_block", AsyncMock(return_value=""))

    with respx.mock(assert_all_mocked=False, assert_all_called=False) as mock:
        mock.get(f"{m.LAZY_AGENT_URL}/config-local").respond(503, json={})
        unmapped_route = mock.get("http://10.0.0.250:8000/v1/models").respond(
            200, json={"data": [{"id": "wrong-host-model"}]}
        )
        mapped_route = mock.get("http://10.0.0.99:8000/v1/models").respond(
            200, json={"data": [{"id": "custom-cluster-model"}]}
        )
        agent_route = mock.post(f"{m.LAZY_AGENT_URL}/agent").respond(
            200,
            headers={"content-type": "text/event-stream"},
            text=f'data: {json.dumps({"type": "done"})}\n\n',
        )
        res = TestClient(m.app).post(
            "/session/message",
            json={"session_id": "sess-custom-map", "message": "build me a custom habit tracker"},
        )
    assert res.status_code == 200
    assert unmapped_route.called is False
    assert mapped_route.called is True
    sent_payload = json.loads(agent_route.calls[0].request.content.decode("utf-8"))
    assert sent_payload["provider"] == "vllm-9"
    assert sent_payload["model"] == "custom-cluster-model"
