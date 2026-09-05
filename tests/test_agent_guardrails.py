"""Guardrails on the agent path: model-supplied image configs and the
follow-up directive target.

Live failures 2026-07-20 these pin:
- "compare birkenstock shoes to other shoes" → image widget showing pasta
  captioned "Classic Birkenstock Arizona two-strap sandal". The injector's
  guard was `not config.get("images")`, so a model-supplied images ARRAY
  bypassed build_image_config, the vision gate, and URL verification entirely.
  Both URLs loaded fine — liveness can't catch a wrong-content pair — so the
  server must re-source, never trust the model's url+caption pairing.
- "tell me more about the deals at costco" edited the SANDALS widget: the
  follow-up directive hard-targeted recency focus_id. Behavior now covered in
  test_followup_targeting.py (SEAM D); the wiring is pinned here.

The injector is inline in the /session/message handler, so these are source
pins: they fail loudly if the guard predicates regress, without needing to
drive the full agent loop.
"""
import inspect
import pytest
import re

from app import main as m

# The agent proxy, its widget injectors and the SYSTEM_PROMPT all moved out of
# app/main.py into app/routes/message.py. `inspect.getsource(m)` still returned
# a real, large string, so these guards kept "running" while asserting against a
# file that no longer contains the code — 18 of them failed outright and the
# rest were checking nothing. See tests/_sources.py.
from tests._sources import MESSAGE_SRC as SRC, SERVER_SRC, LLM_SRC, BUILDERS_SRC


def _image_branch():
    """The injector's image branch source."""
    start = SRC.index('elif widget_type == "image"')
    end = SRC.index('elif (widget_type == "map"', start)
    return SRC[start:end]


def test_image_injector_fires_even_when_model_supplied_images():
    branch = _image_branch()
    assert 'elif widget_type == "image":' in SRC, (
        "the image injector must gate on TYPE alone — `not config.get('images')` "
        "let model-supplied arrays bypass every guardrail")
    assert "not config.get(\"images\")" not in branch.splitlines()[0]


def test_image_injector_resources_and_verifies():
    branch = _image_branch()
    # Re-source from a real search first…
    assert "build_image_config" in branch
    # …fall back to per-URL verification…
    assert "_image_url_loads" in branch
    # …vision-gate whatever survives…
    assert "filter_images_by_relevance" in branch
    # …and captions become the search query when nothing else names the subject.
    assert "model_imgs" in branch and "caption" in branch


def test_image_injector_never_ships_unverified_model_pairs():
    """The success path must REPLACE the model's images key, not merge over it."""
    branch = _image_branch()
    assert re.search(r'k not in \("image_query", "query", "images"\)', branch), (
        "when build_image_config succeeds, the model's images array must be "
        "dropped from the merged config")


def test_system_prompt_forbids_model_image_urls():
    """Prompt-side guardrail: the agent is told it cannot know image URLs."""
    assert "NO image tool" in SRC
    assert "NEVER write 'url' or 'images' entries" in SRC


def test_system_prompt_routes_comparisons_to_data_card():
    assert "COMPARE / contrast" in SRC
    assert "Never answer a comparison with images alone" in SRC


def test_followup_directive_uses_topical_target_not_raw_focus():
    """Both the directive and the message rewrite must flow through
    _followup_target_id — a raw focus_id reference there re-opens the
    edited-the-wrong-widget bug."""
    assert "followup_target = (" in SRC
    assert "_followup_target_id(req.session_id" in SRC
    # The directive text and the rewrite must reference the resolved target…
    directive = SRC[SRC.index("THIS TURN IS A FOLLOW-UP"):]
    assert "{followup_target}" in directive[:400]
    rewrite = SRC[SRC.index("Update the existing widget #"):]
    assert "{followup_target}" in rewrite[:200]
    # …and neither may fall back to the raw recency focus id.
    assert "widget #{turn_ctx['focus_id']}" not in SRC
    assert "widget_id='{turn_ctx['focus_id']}'" not in SRC


def test_directive_and_rewrite_carry_the_widget_anchor():
    """Naming only the id tells the model WHERE, not WHAT ABOUT — the anchor
    text is what resolves 'Miku' against the sushi thread instead of the
    vocaloid. Both the directive and the rewrite must carry it."""
    assert "currently showing:" in SRC       # directive
    assert "It currently shows:" in SRC      # message rewrite
    assert SRC.count("_widget_showing(") >= 3  # helper + both call sites


def test_system_prompt_resolves_names_against_conversation():
    assert "NAMES RESOLVE AGAINST THE CONVERSATION FIRST" in SRC
    assert "BEATS the famous meaning" in SRC


# ── Stacking in-place updates (bounded accumulation, not hard replace) ───────

def _stack(session, wid, cfg, prev=None, on_canvas=True):
    m._session_widget_configs.pop(session, None)
    if prev is not None:
        m._remember_widget_config(session, wid, prev)
    return m._stack_data_card_update(session, wid, "data_card", cfg, on_canvas)


def test_update_stacks_previous_answer_under_new():
    out = _stack("s", "w1", {"answer": "New hardware deals: drills and saws."},
                 prev={"answer": "Costco July deals: tacos, roses, batteries."})
    assert out["answer"].startswith("New hardware deals")
    assert "**Earlier**" in out["answer"]
    assert "tacos" in out["answer"], "the previous content must survive the update"


def test_stacking_respects_the_word_budget():
    prev = {"answer": "old " * 900}
    out = _stack("s", "w1", {"answer": "fresh " * 100}, prev=prev)
    assert m._word_count(out["answer"]) <= m._STACK_WORD_BUDGET + 20  # + rule/ellipsis
    assert out["answer"].rstrip().endswith("…"), "trimmed history is marked"


def test_no_stacking_when_new_answer_fills_the_budget():
    out = _stack("s", "w1", {"answer": "big " * 850}, prev={"answer": "old news"})
    assert "**Earlier**" not in out["answer"], "no room → clean replace"


def test_no_stacking_when_model_already_included_history():
    prev = {"answer": "Costco July deals: tacos, roses, batteries and more items."}
    new = {"answer": "Costco July deals: tacos, roses, batteries and more items. "
                     "Plus new hardware: drills."}
    out = _stack("s", "w1", new, prev=prev)
    assert out["answer"].count("tacos") == 1, "history the model kept must not duplicate"


def test_new_widgets_and_other_types_replace():
    out = _stack("s", "w1", {"answer": "fresh"}, prev={"answer": "old"}, on_canvas=False)
    assert out["answer"] == "fresh", "first render of an id never stacks"
    m._remember_widget_config("s2", "w2", {"answer": "old"})
    out2 = m._stack_data_card_update("s2", "w2", "stock_card", {"answer": "fresh"}, True)
    assert out2["answer"] == "fresh", "stateful widget types always replace"


def test_items_accumulate_and_dedupe():
    prev = {"answer": "old", "items": [{"title": "A", "url": "http://a"},
                                       {"title": "B", "url": "http://b"}]}
    new = {"answer": "brand new content here",
           "items": [{"title": "C", "url": "http://c"},
                     {"title": "A2", "url": "http://a"}]}
    out = _stack("s", "w1", new, prev=prev)
    urls = [i["url"] for i in out["items"]]
    assert urls[0] == "http://c", "newest sources first"
    assert "http://b" in urls, "older sources kept"
    assert urls.count("http://a") == 1, "deduped by url"


def test_stacking_is_wired_into_the_agent_path():
    assert "_stack_data_card_update(" in SRC
    assert "_remember_widget_config(req.session_id, widget_id, config)" in SRC


# ── Multi-ticker comparison ("NVDA vs SPY vs TSM" = ONE chart) ───────────────

def test_compare_ticker_extraction():
    ex = m._extract_compare_tickers
    assert ex("NVDA vs SPY vs TSM") == ["NVDA", "SPY", "TSM"]
    assert ex("compare XLP vs SPY") == ["XLP", "SPY"]
    assert ex("$NVDA versus $AMD chart") == ["NVDA", "AMD"]
    assert ex("NVDA, SPY, and TSM ytd") == ["NVDA", "SPY", "TSM"]
    # Not compare phrasing / no explicit tickers → empty, single path handles.
    assert ex("spy stock chart") == []
    assert "VS" not in ex("AAPL VS MSFT"), "the separator itself is never a ticker"


@pytest.mark.asyncio
async def test_compare_config_normalizes_and_aligns(monkeypatch):
    async def fake_snapshot(sym, range_="1mo"):
        data = {"NVDA": [100, 110, 121], "SPY": [50, 51, 52, 53],
                "BAD": {"is_error": True}}
        v = data[sym]
        if isinstance(v, dict):
            return v
        return {"symbol": sym, "values": v,
                "labels": [f"d{i}" for i in range(len(v))]}
    monkeypatch.setattr(m, "stock_snapshot", fake_snapshot)
    cfg = await m.build_stock_compare_config(["NVDA", "SPY", "BAD"], "6mo")
    ds = cfg["chart"]["data"]["datasets"]
    assert [d["label"].split()[0] for d in ds] == ["NVDA", "SPY"], "failed ticker dropped"
    assert len(cfg["chart"]["data"]["labels"]) == 3, "aligned on the common tail"
    assert all(len(d["data"]) == 3 for d in ds)
    assert ds[0]["data"][0] == 0.0 and ds[0]["data"][-1] == 21.0, "% from first close"
    assert cfg["compare_symbols"] == ["NVDA", "SPY"]
    # Fewer than two survivors → None (caller falls back to single-stock).
    assert await m.build_stock_compare_config(["NVDA", "BAD"], "6mo") is None
    assert await m.build_stock_compare_config(["NVDA"], "6mo") is None


def test_compare_chart_is_never_coerced_to_stock_card():
    wt, cfg = m.coerce_widget_type(
        "chart", "nvda-spy-compare",
        {"compare_symbols": ["NVDA", "SPY"], "title": "NVDA vs SPY"})
    assert wt == "chart", "the compare chart is the one chart a ticker SHOULD get"
    wt2, _ = m.coerce_widget_type(
        "chart", "w",
        {"chart": {"data": {"datasets": [{"data": [1]}, {"data": [2]}]}}})
    assert wt2 == "chart", "any multi-dataset chart must survive coercion"


def test_router_collapses_multiple_stock_specs():
    # The prompt's "never open a second stock" is prose; the collapse is code.
    assert "collapsed" in LLM_SRC and "stock_specs" in LLM_SRC
    idx = LLM_SRC.index("stock_specs = [w for w in clean")
    assert '" vs ".join' in LLM_SRC[idx:idx + 400]


def test_agent_prompt_and_injector_route_comparisons():
    assert "compare_symbols" in SRC
    assert "COMPARE tickers" in SRC, "SYSTEM_PROMPT must teach the compare route"
    assert "Built stock compare" in SRC, "injector must build server-side"


# ── a numeric QUESTION is not a conversion (live misroute 2026-07-31) ────────
#
# "145F chicken breast ... how long to get to 165 ... 25 minutes in the oven at
# 400F" rendered a unit/currency calculator. The console proved it was the AGENT
# path (path=='agent' plus a streamed tool_call), not the router and not the
# deterministic intercept — CONVERT_INTENT_RE does not match that text. So the
# agent picked converter off the prompt alone, and the code-level coercion below
# is what makes the fix hold regardless of what the model decides.

def _converter_coercion_branch():
    """The injector's converter->data_card branch source."""
    start = SRC.index('if widget_type == "converter" and not (')
    end = SRC.index('if widget_type == "chart" and config.get("compare_symbols")', start)
    return SRC[start:end]


def test_system_prompt_converter_requires_an_explicit_calculation():
    assert "CONVERT or CALCULATE — only when the ask IS the arithmetic" in SRC
    assert "NEVER pick converter because the message merely CONTAINS numbers" in SRC


def test_system_prompt_routes_numeric_questions_to_research():
    assert "A QUESTION THAT HAPPENS TO CONTAIN NUMBERS IS NOT A CALCULATION" in SRC
    idx = SRC.index("A QUESTION THAT HAPPENS TO CONTAIN NUMBERS IS NOT A CALCULATION")
    branch = SRC[idx:idx + 700]
    assert "search_query" in branch and "html_notes_web_search" in branch


def test_numeric_question_rule_precedes_the_converter_route():
    """The ROUTING list is scanned top-down and the model takes the first match,
    so an exclusion placed AFTER the converter line arrives too late."""
    assert (SRC.index("A QUESTION THAT HAPPENS TO CONTAIN NUMBERS")
            < SRC.index("widget_type='converter'"))


def test_injector_coerces_a_non_conversion_converter_to_a_research_card():
    branch = _converter_coercion_branch()
    assert "is_conversion_ask" in branch
    assert '"data_card"' in branch and '"search_query"' in branch
    # Without this the widget is right and the SPOKEN line still says converter.
    assert "nonlocal_last_committed" in branch
    # _resolve_agent_widget_id resolved the id AS a converter; reusing it would
    # let the answer card overwrite a real calculator already on the canvas.
    assert "uuid.uuid4()" in branch


def test_converter_coercion_runs_before_the_rehydration_chain():
    """Landing before the chain head is the whole mechanism: the coerced config
    then falls through to the existing data_card+search_query branch, which
    already calls build_answer_config."""
    assert (SRC.index('if widget_type == "converter" and not (')
            < SRC.index('if widget_type == "chart" and config.get("compare_symbols")'))


def test_router_builder_guards_the_converter_type():
    """build_converter_config is pure regex — it never returns None, so it never
    reaches the 'all builds empty -> answer card' degrade, and
    _drop_offsubject_widgets no-ops on a single widget. Nothing catches a wrong
    converter downstream, so the builder has to guard itself."""
    start = BUILDERS_SRC.index('if wtype == "converter":')
    branch = BUILDERS_SRC[start:BUILDERS_SRC.index('if wtype == "reminder":', start)]
    assert "is_conversion_ask" in branch
    assert "build_answer_config" in branch


def test_converter_is_not_deferred_to_the_agent():
    """Pins WHY the builder guard above must exist: a router converter pick is
    built locally and shipped, so the agent never sees it."""
    assert "converter" not in m._AGENT_RESEARCH_TYPES
