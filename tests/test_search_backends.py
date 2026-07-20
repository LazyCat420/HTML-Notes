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


def test_brave_is_the_primary_engine():
    """DDG is unreachable from the NAS; Brave must be tried first. The DDG
    engines stay behind it so this self-heals if DDG comes back."""
    names = [n for n, _ in m._SEARCH_ENGINES]
    assert names[0] == "brave-api"
    assert "ddg-lite" in names and "ddg-collector" in names


def test_web_search_still_returns_a_plain_list(monkeypatch, no_backfill):
    """Call sites were not changed — the list-returning shape must hold."""
    async def good(q, n):
        return [{"title": "T", "url": "https://x.com", "snippet": "s"}]
    monkeypatch.setattr(m, "_SEARCH_ENGINES", (("stub", good),))
    out = _run(m.web_search("q", 5))
    assert isinstance(out, list) and out[0]["title"] == "T"


# ── Brave response normalization ────────────────────────────────────────────

def test_brave_normalizes_and_strips_highlight_markup(monkeypatch):
    """Brave wraps matched terms in <strong>; that must not reach the widget."""
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"web": {"results": [
                {"title": "Best Sandals",
                 "url": "https://example.com/a",
                 "description": "The <strong>best</strong> waterproof pick."},
                {"title": "no url", "url": "", "description": "skip me"},
            ]}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    async def key(_name):
        return "test-key"

    monkeypatch.setattr(m, "_fetch_secret", key)
    monkeypatch.setattr(m.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(m, "_BRAVE_MIN_INTERVAL", 0)
    out = _run(m._search_brave_api("sandals", 5))
    assert len(out) == 1, "a result with no url must be dropped"
    assert out[0]["snippet"] == "The best waterproof pick."
    assert "<strong>" not in out[0]["snippet"]


def test_brave_without_a_key_is_skipped_not_fatal(monkeypatch):
    async def nokey(_name):
        return ""
    monkeypatch.setattr(m, "_fetch_secret", nokey)
    assert _run(m._search_brave_api("q", 5)) == []


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
