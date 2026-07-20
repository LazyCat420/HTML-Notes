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
