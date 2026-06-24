import os
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "data/test_notes.db"

from app.main import app

client = TestClient(app)

def test_health_endpoints():
    res = client.get("/health/app")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "html-notes"}
    
    res = client.get("/health/model")
    assert res.status_code == 200
    assert "status" in res.json()

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
