"""Tests for dynamic question routing, list restore precision, and interrogative matching."""
import json
import pytest
from app import main as m
from app import database


def test_list_restore_re_precision():
    """LIST_RESTORE_RE must only match queries that ask to restore/reopen a list,
    never general questions containing 'reopen' or 'restore'."""
    # Questions that contain 'reopen' or 'restore' but are NOT asking to restore a list
    adversarial_non_list_queries = [
        "how is trump going to reopen the strait",
        "how is trump going to \"reopen\" the strait",
        "why did they reopen the bridge?",
        "plans to reopen schools in autumn",
        "can surgery restore eyesight",
        "restore the power grid after the hurricane",
        "how to restore factory settings on iphone",
        "will they reopen borders next month",
    ]
    for q in adversarial_non_list_queries:
        assert not m.LIST_RESTORE_RE.search(q.lower()), f"False positive match for '{q}'"

    # Legitimate list restoration asks
    legit_list_queries = [
        "bring back my grocery list",
        "restore the checklist",
        "reopen my packing list",
        "bring back that list",
        "restore my todos please",
        "reopen the list",
        "grocery list again",
        "back to my checklist",
    ]
    for q in legit_list_queries:
        assert m.LIST_RESTORE_RE.search(q.lower()), f"Failed to match valid list restore '{q}'"


def test_resolve_restorable_list_does_not_fall_back_without_list_context(monkeypatch):
    """_resolve_restorable_list must return None if the query does not ask for a list,
    even if list:__last__ exists in the database."""
    # Mock database to simulate an existing list:__last__
    mock_list_state = json.dumps({"title": "Old Shopping List", "items": ["Apples", "Milk"]})
    monkeypatch.setattr(database, "list_widget_states", lambda prefix: [{"key": "list:__last__", "value": mock_list_state}])
    monkeypatch.setattr(database, "get_widget_state", lambda key: mock_list_state if key == "list:__last__" else None)

    # General question about reopening should NOT get the old shopping list back
    result = m._resolve_restorable_list("how is trump going to reopen the strait")
    assert result is None, f"Expected None but got {result}"

    # An actual request for a list CAN fall back to list:__last__
    legit_result = m._resolve_restorable_list("bring back my list")
    assert legit_result is not None, "Expected valid restore for 'bring back my list'"
    assert legit_result.get("title") == "Old Shopping List"


def test_answer_ask_re_matches_dynamic_interrogatives():
    """ANSWER_ASK_RE must match future, conditional, and auxiliary interrogatives
    (how is, how will, how would, what will, what would, why would, etc.)."""
    queries = [
        "how is trump going to reopen the strait",
        "how will the economy recover",
        "how would a ceasefire work",
        "how are airplanes pressurized",
        "how did the roman empire fall",
        "what will happen to oil prices",
        "what would cause a market crash",
        "who will win the election",
        "why would interest rates drop",
        "why will inflation slow down",
    ]
    for q in queries:
        assert m.ANSWER_ASK_RE.search(q.lower()), f"ANSWER_ASK_RE failed to match '{q}'"


def test_dynamic_question_routes_to_answer_card(monkeypatch):
    """'how is trump going to reopen the strait' must route to answer data_card, not checklist."""
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock

    client = TestClient(m.app)
    # Mock build_answer_config so it doesn't need external LLM / web
    mock_config = {"title": "Reopening the Strait", "answer": "Analysis of the strait reopening plans...", "items": []}
    monkeypatch.setattr("app.config_builders.build_answer_config", AsyncMock(return_value=mock_config))

    res = client.post("/session/message", json={
        "session_id": "test_dynamic_session",
        "message": "how is trump going to reopen the strait"
    })
    assert res.status_code == 200
    debug_frames = [
        json.loads(line[len("data: "):])
        for line in res.text.split("\n")
        if line.startswith("data: ") and '"type": "debug"' in line
    ]
    assert debug_frames, "Expected at least one debug frame"
    first_debug = debug_frames[0]
    assert first_debug.get("id_prefix") == "answer", f"Expected id_prefix='answer', got: {first_debug}"
    assert first_debug.get("widget_type") == "data_card", f"Expected data_card, got: {first_debug}"
    assert first_debug.get("widget_type") != "checklist", "CRITICAL BUG: Routed to checklist!"

