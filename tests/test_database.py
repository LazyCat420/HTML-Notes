import os
import pytest

# Configure DATABASE_URL to use a test file
os.environ["DATABASE_URL"] = "data/test_notes.db"

from app import database

@pytest.fixture(autouse=True)
def setup_and_teardown_db():
    # Setup: Ensure schema is initialized
    database.init_db()
    yield
    # Clean up THIS test's rows — not the shared schema. Dropping chat_sessions/
    # chat_messages/notes here left every later module (collection order) with a
    # schema missing those tables: "no such table: chat_sessions" → 500s that
    # only appeared in the full-suite run.
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM note_versions WHERE note_id = 'test_note_999'")
    cursor.execute("DELETE FROM notes WHERE id = 'test_note_999'")
    conn.commit()
    conn.close()

def test_note_lifecycle():
    note_id = "test_note_999"
    # Create Note
    note = database.create_note(
        note_id=note_id,
        title="Test Journal Title",
        tags=["pytest", "db"],
        links=[],
        source_messages=["msg_0"],
        canonical_blocks=[{"type": "paragraph", "text": "Testing..."}],
        rendered_html="<article><p>Testing...</p></article>"
    )
    
    assert note["id"] == note_id
    assert note["title"] == "Test Journal Title"
    assert note["version"] == 1
    assert "pytest" in note["tags"]
    
    # Update Note (Version 2)
    updated = database.update_note(
        note_id=note_id,
        title="Updated Title",
        tags=["pytest", "db", "updated"],
        rendered_html="<article><p>Updated content</p></article>"
    )
    
    assert updated["version"] == 2
    assert updated["title"] == "Updated Title"
    assert "updated" in updated["tags"]
    
    # Retrieve History
    history = database.get_note_history(note_id)
    assert len(history) == 2
    assert history[0]["version"] == 2
    assert history[1]["version"] == 1
    
    # List and Search
    all_notes = database.list_all_notes()
    assert len(all_notes) >= 1
    
    results = database.search_notes("Updated")
    assert len(results) >= 1
    assert results[0]["id"] == note_id
