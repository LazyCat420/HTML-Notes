"""Unit tests for deterministic research intent and budget calculation."""
import pytest
from app.services.research.intent import classify_research_intent
from app.services.research.budget import calculate_research_budget


def test_conversational_and_canvas_returns_none():
    assert classify_research_intent("hello") is None
    assert classify_research_intent("thanks") is None
    assert classify_research_intent("good morning!") is None
    assert classify_research_intent("close everything") is None
    assert classify_research_intent("clear the canvas") is None


def test_fast_market_brief_routing():
    intent = classify_research_intent("stock market news")
    assert intent is not None
    assert intent.mode == "fast_market_brief"
    assert intent.needs_live_price is True

    budget = calculate_research_budget(intent)
    assert budget.foreground_deadline_ms <= 6500
    assert budget.max_llm_calls == 1


def test_explain_move_routing():
    intent = classify_research_intent("why is NVDA down today?")
    assert intent is not None
    assert intent.mode == "explain_move"
    assert len(intent.entities) == 1
    assert intent.entities[0].symbol == "NVDA"
    assert intent.needs_live_price is True

    budget = calculate_research_budget(intent)
    assert budget.foreground_deadline_ms <= 7000
    assert budget.background_deadline_ms >= 30000


def test_event_report_routing():
    intent = classify_research_intent("NVDA earnings summary and guidance")
    assert intent is not None
    assert intent.mode == "event_report"
    assert "earnings" in intent.event_types
    assert "guidance" in intent.event_types
    assert intent.needs_primary_sources is True


def test_comparison_routing():
    intent = classify_research_intent("compare MSFT vs GOOGL AI exposure")
    assert intent is not None
    assert intent.mode == "comparison"
    symbols = {e.symbol for e in intent.entities}
    assert "MSFT" in symbols
    assert "GOOGL" in symbols or "GOOGLE" in symbols
    assert intent.needs_comparison is True


def test_dossier_routing():
    intent = classify_research_intent("research a bull/bear thesis on SOFI")
    assert intent is not None
    assert intent.mode == "dossier"
    assert any(e.symbol == "SOFI" for e in intent.entities)
    assert intent.evidence_depth == "deep"

    budget = calculate_research_budget(intent)
    assert budget.foreground_deadline_ms >= 8000
    assert budget.background_deadline_ms >= 60000
