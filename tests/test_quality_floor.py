"""Quality floor (P0 bug ③): a data_card must never render as naked links. If it
shows links it must carry a summary — either per-item descriptions or, failing
that, a synthesized top-level answer. Fails SAFE, never open."""
import os
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_quality_floor.db")

from app import main as m


# ── classifier ───────────────────────────────────────────────────────────────

def test_any_bare_link_is_flagged():
    # One described, one bare — a single naked link is still unacceptable.
    cfg = {"items": [
        {"title": "A", "url": "http://a", "description": "has text"},
        {"title": "B", "url": "http://b"},
    ]}
    assert m._data_card_quality_gap(cfg) == "bare_links"


def test_all_described_is_clean():
    cfg = {"items": [{"title": "A", "url": "http://a", "description": "x"}]}
    assert m._data_card_quality_gap(cfg) == ""


def test_answer_without_sources_flagged():
    assert m._data_card_quality_gap({"answer": "hi", "items": []}) == "no_sources"


# ── the guarantee ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bare_card_gets_synthesized_answer(monkeypatch):
    async def _noop_enrich(enrich, timeout=6.0):
        return None
    async def _synth(prompt, **kw):
        # editor pass returns nothing; overview pass returns an answer
        if "overview" in prompt or "answer" in prompt:
            return {"answer": "Taco Bell reworked its value menu."}
        return None
    monkeypatch.setattr(m, "_enrich_news", _noop_enrich)
    monkeypatch.setattr(m, "fast_llm_json", _synth)

    out = await m._ensure_data_card_quality(
        {"items": [{"title": "Taco Bell news", "url": "http://x"}]},
        query_hint="taco bell")
    assert out.get("answer"), "a link-only card must come back with a summary"


@pytest.mark.asyncio
async def test_fails_safe_when_everything_errors(monkeypatch):
    async def _noop_enrich(enrich, timeout=6.0):
        return None
    async def _dead_llm(prompt, **kw):
        return None  # every model call fails/empties
    monkeypatch.setattr(m, "_enrich_news", _noop_enrich)
    monkeypatch.setattr(m, "fast_llm_json", _dead_llm)

    out = await m._ensure_data_card_quality(
        {"items": [{"title": "Taco Bell news", "url": "http://x"}]},
        query_hint="taco bell")
    # No answer was synthesizable, but the link must still not render naked:
    # its own title is used as a minimal description.
    it = out["items"][0]
    assert it.get("description"), "a bare link must never survive the quality floor"


@pytest.mark.asyncio
async def test_clean_card_is_untouched(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("should not enrich a clean card")
    monkeypatch.setattr(m, "_enrich_news", _boom)
    cfg = {"items": [{"title": "A", "url": "http://a", "description": "already good"}]}
    out = await m._ensure_data_card_quality(cfg, query_hint="a")
    assert out == cfg
