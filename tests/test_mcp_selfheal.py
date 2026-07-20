"""MCP self-healing.

The outage behind the 2026-07-20 wave: registration present, enabled, the
:5591/mcp/sse endpoint live and serving — prism had simply never dialled it.
`lastError: null`, invisible for hours, fixed by one POST. So do the POST.
"""
import os
import asyncio
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_mcp_selfheal.db")

from app import main as m


def _run(coro):
    return asyncio.run(coro)


class _FakePrism:
    """Minimal prism stand-in. `connect_makes_it_work` decides whether the POST
    actually brings the link up."""

    def __init__(self, servers, connect_makes_it_work=True):
        self.servers = servers
        self.connect_makes_it_work = connect_makes_it_work
        self.connect_calls = []
        self.get_headers = []

    def _resp(self, payload):
        class _R:
            def __init__(self, p):
                self._p = p
                self.status_code = 200

            def json(self):
                return self._p

            def raise_for_status(self):
                pass
        return _R(payload)

    async def get(self, url, headers=None, **k):
        self.get_headers.append(headers or {})
        return self._resp(self.servers)

    async def post(self, url, headers=None, **k):
        self.connect_calls.append(url)
        if self.connect_makes_it_work:
            for s in self.servers:
                s["connected"] = True
                s["toolCount"] = 76
        return self._resp({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _install(monkeypatch, prism):
    monkeypatch.setattr(m.httpx, "AsyncClient", lambda *a, **k: prism)
    # Capture the REAL sleep first: m.asyncio is the global module, so a lambda
    # that calls asyncio.sleep would call the patched version — itself.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(m.asyncio, "sleep", lambda *_a, **_k: real_sleep(0))


def test_reconnects_a_registered_but_undialled_server(monkeypatch):
    prism = _FakePrism([{"name": m.MCP_SERVER_NAME, "id": "abc123",
                         "connected": False, "toolCount": 0, "enabled": True}])
    _install(monkeypatch, prism)
    assert _run(m._try_reconnect_mcp()) is True
    assert any("/mcp-servers/abc123/connect" in u for u in prism.connect_calls)


def test_no_connect_when_already_healthy(monkeypatch):
    prism = _FakePrism([{"name": m.MCP_SERVER_NAME, "id": "abc123",
                         "connected": True, "toolCount": 76}])
    _install(monkeypatch, prism)
    assert _run(m._try_reconnect_mcp()) is True
    assert prism.connect_calls == [], "poked a healthy connection"


def test_connected_but_zero_tools_still_reconnects(monkeypatch):
    """A connected server serving 0 tools is the shape a half-dead SSE link
    takes — treat it as down."""
    prism = _FakePrism([{"name": m.MCP_SERVER_NAME, "id": "abc123",
                         "connected": True, "toolCount": 0}])
    _install(monkeypatch, prism)
    _run(m._try_reconnect_mcp())
    assert prism.connect_calls, "0 tools was treated as healthy"


def test_reports_failure_when_connect_does_not_take(monkeypatch):
    prism = _FakePrism([{"name": m.MCP_SERVER_NAME, "id": "abc123",
                         "connected": False, "toolCount": 0}],
                       connect_makes_it_work=False)
    _install(monkeypatch, prism)
    assert _run(m._try_reconnect_mcp()) is False


def test_unregistered_server_is_not_our_problem_to_fix(monkeypatch):
    """lazy-tool-service registers itself on ITS boot; we can't do it for it."""
    prism = _FakePrism([{"name": "someone-else", "id": "x", "connected": True}])
    _install(monkeypatch, prism)
    assert _run(m._try_reconnect_mcp()) is False
    assert prism.connect_calls == []


def test_queries_are_scoped_by_headers(monkeypatch):
    """GET /mcp-servers returns [] for ANY state without x-project/x-username —
    the exact thing that made this look like an empty registry."""
    prism = _FakePrism([{"name": m.MCP_SERVER_NAME, "id": "abc123",
                         "connected": True, "toolCount": 76}])
    _install(monkeypatch, prism)
    _run(m._try_reconnect_mcp())
    assert prism.get_headers, "no GET was issued"
    for h in prism.get_headers:
        assert h.get("x-project") == m.AGENT_PROJECT
        assert h.get("x-username") == m.AGENT_USERNAME


def test_reconnect_never_raises(monkeypatch):
    """A self-heal that can take the app down is worse than the bug it fixes."""
    class _Boom:
        async def __aenter__(self):
            raise OSError("prism unreachable")

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(m.httpx, "AsyncClient", lambda *a, **k: _Boom())
    assert _run(m._try_reconnect_mcp()) is False
