"""Guards for the 2026-07-20 debug wave.

Each test was verified to FAIL before its fix landed. See DEBUG_WAVE_PLAN.md for
the audit that reframed three of the five reported bugs.
"""
import os
import asyncio
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_debug_wave.db")

from app import main as m


# ── Bug 3: deictic location words must never reach a geocoder ───────────────
# Every one of these is ALSO a real place name, so the geocode "succeeds" and
# pins the map thousands of miles away. Measured: Current -> Bahamas,
# here -> Somalia, my location -> Rwanda.

@pytest.mark.parametrize("word", [
    "Current", "current", "current location", "my location", "my current location",
    "here", "near me", "nearby", "around me", "my area", "this area",
    "where i am", "local",
])
def test_deictic_place_words_rejected(word):
    assert m.is_deictic_place(word), f"{word!r} would be geocoded literally"


@pytest.mark.parametrize("place", [
    "Seattle", "New York", "Current River", "Tokyo", "San Jose del Cabo",
])
def test_real_places_still_pass(place):
    assert not m.is_deictic_place(place)


@pytest.mark.parametrize("msg", ["how is the traffic here", "traffic near me",
                                 "traffic in my area"])
def test_extract_place_drops_deictic(msg):
    assert m._extract_directions_place(msg) == ""


def test_extract_place_keeps_real_city():
    assert m._extract_directions_place("how is the traffic in seattle") == "seattle"


def test_router_traffic_with_no_location_prompts_instead_of_nothing(monkeypatch):
    """Bare "how is the traffic" with no saved location: build_traffic_widget
    still returns None (the FAST path relies on that to fall through to a
    travel-time answer card), but the ROUTER used to read None as "build
    nothing" and left the canvas empty. It must ask which city instead."""
    import app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {})
    out = asyncio.run(m.build_router_widget(
        {"type": "traffic", "query": "how is the traffic"}, "s", "how is the traffic"))
    assert out is not None, "router built nothing for a bare traffic ask"
    wtype, _slug, cfg = out
    assert wtype == "data_card"
    assert "where" in (cfg.get("answer", "") + cfg.get("title", "")).lower()


def test_fast_path_traffic_fallback_preserved(monkeypatch):
    """The contract the fast path depends on — do not "fix" this to return a
    config; test_edge_case_fixes::test_traffic_widget_fallbacks_without_tomtom_key
    pins it too."""
    import app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {})
    _wtype, cfg = asyncio.run(m.build_traffic_widget("traffic"))
    assert cfg is None


# ── Bug 1b: adoption must not desynchronize the client's canvas version ─────

def test_adoption_does_not_bump_version():
    """Nothing emits a `component` for an adoption, so a version minted there is
    never learned by the client — every later request then looks stale and the
    user's widget dismissals stop being honored for the rest of the session."""
    sid = "adopt-1"
    v1 = m.set_session_canvas(sid, "<div id='dashboard-grid'>a</div>")
    v2 = m.set_session_canvas(sid, "<div id='dashboard-grid'>b</div>",
                              bump_version=False)
    assert v2 == v1
    assert m.get_session_canvas(sid) == "<div id='dashboard-grid'>b</div>"


def test_real_commit_still_bumps_version():
    sid = "adopt-2"
    v1 = m.set_session_canvas(sid, "<div>a</div>")
    v2 = m.set_session_canvas(sid, "<div>b</div>")
    assert v2 > v1


def test_first_set_always_assigns_a_version():
    """Even with bump_version=False, a session with no version yet must get one
    — otherwise the KeyError path returns a bogus 0 and everything looks stale."""
    sid = "adopt-fresh"
    m._session_canvas_version.pop(sid, None)
    v = m.set_session_canvas(sid, "<div>x</div>", bump_version=False)
    assert v > 0


# ── Bug 4: a turn that answers but commits nothing still shows a card ───────

def test_text_answer_card_config_shape():
    cfg = m._text_answer_card_config("what are the best sandals for hiking?",
                                     "Teva Hurricane and Chaco Z/1 lead most tests.")
    assert cfg["title"]
    assert len(cfg["title"]) <= 60
    assert "Teva" in cfg["answer"]


def test_text_answer_card_titles_from_the_question():
    """The question is short and already scoped; an answer's first line is
    usually a whole sentence."""
    cfg = m._text_answer_card_config("best sandals", "A very long answer " * 40)
    assert cfg["title"] == "best sandals"


def test_text_answer_card_truncates_long_question():
    cfg = m._text_answer_card_config("x" * 300, "answer")
    assert len(cfg["title"]) <= 60
    assert cfg["title"].endswith("...")


@pytest.mark.parametrize("thin", ["...", "Sure!", "", "Let me look", "ok"])
def test_thin_reply_becomes_an_honest_card_not_an_answer(thin):
    """Observed live with the research tools down: the agent streamed 5 chars
    and committed nothing, so the card's body was literally "...". A card that
    looks like a result but says nothing is worse than admitting the miss."""
    cfg = m._text_answer_card_config("best waterproof hiking sandals", thin)
    assert "couldn't put together an answer" in cfg["answer"]
    assert thin.strip() not in cfg["answer"] or len(thin.strip()) < 2
    assert "no result" in cfg["source_note"]


def test_pure_narration_becomes_the_honest_card():
    """Verbatim from a live research turn: 18 successful research calls, then the
    entire reply was narration and not one word of the answer it had gathered."""
    real = ("... ... ... Now I have comprehensive data from three major review "
            "sources. Let me build a data_card with the best waterproof sandals.")
    assert m._strip_agent_narration(real) == ""
    cfg = m._text_answer_card_config("best waterproof sandals", real)
    assert "couldn't put together an answer" in cfg["answer"]
    assert "data_card" not in cfg["answer"]


def test_narration_stripped_but_answer_kept():
    mixed = ("Let me check that. Teva Hurricane XLT2 leads for strap comfort "
             "and Chaco Z/1 wins on arch support overall.")
    out = m._strip_agent_narration(mixed)
    assert out.startswith("Teva")
    assert "Let me" not in out


def test_stripping_never_eats_a_real_answer():
    real = ("Teva Hurricane XLT2 leads for strap comfort, while Chaco Z/1 Classic "
            "wins on arch support. Keen Newport H2 is the most protective.")
    assert m._strip_agent_narration(real) == real


def test_substantial_reply_is_kept_verbatim():
    real = ("Teva Hurricane XLT2 and Chaco Z/1 Classic lead most waterproof "
            "hiking sandal tests, with Keen Newport H2 close behind.")
    cfg = m._text_answer_card_config("best sandals", real)
    assert cfg["answer"] == real
    assert "rendered from the reply" in cfg["source_note"]


def test_text_answer_card_renders_a_real_widget():
    """The whole point is a widget on the canvas — assert it actually builds."""
    from app.widgets.factory import generate_widget_html
    cfg = m._text_answer_card_config(
        "best sandals",
        "Teva Hurricane XLT2 and Chaco Z/1 Classic lead most waterproof tests.")
    html = generate_widget_html("data_card", "answer-test1", cfg)
    assert 'id="answer-test1"' in html
    assert "widget-container" in html
    assert "Teva" in html


def test_unhandled_tool_names_are_not_canvas_tools():
    """Pins the four-name whitelist. If a canvas tool is ever renamed, this fails
    rather than silently becoming a no-op turn."""
    handled = {
        "mcp__lazy-tool-service__canvas_modify_dom",
        "mcp__lazy-tool-service__canvas_add_widget",
        "mcp__lazy-tool-service__create_widget",
        "mcp__lazy-tool-service__update_widget",
    }
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py")).read()
    for name in handled:
        assert name in src, f"{name} vanished — the SSE dispatch would no-op"
    # prism forces these on us; they must NOT be treated as canvas tools
    for forced in ("create_artifact", "execute_python"):
        assert forced not in handled
