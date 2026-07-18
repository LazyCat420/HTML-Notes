"""Follow-up widget reuse (P0 bug ①): a conversational follow-up on the same
thread must UPDATE the open card in place instead of stacking a new one, while a
genuinely new subject still gets its own card."""
import os
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_followup_reuse.db")

from app import main as m


def _canvas(*widgets):
    """Build a canvas HTML from (id, css_class, title) triples."""
    cards = "".join(
        f'<div class="glass-card {cls}" id="{wid}">'
        f'<h3 class="glass-card-title">{title}</h3></div>'
        for wid, cls, title in widgets)
    return f'<div id="dashboard-grid">{cards}</div>'


@pytest.fixture
def canvas(monkeypatch):
    """Patch the session canvas store so find_reuse_target reads our fixture."""
    holder = {"html": ""}
    monkeypatch.setattr(m, "get_session_canvas", lambda _sid: holder["html"])
    return holder


# ── the deixis detector ──────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "what about fifa?",
    "wait...what military health concerns?",
    "what happened with taco bell?",
    "tell me more",
    "and the away team?",
    "why did that happen",
    "its earnings though",
])
def test_followup_phrasing_detected(msg):
    assert m._FOLLOWUP_RE.search(msg)


@pytest.mark.parametrize("msg", [
    "tell me about the news",
    "nvda stock chart",
    "chocolate chip cookie recipe",
    "weather in Tokyo",
])
def test_new_topic_not_flagged_as_followup(msg):
    assert not m._FOLLOWUP_RE.search(msg)


# ── subject overlap ──────────────────────────────────────────────────────────

def test_subject_overlap_matches_shared_topic():
    assert m._subject_overlap("taco bell earnings", "Taco Bell menu shakeup") >= 0.5


def test_subject_overlap_ignores_distinct_topics():
    assert m._subject_overlap("eiffel tower height", "Taco Bell menu") == 0.0


# ── find_reuse_target ────────────────────────────────────────────────────────

def test_deictic_followup_reuses_open_data_card(canvas):
    canvas["html"] = _canvas(("dc-1", "data-card", "Taco Bell menu"))
    rid = m.find_reuse_target("s", "data_card", "what happened with taco bell?")
    assert rid == "dc-1"


def test_fresh_distinct_subject_gets_new_card(canvas):
    canvas["html"] = _canvas(("dc-1", "data-card", "Taco Bell menu"))
    rid = m.find_reuse_target("s", "data_card", "tell me about the eiffel tower")
    assert rid is None


def test_subject_overlap_reuses_without_deixis(canvas):
    canvas["html"] = _canvas(("dc-1", "data-card", "Taco Bell menu"))
    rid = m.find_reuse_target("s", "data_card", "taco bell stock price",
                              subject="Taco Bell earnings")
    assert rid == "dc-1"


def test_weather_is_always_singleton(canvas):
    canvas["html"] = _canvas(("wx-1", "weather-widget", "Tokyo"))
    # Not a follow-up phrasing and a different city — still reuses the one weather.
    rid = m.find_reuse_target("s", "weather", "weather in Paris")
    assert rid == "wx-1"


def test_non_singleton_type_never_reuses(canvas):
    canvas["html"] = _canvas(("yt-1", "", "some video"))
    rid = m.find_reuse_target("s", "youtube_player", "wait play another")
    assert rid is None


def test_most_recent_same_type_wins(canvas):
    canvas["html"] = _canvas(
        ("dc-1", "data-card", "Old topic"),
        ("dc-2", "data-card", "Newer topic"))
    rid = m.find_reuse_target("s", "data_card", "tell me more")
    assert rid == "dc-2"  # DOM order last = most recent


def test_empty_canvas_returns_none(canvas):
    canvas["html"] = ""
    assert m.find_reuse_target("s", "data_card", "what about it?") is None
