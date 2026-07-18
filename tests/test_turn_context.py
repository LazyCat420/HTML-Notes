"""Conversation self-awareness (P1 turn ledger + context bundle, P2 targeting):
every routing tier can see what recent turns built and reuse the right widget."""
import os
import pytest

os.environ.setdefault("DATABASE_URL", "data/test_turn_context.db")

from app import main as m


@pytest.fixture(autouse=True)
def clean_ledger():
    m._session_turn_ledger.clear()
    yield
    m._session_turn_ledger.clear()


@pytest.fixture
def canvas(monkeypatch):
    holder = {"html": ""}
    monkeypatch.setattr(m, "get_session_canvas", lambda _sid: holder["html"])
    return holder


# ── _widget_detail: the content gist ─────────────────────────────────────────

def test_detail_prefers_answer_first_line():
    assert m._widget_detail({"answer": "Taco Bell reworked its menu.\n\nMore."}) \
        == "Taco Bell reworked its menu. More."


def test_detail_falls_back_to_item_titles():
    d = m._widget_detail({"items": [{"title": "A"}, {"title": "B"}, {"title": "C"}, {"title": "D"}]})
    assert d == "A · B · C"  # capped at 3


def test_detail_empty_config():
    assert m._widget_detail({}) == ""
    assert m._widget_detail(None) == ""


# ── record_turn + the ledger ─────────────────────────────────────────────────

def test_record_and_cap():
    for i in range(m._LEDGER_MAX_TURNS + 5):
        m.record_turn("s", f"msg {i}", "router", [(f"w{i}", "data_card", "x", "")])
    led = m._session_turn_ledger["s"]
    assert len(led) == m._LEDGER_MAX_TURNS  # oldest trimmed
    assert led[-1]["message"] == f"msg {m._LEDGER_MAX_TURNS + 4}"


def test_record_skips_widgetless_entries_but_keeps_turn():
    m.record_turn("s", "close everything", "clear", [])
    assert m._session_turn_ledger["s"][-1]["widgets"] == []


def test_record_no_session_is_noop():
    m.record_turn("", "x", "router", [("w", "data_card", "s", "d")])
    assert "" not in m._session_turn_ledger


# ── build_turn_context ───────────────────────────────────────────────────────

def test_context_block_lists_recent_turns_and_focus(canvas):
    canvas["html"] = ('<div id="dashboard-grid">'
                      '<div class="glass-card data-card" id="dc-1"><h3>News</h3></div></div>')
    m.record_turn("s", "tell me about the news", "router",
                  [("dc-1", "data_card", "the news", "Testosterone plan · TRICARE")])
    ctx = m.build_turn_context("s")
    assert "RECENT TURNS" in ctx["context_block"]
    assert "tell me about the news" in ctx["context_block"]
    assert "Testosterone plan" in ctx["context_block"]  # the content gist is present
    assert "CURRENT CANVAS:" in ctx["context_block"]
    assert "#dc-1" in ctx["context_block"]
    assert ctx["focus_id"] == "dc-1"


def test_context_empty_session(canvas):
    canvas["html"] = ""
    ctx = m.build_turn_context("s")
    assert ctx["focus_id"] is None
    assert "CURRENT CANVAS:" in ctx["context_block"]


# ── P2: model-driven target resolution ───────────────────────────────────────

def test_valid_same_type_target_is_honored(canvas):
    canvas["html"] = ('<div id="dashboard-grid">'
                      '<div class="glass-card data-card" id="dc-1"><h3>News</h3></div></div>')
    assert m._resolve_widget_target("s", "data_card", "dc-1", "what about taco bell?") == "dc-1"
    # a leading '#' from the model is tolerated
    assert m._resolve_widget_target("s", "data_card", "#dc-1", "more") == "dc-1"


def test_target_of_wrong_type_falls_back(canvas):
    canvas["html"] = ('<div id="dashboard-grid">'
                      '<div class="glass-card data-card" id="dc-1"><h3>News</h3></div></div>')
    # model named a data_card id but we're building a stock_card — don't clobber it,
    # and there's no stock_card to reuse, so mint fresh (None).
    assert m._resolve_widget_target("s", "stock_card", "dc-1", "fresh subject") is None


def test_nonexistent_target_falls_back_to_deterministic(canvas):
    canvas["html"] = ('<div id="dashboard-grid">'
                      '<div class="glass-card data-card" id="dc-1"><h3>News</h3></div></div>')
    # ghost id → ignored; but the message is a follow-up so deterministic reuse
    # still lands on the open data_card.
    assert m._resolve_widget_target("s", "data_card", "ghost-99", "tell me more") == "dc-1"


def test_no_target_uses_deterministic_reuse(canvas):
    canvas["html"] = ('<div id="dashboard-grid">'
                      '<div class="glass-card data-card" id="dc-1"><h3>News</h3></div></div>')
    assert m._resolve_widget_target("s", "data_card", None, "what happened next?") == "dc-1"
