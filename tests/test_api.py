import os
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "data/test_notes.db"

from app.main import app

client = TestClient(app)

def test_health_endpoints():
    res = client.get("/health/app")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["service"] == "html-notes"
    # The agent dependency is REPORTED here but never gates liveness.
    assert "agent" in body

    res = client.get("/health/model")
    assert res.status_code == 200
    assert "status" in res.json()


def test_liveness_stays_ok_when_the_agent_dependency_is_down(monkeypatch):
    """docker-compose healthchecks /health/app with `curl -f`. A non-2xx here
    restarts the container — which cannot fix a Prism-side outage, so it would
    just loop. The breakage must be visible in the body, not in the status."""
    import app.main as m

    async def dead(*a, **k):
        return {"ok": False, "error": "prism unreachable: boom"}

    monkeypatch.setattr(m, "_agent_dependency_status", dead)

    res = client.get("/health/app")
    assert res.status_code == 200, "liveness must not fail on a downstream outage"
    assert res.json()["agent"]["ok"] is False, "but it must SAY so"


def test_readiness_503s_when_the_agent_dependency_is_down(monkeypatch):
    """/health/agent is the one that fails, so a monitor can see it."""
    import app.main as m

    async def dead(*a, **k):
        return {"ok": False, "error": "MCP server registered but not serving tools"}

    monkeypatch.setattr(m, "_agent_dependency_status", dead)

    res = client.get("/health/agent")
    assert res.status_code == 503
    assert res.json()["status"] == "unavailable"


def test_readiness_is_ok_when_tools_are_serving(monkeypatch):
    import app.main as m

    async def alive(*a, **k):
        return {"ok": True, "mcp_connected": True, "tool_count": 75, "persona_tools": 21}

    monkeypatch.setattr(m, "_agent_dependency_status", alive)

    res = client.get("/health/agent")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

def test_note_apis():
    # Direct Create Note via API
    payload = {
        "title": "API Created Note",
        "tags": ["api"],
        "links": [],
        "canonical_blocks": [{"type": "paragraph", "text": "API text"}],
        "rendered_html": "<article><p>API text</p></article>"
    }
    
    res = client.post("/notes/create", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "API Created Note"
    note_id = data["id"]
    
    # Get note details
    res = client.get(f"/notes/{note_id}")
    assert res.status_code == 200
    assert res.json()["note"]["id"] == note_id
    
    # Direct update via API
    update_payload = {
        "note_id": note_id,
        "title": "API Created Note (Updated)"
    }
    res = client.post("/notes/update", json=update_payload)
    assert res.status_code == 200
    assert res.json()["title"] == "API Created Note (Updated)"
    assert res.json()["version"] == 2

def test_notes_graph_endpoint():
    res = client.get("/graph")
    assert res.status_code == 200
    data = res.json()
    assert "nodes" in data
    assert "edges" in data
