"""Intent grounding + vision relevance gate — the "sandals -> Grand Canyon /
Sandals-Resort promo / stray stock-market card" fix.

Two guarantees under test:
  1. The gate actually DROPS off-subject content (images the vision model rejects,
     widgets the consistency pass flags).
  2. It FAILS OPEN everywhere — any LLM/network outage degrades to the old
     behaviour, never an empty canvas.
"""
import os
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_relevance_gate.db")

from app import main as m


@pytest.fixture(autouse=True)
def _clear_ground_cache():
    m._GROUND_CACHE.clear()
    yield
    m._GROUND_CACHE.clear()


# ── ground_query ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ground_query_parses_intent(monkeypatch):
    async def fake(instruction, max_tokens=400):
        return {"subject": "sandals (footwear)", "intent": "shopping",
                "retrieval_query": "best sandals to buy footwear",
                "hyde": "a product photo of sandals",
                "negatives": ["Sandals Resorts vacation", "beach scenery"],
                "ambiguous": False, "clarify": ""}
    monkeypatch.setattr(m, "fast_llm_json", fake)
    g = await m.ground_query("sandals")
    assert g["intent"] == "shopping"
    assert "footwear" in g["retrieval_query"]
    assert any("Resort" in n for n in g["negatives"])


@pytest.mark.asyncio
async def test_ground_query_fails_open(monkeypatch):
    async def fake(instruction, max_tokens=400):
        return None                       # model outage
    monkeypatch.setattr(m, "fast_llm_json", fake)
    g = await m.ground_query("sandals")
    assert g["subject"] == "sandals"      # falls back to the raw message
    assert g["retrieval_query"] == "sandals"
    assert g["negatives"] == []


@pytest.mark.asyncio
async def test_ground_query_caches(monkeypatch):
    calls = {"n": 0}
    async def fake(instruction, max_tokens=400):
        calls["n"] += 1
        return {"subject": "x", "intent": "other", "retrieval_query": "x",
                "hyde": "", "negatives": [], "ambiguous": False, "clarify": ""}
    monkeypatch.setattr(m, "fast_llm_json", fake)
    await m.ground_query("dogs")
    await m.ground_query("dogs")
    assert calls["n"] == 1                # second call served from cache


# ── filter_images_by_relevance (the vision gate) ─────────────────────────────

@pytest.mark.asyncio
async def test_gate_drops_rejected_images(monkeypatch):
    async def fake_fetch(client, url, max_bytes=1_800_000):
        return "data:image/jpeg;base64,AAAA"     # every image "fetches"
    async def fake_vision(content, max_tokens=300, temperature=0.1):
        return {"keep": [0, 2]}           # keep 0 & 2, drop the canyon at 1
    monkeypatch.setattr(m, "_fetch_image_data_url", fake_fetch)
    monkeypatch.setattr(m, "_fast_multimodal_json", fake_vision)
    items = [{"image": "http://a", "caption": "sandal"},
             {"image": "http://b", "caption": "grand canyon"},
             {"image": "http://c", "caption": "sandal 2"}]
    kept = await m.filter_images_by_relevance("sandals", ["scenery"], items, keep=4)
    assert [it["caption"] for it in kept] == ["sandal", "sandal 2"]


@pytest.mark.asyncio
async def test_gate_fails_open_when_no_image_fetches(monkeypatch):
    async def fake_fetch(client, url, max_bytes=1_800_000):
        return None                       # server can't hotlink any image
    async def fake_vision(content, max_tokens=300, temperature=0.1):
        raise AssertionError("vision must not run when nothing fetched")
    monkeypatch.setattr(m, "_fetch_image_data_url", fake_fetch)
    monkeypatch.setattr(m, "_fast_multimodal_json", fake_vision)
    items = [{"image": "http://a", "caption": "x"}]
    kept = await m.filter_images_by_relevance("sandals", [], items)
    assert kept == items                  # unchanged (fail open)


@pytest.mark.asyncio
async def test_gate_fails_open_on_vision_outage(monkeypatch):
    async def fake_fetch(client, url, max_bytes=1_800_000):
        return "data:image/jpeg;base64,AAAA"
    async def fake_vision(content, max_tokens=300, temperature=0.1):
        return None                       # vision model down
    monkeypatch.setattr(m, "_fetch_image_data_url", fake_fetch)
    monkeypatch.setattr(m, "_fast_multimodal_json", fake_vision)
    items = [{"image": "http://a", "caption": "x"}, {"image": "http://b", "caption": "y"}]
    assert await m.filter_images_by_relevance("z", [], items) == items


@pytest.mark.asyncio
async def test_gate_min_keep_prevents_empty_grid(monkeypatch):
    async def fake_fetch(client, url, max_bytes=1_800_000):
        return "data:image/jpeg;base64,AAAA"
    async def fake_vision(content, max_tokens=300, temperature=0.1):
        return {"keep": []}               # over-strict: rejects everything
    monkeypatch.setattr(m, "_fetch_image_data_url", fake_fetch)
    monkeypatch.setattr(m, "_fast_multimodal_json", fake_vision)
    items = [{"image": "http://a", "caption": "x"}, {"image": "http://b", "caption": "y"}]
    kept = await m.filter_images_by_relevance("z", [], items, min_keep=3)
    assert kept == items                  # min_keep guard keeps the original set


@pytest.mark.asyncio
async def test_gate_noop_without_images():
    items = [{"caption": "x"}, {"caption": "y"}]      # no 'image' keys
    assert await m.filter_images_by_relevance("z", [], items) == items


# ── _drop_offsubject_widgets (cross-widget consistency) ──────────────────────

@pytest.mark.asyncio
async def test_consistency_skips_single_widget(monkeypatch):
    async def fake(*a, **k):
        raise AssertionError("must not call the LLM for a single widget")
    monkeypatch.setattr(m, "fast_llm_json", fake)
    good = [("image", "image", {"title": "Sandals"}, None)]
    assert await m._drop_offsubject_widgets("sandals", good) == good


@pytest.mark.asyncio
async def test_consistency_drops_offsubject_widget(monkeypatch):
    async def fake(instruction, max_tokens=120):
        return {"keep": [0]}              # keep the sandals grid, drop the stock card
    monkeypatch.setattr(m, "fast_llm_json", fake)
    good = [("products", "products", {"title": "Best Sandals"}, None),
            ("data_card", "stock-news", {"title": "Stock Market"}, None)]
    kept = await m._drop_offsubject_widgets("sandals", good)
    assert len(kept) == 1 and kept[0][2]["title"] == "Best Sandals"


@pytest.mark.asyncio
async def test_consistency_fails_open_when_all_dropped(monkeypatch):
    async def fake(instruction, max_tokens=120):
        return {"keep": []}              # model dropped everything
    monkeypatch.setattr(m, "fast_llm_json", fake)
    good = [("a", "a", {"title": "x"}, None), ("b", "b", {"title": "y"}, None)]
    assert await m._drop_offsubject_widgets("q", good) == good


@pytest.mark.asyncio
async def test_consistency_fails_open_on_model_error(monkeypatch):
    async def fake(instruction, max_tokens=120):
        return None
    monkeypatch.setattr(m, "fast_llm_json", fake)
    good = [("a", "a", {"title": "x"}, None), ("b", "b", {"title": "y"}, None)]
    assert await m._drop_offsubject_widgets("q", good) == good


# ── build_image_config end-to-end (grounding + gate wired) ───────────────────

@pytest.mark.asyncio
async def test_build_image_config_searches_expanded_query_and_gates(monkeypatch):
    seen = {}
    async def fake_ground(msg):
        return {"subject": "sandals footwear", "intent": "shopping",
                "retrieval_query": "best sandals footwear", "hyde": "",
                "negatives": ["resort"], "ambiguous": False, "clarify": ""}
    async def fake_search(q, limit=6):
        seen["q"] = q
        return [{"url": "http://a", "title": "Sandal", "snippet": ""},
                {"url": "http://b", "title": "Canyon", "snippet": ""}]
    async def fake_enrich(items, timeout=5.0):
        for it in items:
            it["image"] = "http://img/" + it["url"][-1]
    async def fake_gate(subject, negatives, cands, keep=4, hyde="", min_keep=0):
        return [c for c in cands if "Sandal" in c["caption"]]   # keep only the sandal
    monkeypatch.setattr(m, "ground_query", fake_ground)
    monkeypatch.setattr(m, "web_search", fake_search)
    monkeypatch.setattr(m, "_enrich_news", fake_enrich)
    monkeypatch.setattr(m, "filter_images_by_relevance", fake_gate)
    cfg = await m.build_image_config("sandals")
    assert seen["q"] == "best sandals footwear"     # searched the EXPANDED query
    assert cfg is not None
    assert len(cfg["images"]) == 1                   # canyon dropped by the gate
    assert cfg["title"] == "Sandals Footwear"
