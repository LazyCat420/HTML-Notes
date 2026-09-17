"""End-to-End Integration tests for Research Protocol."""
import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
import app.database as database


@pytest.fixture(autouse=True)
def init_test_db():
    database.init_db()


def _parse_sse_events(sse_text: str):
    events = []
    for line in sse_text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except Exception:
                pass
    return events


def test_research_protocol_explain_move_e2e():
    client = TestClient(app)
    session_id = "test-session-research-e2e"

    # Mock stock_snapshot to return immediately
    fake_snap = {
        "price": 128.50,
        "change_pct": -3.2,
        "change": -4.25,
        "name": "NVIDIA Corporation",
        "currency": "USD",
        "history": [{"close": 132.75}, {"close": 128.50}],
        "as_of": "2026-09-17 14:00 UTC",
    }

    # Mock _finnews_articles to return immediately
    fake_news = [
        {
            "title": "Tech Stocks Slide on Macro Rate Jitter",
            "publisher": "Reuters",
            "published": "2026-09-17 13:45 UTC",
            "url": "https://reuters.com/tech-stocks-slide",
            "og_desc": "Semiconductors face pressure across the board.",
            "related_tickers": ["NVDA", "SMH"],
        }
    ]

    with patch("app.main.stock_snapshot", new=AsyncMock(return_value=fake_snap)), \
         patch("app.main._finnews_articles", new=AsyncMock(return_value=fake_news)), \
         patch("app.main.stock_news", new=AsyncMock(return_value={"news": fake_news})):

        resp = client.post("/session/message", json={
            "session_id": session_id,
            "message": "why is NVDA down today?",
        })

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        types = [e.get("type") for e in events]

        # Verify full lifecycle events
        assert "debug" in types
        debug_ev = next(e for e in events if e.get("type") == "debug")
        assert debug_ev.get("id_prefix") == "research"
        assert debug_ev.get("research_mode") == "explain_move"

        assert "research.started" in types
        started_ev = next(e for e in events if e.get("type") == "research.started")
        assert started_ev.get("mode") == "explain_move"
        assert "NVDA" in started_ev.get("summary", "")

        assert "research.plan" in types
        assert "widget.provisional" in types
        prov_ev = next(e for e in events if e.get("type") == "widget.provisional")
        assert prov_ev.get("config", {}).get("provisional") is True

        assert "answer.partial" in types
        partial_ev = next(e for e in events if e.get("type") == "answer.partial")
        assert partial_ev.get("version") == 1
        assert len(partial_ev.get("text", "")) > 0

        assert "research.completed" in types
        assert "done" in types
