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
import re

from app import main as m

SRC = inspect.getsource(m)


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
