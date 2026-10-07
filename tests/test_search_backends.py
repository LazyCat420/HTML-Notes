"""Web-search backend health and the outage-vs-no-results distinction.

The bug these pin: DuckDuckGo became unreachable from the NAS, and since BOTH
engines were DuckDuckGo, search failed closed and returned [] for every query.
The tool then told the model "Retry with a shorter, simpler query" — so it
retried 10-18 times per turn. An engine outage must never be reported as a bad
query.
"""
import os
import asyncio
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_search_backends.db")

from app import main as m


def _run(coro):
    # asyncio.run, not get_event_loop().run_until_complete: the latter picks up
    # whatever loop a previously-run async test left behind, so these pass in
    # isolation and fail in the full suite.
    return asyncio.run(coro)


@pytest.fixture
def no_backfill(monkeypatch):
    async def _noop(results):
        return results
    monkeypatch.setattr(m, "_backfill_snippets", _noop)


# ── the outage-vs-no-results distinction ────────────────────────────────────

def test_all_engines_unreachable_is_flagged(monkeypatch, no_backfill):
    """Every engine raising = outage. The caller must be able to tell."""
    async def boom(q, n):
        raise OSError("ConnectTimeout")
    monkeypatch.setattr(m, "_SEARCH_ENGINES", tuple(
        (name, boom) for name, _ in m._SEARCH_ENGINES))
    results, down = _run(m.web_search_ex("anything", 5))
    assert results == []
    assert down is True


def test_engines_alive_but_no_hits_is_not_an_outage(monkeypatch, no_backfill):
    """An engine that ANSWERS with zero hits proves the backend is alive."""
    async def empty(q, n):
        return []
    monkeypatch.setattr(m, "_SEARCH_ENGINES", (("stub", empty),))
    results, down = _run(m.web_search_ex("zxqw obscure", 5))
    assert results == []
    assert down is False


def test_first_engine_with_hits_wins(monkeypatch, no_backfill):
    calls = []

    async def dead(q, n):
        calls.append("dead")
        raise OSError("down")

    async def good(q, n):
        calls.append("good")
        return [{"title": "T", "url": "https://x.com", "snippet": "s"}]

    async def never(q, n):
        calls.append("never")
        return [{"title": "N", "url": "https://n.com", "snippet": ""}]

    monkeypatch.setattr(m, "_SEARCH_ENGINES",
                        (("dead", dead), ("good", good), ("never", never)))
    results, down = _run(m.web_search_ex("q", 5))
    assert [r["title"] for r in results] == ["T"]
    assert down is False
    assert "never" not in calls, "kept trying engines after one succeeded"


def test_search_goes_only_to_the_shared_web_search():
    """The search engines bot-block this network's IP; html-notes scraping
    DuckDuckGo made it worse for every project (docs/WEB_SEARCH.md). One engine:
    lazy-agent-service's shared keyless search."""
    names = [n for n, _ in m._SEARCH_ENGINES]
    assert names == ["shared-web"]
    assert not any("ddg" in n or "duck" in n for n in names)


def test_web_search_still_returns_a_plain_list(monkeypatch, no_backfill):
    """Call sites were not changed — the list-returning shape must hold."""
    async def good(q, n):
        return [{"title": "T", "url": "https://x.com", "snippet": "s"}]
    monkeypatch.setattr(m, "_SEARCH_ENGINES", (("stub", good),))
    out = _run(m.web_search("q", 5))
    assert isinstance(out, list) and out[0]["title"] == "T"


# ── the shared web search (lazy-agent-service /execute/web_search) ──────────

def _client(reply=None, error=None, calls=None):
    class _Resp:
        status_code = 200

        def json(self):
            return reply

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            if calls is not None:
                calls.append((url, json))
            if error:
                raise error
            return _Resp()
    return _Client


def test_shared_web_search_normalizes_and_filters_results(monkeypatch):
    calls = []
    monkeypatch.setattr(m.httpx, "AsyncClient", _client({"status": "ok", "results": [
        {"title": "Best Sandals", "url": "https://example.com/a", "snippet": "The best waterproof pick."},
        {"title": "no url", "url": "", "snippet": "skip me"},
        {"title": "", "url": "https://example.com/b", "snippet": "no title"},
    ]}, calls=calls))
    out = _run(m._search_shared_web("sandals", 5))
    assert out == [{"title": "Best Sandals", "url": "https://example.com/a",
                    "snippet": "The best waterproof pick."}]
    assert calls == [(f"{m.LAZY_TOOL_SERVICE_URL}/execute/web_search", {"query": "sandals", "limit": 5})]


def test_shared_web_search_with_nothing_found_is_an_empty_list(monkeypatch):
    monkeypatch.setattr(m.httpx, "AsyncClient", _client({"status": "ok", "results": []}))
    assert _run(m._search_shared_web("zxqw", 5)) == []


@pytest.mark.parametrize("reply,error", [
    ({"status": "rate_limited", "error": "searches resume in 40 s"}, None),
    ({"status": "busy", "error": "too many searches queued"}, None),
    ({"status": "error", "error": "Exa unreachable"}, None),
    (None, OSError("lazy-agent-service down")),
])
def test_shared_web_search_raises_when_it_cannot_answer(monkeypatch, reply, error):
    """A refusal or an outage must read as "unreachable" to web_search_ex, not
    as "no results", or the tool tells the model to retry a working query."""
    monkeypatch.setattr(m.httpx, "AsyncClient", _client(reply, error))
    with pytest.raises(Exception):
        _run(m._search_shared_web("q", 5))


# ── the tool contract: no retry advice on an outage ─────────────────────────

def test_tool_never_advises_retry_when_backends_are_down(monkeypatch):
    async def down(q, limit=6):
        return [], True
    monkeypatch.setattr(m, "web_search_ex", down)
    out = _run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_web_search", args={"query": "sandals"})))
    assert out["is_error"] is True
    msg = out["message"].lower()
    assert "do not retry" in msg
    assert "shorter" not in msg and "simpler" not in msg


def test_tool_allows_one_reword_on_a_genuine_miss(monkeypatch):
    async def empty(q, limit=6):
        return [], False
    monkeypatch.setattr(m, "web_search_ex", empty)
    out = _run(m.internal_tool_execute(m.InternalToolRequest(
        tool="html_notes_web_search", args={"query": "zxqw"})))
    assert not out.get("is_error")
    assert "one more time" in out["message"].lower()


# ── every real search records its outcome for /health/app ───────────────────

def test_web_search_ex_records_each_outcome(monkeypatch):
    from app.services import search as s
    saved = dict(s.LAST_SEARCH)

    async def hits(q, n):
        return [{"title": "t", "url": "http://x", "snippet": "s"}]

    async def nothing(q, n):
        return []

    async def boom(q, n):
        raise RuntimeError("unreachable")

    async def no_backfill(results):
        return None

    monkeypatch.setattr(s, "_backfill_snippets", no_backfill)
    try:
        monkeypatch.setattr(m, "_SEARCH_ENGINES", (("one", hits),))
        _run(m.web_search_ex("q", 3))
        assert s.LAST_SEARCH["ok"] is True and s.LAST_SEARCH["engine"] == "one"
        monkeypatch.setattr(m, "_SEARCH_ENGINES", (("one", nothing),))
        _run(m.web_search_ex("q", 3))
        assert s.LAST_SEARCH["ok"] is True and s.LAST_SEARCH["engine"] is None
        monkeypatch.setattr(m, "_SEARCH_ENGINES", (("one", boom),))
        _run(m.web_search_ex("q", 3))
        assert s.LAST_SEARCH["ok"] is False
    finally:
        s.LAST_SEARCH.update(saved)
