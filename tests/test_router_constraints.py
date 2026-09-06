"""The LLM router is constrained in code, because prose cannot outvote sampling.

Observed live 2026-09-06: "stock market for the day please" came back from the
router as THREE widgets — ['stock_trending', 'news', 'stock'] — and the
stock_trending one charted NKE, VST, SCHD, KO and ETN from Yahoo's trending
feed ("unscoped most-viewed noise", per its own docstring) for an ask that
never mentioned trending stocks. The router's prompt says "a BROAD or rich ask
should COMPOSE… Max 4", and with a finance canvas as context it applied that to
a greeting.
"""
import pytest

from app import main as m
from app import config_builders as cb


def _plan(*types):
    async def fake(instruction, max_tokens=400):
        return {"widgets": [{"type": t, "query": "" if t in ("news", "stock_news") else "x"}
                            for t in types],
                "reason": "test", "checks": {}}
    return fake


@pytest.mark.asyncio
async def test_three_widget_plan_collapses_to_one_for_a_plain_ask(patch_server):
    patch_server("fast_llm_json", _plan("stock_trending", "news", "stock"))
    plan = await m.route_with_llm("stock market for the day please", "")
    types = [w["type"] for w in plan["widgets"]]
    assert "stock_trending" not in types, "no trending word in the ask"
    assert len(types) == 1, f"composition must be suppressed for a plain ask: {types}"


@pytest.mark.asyncio
async def test_explicitly_broad_ask_may_compose(patch_server):
    patch_server("fast_llm_json", _plan("weather", "map", "news"))
    plan = await m.route_with_llm("plan my saturday in seattle", "")
    assert [w["type"] for w in plan["widgets"]] == ["weather", "map", "news"]


@pytest.mark.asyncio
async def test_conjunction_of_two_named_intents_may_compose(patch_server):
    patch_server("fast_llm_json", _plan("weather", "map"))
    plan = await m.route_with_llm("weather in tokyo and a map of shibuya", "")
    assert len(plan["widgets"]) == 2


@pytest.mark.asyncio
async def test_stock_trending_survives_only_with_a_trending_word(patch_server):
    patch_server("fast_llm_json", _plan("stock_trending"))
    kept = await m.route_with_llm("top gainers today", "")
    assert [w["type"] for w in kept["widgets"]] == ["stock_trending"]

    # Same plan for a general-market ask: trending is dropped and, since the
    # ask names the market, it becomes a market-news card rather than nothing.
    swapped = await m.route_with_llm("how is the market doing", "")
    assert [w["type"] for w in swapped["widgets"]] == ["stock_news"]
    assert swapped["widgets"][0]["query"] == ""


@pytest.mark.asyncio
async def test_router_honours_an_explicit_empty_query(patch_server):
    """`query or message` used to hand the raw "hello" to the builder, which
    grounded it into "hello (greeting)". An empty query means top stories."""
    seen = {}

    async def fake_card(message, *, finance=False, general=None, depth="card", subject_hint=""):
        seen.update(message=message, finance=finance, general=general)
        return {"title": "News: Top Stories", "answer": "x", "subtitle": "1 stories", "items": [{"title": "t"}]}

    async def boom(message):
        raise AssertionError("ground_query ran for an explicit empty query")

    patch_server("build_news_card", fake_card)
    patch_server("ground_query", boom)
    out = await cb.build_router_widget({"type": "news", "query": "", "modifiers": {}}, "sid", "hello")
    assert out and out[1] == "news"
    assert seen["general"] is True, "an explicit empty query must mean general/top stories"
    out = await cb.build_router_widget({"type": "stock_news", "query": "", "modifiers": {}}, "sid", "hello")
    assert seen["finance"] is True and seen["general"] is True


@pytest.mark.asyncio
async def test_router_widget_refuses_trending_without_the_word(patch_server):
    async def boom(*a, **k):
        raise AssertionError("trending builder ran without a trending word")
    patch_server("build_trending_compare_config", boom)
    out = await cb.build_router_widget({"type": "stock_trending", "query": "x", "modifiers": {}},
                                       "sid", "stock market for the day please")
    assert out is None


@pytest.mark.parametrize("text,allowed", [
    ("stock market for the day please", False),
    ("hello", False),
    ("news about nvidia earnings", False),
    ("plan my saturday in seattle", True),
    ("give me a rundown of the market", True),
    ("weather in tokyo and a map of shibuya", True),
    ("tesla stock and news", True),
    ("weather and also the time", False),   # one named intent + a clock word the axes do not count
])
def test_composition_allowed_rule(text, allowed):
    assert m.composition_allowed(text) is allowed, text


def test_use_lazy_agent_default_and_comments_agree():
    """Two comments claimed prism mode (use_lazy_agent=False) was the default.
    It has been True since 2026-08-16; the cascade runs for every browser turn."""
    import pathlib
    from tests._sources import MESSAGE_SRC
    assert m.MessageRequest.model_fields["use_lazy_agent"].default is True
    assert "PRISM MODE (default)" not in MESSAGE_SRC
    js = (pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "index.js").read_text()
    assert "defaults it to False = PRISM MODE" not in js
