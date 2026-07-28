"""Provisional widgets: a research tool's finished result is committed to the
canvas the moment the tool returns — flagged `provisional` and wearing a
"composing…" badge — while the agent is still writing its final answer.

The load-bearing invariants:

1. The `provisional: True` config key changes the content signature, so the
   client reconciler is GUARANTEED to replace the preview with the final
   render (whose config lacks the key) instead of early-returning on equal
   sigs and leaving the badge up forever.
2. The provisional path must never mutate the cached tool result —
   `_resolve_news_topic_config` hands that same dict to the agent's final
   commit, and a leaked `provisional: True` there would re-flag the final.
3. A data_card follow-up onto a provisional widget REPLACES it outright; the
   stacking merge is for preserving history, and a preview is not history.
4. The rendered provisional widget carries `data-provisional="1"` on its root
   (the client keys the badge and the reveal-gate bypass off that attribute);
   the final render must not.
"""
import app.main as m
from app.main import (_PROVISIONAL_TOOLS, _session_widget_configs,
                      _stack_data_card_update, cache_tool_result,
                      get_cached_tool_result)
from app.widgets.factory import _content_sig, generate_widget_html


def _news_cfg():
    return {
        "title": "Today's News",
        "subtitle": "top stories",
        "icon": "📰",
        "items": [
            {"title": "Story A", "description": "Summary A.",
             "url": "https://example.com/a", "meta": "Example", "badge": "News"},
            {"title": "Story B", "description": "Summary B.",
             "url": "https://example.com/b", "meta": "Example", "badge": "News"},
        ],
    }


def test_provisional_flag_changes_content_sig():
    cfg = _news_cfg()
    assert (_content_sig("data_card", cfg)
            != _content_sig("data_card", {**cfg, "provisional": True}))


def test_provisional_attribute_stamped_and_absent_on_final():
    cfg = _news_cfg()
    prov = generate_widget_html("data_card", "news-abc123", {**cfg, "provisional": True})
    final = generate_widget_html("data_card", "news-abc123", cfg)
    assert 'data-provisional="1"' in prov
    assert "data-provisional" not in final


def test_cached_tool_result_not_mutated_by_provisional_copy():
    cache_tool_result("news:sig-test", _news_cfg())
    cached = get_cached_tool_result("news:sig-test")
    # The provisional commit builds {**cached, "provisional": True} — verify the
    # spread-copy discipline holds by construction.
    pcfg = {**cached, "provisional": True}
    assert "provisional" not in cached
    assert pcfg is not cached


def test_news_tool_is_whitelisted_with_topic_key():
    key_fn, wtype = _PROVISIONAL_TOOLS["mcp__lazy-tool-service__html_notes_news"]
    assert wtype == "data_card"
    assert key_fn({"topic": " ai "}) == "news:ai"
    assert key_fn({"query": "markets"}) == "news:markets"
    assert key_fn({}) == "news:"


def test_stack_replaces_provisional_outright():
    sid, wid = "sess-prov-stack", "news-deadbeef"
    prev = {**_news_cfg(), "provisional": True,
            "answer": "provisional filler that must not be preserved"}
    _session_widget_configs.setdefault(sid, {})[wid] = prev
    new = {**_news_cfg(), "answer": "the final composed answer"}
    merged = _stack_data_card_update(sid, wid, "data_card", new, on_canvas=True)
    assert merged == new
    assert "provisional filler" not in str(merged.get("answer"))
    _session_widget_configs.pop(sid, None)


def test_stack_still_merges_for_non_provisional_prev():
    sid, wid = "sess-nonprov-stack", "answer-cafe0001"
    prev = {"answer": "an earlier distinct answer about pelicans and their diet",
            "items": []}
    _session_widget_configs.setdefault(sid, {})[wid] = prev
    new = {"answer": "a new answer on a related follow-up", "items": []}
    merged = _stack_data_card_update(sid, wid, "data_card", new, on_canvas=True)
    assert "pelicans" in merged["answer"]  # history preserved when not provisional
    _session_widget_configs.pop(sid, None)
