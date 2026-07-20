"""Follow-up → widget TARGETING: which id does a follow-up render into?

The bug class this pins: a follow-up either stacks a duplicate or silently edits
the WRONG (newest) widget. Reconcile and the update mechanism were never at
fault — target SELECTION was.

Every test here was verified to FAIL before the fix landed. See
FOLLOWUP_TARGETING_PLAN.md for the measurements that drove the design.
"""
import os
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_followup_targeting.db")

from app import main as m


def _canvas(*widgets):
    """(id, css_class, title) triples -> canvas HTML, DOM order = add order."""
    cards = "".join(
        f'<div class="glass-card {cls}" id="{wid}">'
        f'<h3 class="glass-card-title">{title}</h3></div>'
        for wid, cls, title in widgets)
    return f'<div id="dashboard-grid">{cards}</div>'


@pytest.fixture
def canvas(monkeypatch):
    holder = {"html": ""}
    monkeypatch.setattr(m, "get_session_canvas", lambda _sid: holder["html"])
    return holder


@pytest.fixture(autouse=True)
def _clean_ledger():
    m._session_turn_ledger.clear()
    yield
    m._session_turn_ledger.clear()


def _ledger(sid, *turns):
    """turns = (message, widget_id, widget_type, detail)."""
    m._session_turn_ledger[sid] = [
        {"message": msg, "route": "test",
         "widgets": [{"id": wid, "type": wt, "subject": "", "detail": det}]}
        for msg, wid, wt, det in turns]


# ── SEAM A: the agent tier must validate the model's widget_id ───────────────

def test_agent_ghost_id_falls_back_to_real_widget(canvas):
    """A hallucinated id must NOT mint a new widget when a real reuse target
    exists. This is the tier-3 equivalent of _resolve_widget_target."""
    canvas["html"] = _canvas(("answer-3f2a91b0", "data-card", "Best Sandals"))
    got = m._resolve_agent_widget_id(
        "s", "data_card", "news-card", "tell me more", "")
    assert got == "answer-3f2a91b0"


def test_agent_real_id_is_honoured(canvas):
    canvas["html"] = _canvas(("answer-3f2a91b0", "data-card", "Best Sandals"))
    got = m._resolve_agent_widget_id(
        "s", "data_card", "answer-3f2a91b0", "cheaper ones", "")
    assert got == "answer-3f2a91b0"


def test_agent_new_subject_mints_fresh_id(canvas):
    """No reuse signal → a fresh id, not a clobber of the open card."""
    canvas["html"] = _canvas(("answer-3f2a91b0", "data-card", "Best Sandals"))
    got = m._resolve_agent_widget_id(
        "s", "data_card", "", "tell me about volcanoes in iceland", "")
    assert got is not None
    assert got != "answer-3f2a91b0"


def test_agent_keeps_its_own_id_for_a_genuinely_new_widget(canvas):
    """With nothing to reuse, the model's id must be HONOURED, not replaced.
    Overriding it here renamed every first-of-its-kind widget — caught by
    test_sse_duplication::test_sse_no_duplicate_widget."""
    canvas["html"] = '<div id="dashboard-grid"></div>'
    got = m._resolve_agent_widget_id(
        "s", "mini_music_player", "widget-music-player-1", "play some jazz", "")
    assert got == "widget-music-player-1"


def test_agent_id_is_stable_not_random_per_call(canvas):
    """Two identical follow-ups must resolve to the SAME widget, not two."""
    canvas["html"] = _canvas(("answer-3f2a91b0", "data-card", "Best Sandals"))
    a = m._resolve_agent_widget_id("s", "data_card", "", "tell me more", "")
    b = m._resolve_agent_widget_id("s", "data_card", "", "tell me more", "")
    assert a == b == "answer-3f2a91b0"


# ── SEAM B: rank by topical score; recency is only the tiebreaker ────────────

def test_followup_targets_the_topical_card_not_the_newest(canvas):
    """Two open cards, follow-up clearly about the OLDER one. 'last match wins'
    picked sourdough; scoring must pick sandals."""
    canvas["html"] = _canvas(
        ("dc-sandals", "data-card", "Best Waterproof Sandals"),
        ("dc-bread", "data-card", "Sourdough Starter Guide"),
    )
    assert m.find_reuse_target(
        "s", "data_card", "what about cheaper sandals?") == "dc-sandals"


def test_followup_matches_via_ledger_detail(canvas):
    """The subject may only appear in the card BODY, never in its title.
    _widget_detail records it; scoring must consume it."""
    canvas["html"] = _canvas(("dc-1", "data-card", "Best Sandals"))
    _ledger("s", ("best sandals", "dc-1", "data_card",
                  "Teva Hurricane, Chaco Z/1, Keen Newport"))
    assert m.find_reuse_target("s", "data_card", "show me teva instead") == "dc-1"


def test_detail_disambiguates_between_two_cards(canvas):
    canvas["html"] = _canvas(
        ("dc-sandals", "data-card", "Best Sandals"),
        ("dc-bread", "data-card", "Sourdough Guide"),
    )
    _ledger("s",
            ("best sandals", "dc-sandals", "data_card", "Teva, Chaco, Keen"),
            ("sourdough", "dc-bread", "data_card", "levain, hydration, banneton"))
    assert m.find_reuse_target("s", "data_card", "what hydration ratio?") == "dc-bread"
    assert m.find_reuse_target("s", "data_card", "is teva any good?") == "dc-sandals"


def test_ambiguous_refinement_falls_back_to_recency(canvas):
    """'under $50' scores 0 against both titles — genuinely ambiguous from text.
    Recency is the RIGHT answer here; it just must not be the DEFAULT."""
    canvas["html"] = _canvas(
        ("dc-a", "data-card", "Best Sandals"),
        ("dc-b", "data-card", "Best Hiking Boots"),
    )
    assert m.find_reuse_target("s", "data_card", "only under $50") == "dc-b"


def test_new_subject_still_gets_its_own_card(canvas):
    """The regression this whole mechanism must not cause."""
    canvas["html"] = _canvas(("dc-sandals", "data-card", "Best Waterproof Sandals"))
    assert m.find_reuse_target(
        "s", "data_card", "tell me about volcanoes in iceland") is None


# ── SEAM C: focus_id must survive ledger loss (restart) ─────────────────────

def test_focus_id_recovers_from_canvas_when_ledger_empty(canvas):
    """Canvas survives a restart (client resends current_canvas); the in-memory
    ledger does not. focus_id going None silently disabled the directive AND the
    message rewrite, so follow-ups behaved differently before/after a restart."""
    html = _canvas(("dc-1", "data-card", "Best Sandals"),
                   ("dc-2", "data-card", "Sourdough Guide"))
    canvas["html"] = html
    ctx = m.build_turn_context("fresh-session", current_canvas=html)
    assert ctx["focus_id"] == "dc-2"


def test_ledger_focus_wins_over_canvas_order(canvas):
    """When the ledger IS present it is the better signal — it knows what the
    last turn actually produced, which canvas DOM order can't express."""
    canvas["html"] = _canvas(("dc-1", "data-card", "Best Sandals"),
                             ("dc-2", "data-card", "Sourdough Guide"))
    _ledger("s", ("sandals", "dc-1", "data_card", "Teva"))
    assert m.build_turn_context("s")["focus_id"] == "dc-1"


# ── SEAM D: refinements that are not sentence-openers ───────────────────────

@pytest.mark.parametrize("msg", [
    "waterproof only please",     # _REFINE_RE is ^-anchored -> missed
    "under $50",                  # no opener, no pronoun
    "cheaper ones",
])
def test_non_opener_refinements_reuse(canvas, msg):
    canvas["html"] = _canvas(("dc-1", "data-card", "Best Waterproof Sandals"))
    _ledger("s", ("best sandals", "dc-1", "data_card",
                  "waterproof picks under $100"))
    assert m.find_reuse_target("s", "data_card", msg) == "dc-1"


# ── SEAM E: reuse must not be limited to 5 of 14 widget types ───────────────

@pytest.mark.parametrize("wtype,cls", [
    ("products", "products-card"),
    ("chart", "chart-card"),
    ("checklist", "checklist-card"),
])
def test_refinable_types_are_reuse_eligible(canvas, wtype, cls):
    """products/chart/checklist are exactly what users refine, and could never
    be updated in place under the old 5-type allowlist."""
    assert wtype in m.REUSABLE_WIDGET_TYPES


def test_media_types_are_not_reuse_eligible():
    """Media swap via _place_media_widget; clock/notes are user-owned. These
    must stay OUT or a follow-up would clobber them."""
    for wtype in ("mini_music_player", "youtube_player", "clock", "notes"):
        assert wtype not in m.REUSABLE_WIDGET_TYPES


# ── SEAM F: truncation must drop history, not widget ids ────────────────────

def test_canvas_inventory_precedes_ledger_in_context(canvas):
    """The router truncates context to 1200 chars. CURRENT CANVAS was appended
    LAST, so the id list was the first thing cut — while the prompt still said
    'never invent a widget id'."""
    canvas["html"] = _canvas(("dc-1", "data-card", "Best Sandals"))
    _ledger("s", *[(f"msg {i}", f"dc-{i}", "data_card", "x" * 150)
                   for i in range(6)])
    block = m.build_turn_context("s")["context_block"]
    assert block.index("CURRENT CANVAS") < block.index("RECENT TURNS")
    assert "#dc-1" in block[:1200]


# ── PHASE 2: an explicit client focus signal outranks all inference ─────────

def test_explicit_focus_id_wins_over_scoring(canvas):
    """When the client tells us which widget the question came from, that is a
    FACT — it must beat topical scoring, recency, and the model's guess."""
    canvas["html"] = _canvas(
        ("dc-sandals", "data-card", "Best Waterproof Sandals"),
        ("dc-bread", "data-card", "Sourdough Starter Guide"),
    )
    got = m._resolve_widget_target(
        "s", "data_card", "", "what about cheaper sandals?",
        focus_widget_id="dc-bread")
    assert got == "dc-bread"


def test_stale_explicit_focus_id_is_ignored(canvas):
    """A focus id for a widget that has since been dismissed must not win."""
    canvas["html"] = _canvas(("dc-sandals", "data-card", "Best Sandals"))
    got = m._resolve_widget_target(
        "s", "data_card", "", "what about cheaper ones?",
        focus_widget_id="dc-dismissed")
    assert got == "dc-sandals"


def test_message_request_accepts_focus_widget_id():
    """The field must be optional — old clients and the voice path omit it."""
    r = m.MessageRequest(session_id="s", message="hi")
    assert r.focus_widget_id is None
    r2 = m.MessageRequest(session_id="s", message="hi", focus_widget_id="dc-1")
    assert r2.focus_widget_id == "dc-1"


def test_focus_id_of_wrong_type_is_ignored(canvas):
    canvas["html"] = _canvas(("dc-1", "data-card", "Best Sandals"),
                             ("wx-1", "weather-card", "Weather in Tokyo"))
    got = m._resolve_widget_target(
        "s", "data_card", "", "cheaper ones", focus_widget_id="wx-1")
    assert got == "dc-1"


# ── SEAM D: the follow-up DIRECTIVE must target topically, not by recency ────
# Live failure 2026-07-20: costco-deals card built, then a Birkenstock card
# (newest). "tell me more about the deals at costco anything hardware related?"
# tripped the "tell me more" trigger, and the directive/rewrite hard-targeted
# focus_id — pure recency — ordering an in-place rewrite of the SANDALS card.
# find_reuse_target would have scored costco correctly but never ran: the
# directive pre-empted it. _followup_target_id closes that seam.

def test_followup_directive_targets_topical_card_not_newest(canvas):
    canvas["html"] = _canvas(
        ("answer-costco11", "data-card", "Costco Concord Deals"),
        ("answer-sandal22", "data-card", "Birkenstock Arizona"),  # newest
    )
    _ledger("s",
            ("what deals are at the costco in concord?",
             "answer-costco11", "data_card", "costco concord deals kirkland"),
            ("find me more info on birkenstock arizona",
             "answer-sandal22", "data_card", "birkenstock arizona sandal cork"))
    got = m._followup_target_id(
        "s", "answer-sandal22",
        "tell me more about the deals at costco anything hardware related?")
    assert got == "answer-costco11", (
        "a follow-up that NAMES a subject must edit the widget about that "
        "subject, not whatever was built last")


def test_subjectless_deictic_followup_keeps_recency_focus(canvas):
    """'tell me more' with no subject carries no topical signal — recency is
    the right call there, and must be preserved."""
    canvas["html"] = _canvas(
        ("answer-costco11", "data-card", "Costco Concord Deals"),
        ("answer-sandal22", "data-card", "Birkenstock Arizona"),
    )
    _ledger("s",
            ("costco deals", "answer-costco11", "data_card", "costco deals"),
            ("birkenstock arizona", "answer-sandal22", "data_card", "birkenstock"))
    got = m._followup_target_id("s", "answer-sandal22", "tell me more")
    assert got == "answer-sandal22"


def test_followup_target_scores_ledger_detail_not_just_title(canvas):
    """The subject often lives in the card BODY, recorded as the ledger gist —
    a generic title must not blind the topical override."""
    canvas["html"] = _canvas(
        ("answer-aaaa1111", "data-card", "Search Results"),   # generic title
        ("answer-bbbb2222", "data-card", "Birkenstock Arizona"),
    )
    _ledger("s",
            ("costco deals", "answer-aaaa1111", "data_card",
             "costco concord deals hardware tools"),
            ("birkenstock", "answer-bbbb2222", "data_card", "birkenstock sandal"))
    got = m._followup_target_id(
        "s", "answer-bbbb2222", "what about the costco hardware deals?")
    assert got == "answer-aaaa1111"


def test_followup_target_with_empty_canvas_returns_focus(canvas):
    canvas["html"] = ""
    assert m._followup_target_id("s", None, "tell me more") is None
    assert m._followup_target_id("s", "w-1", "tell me more") == "w-1"
