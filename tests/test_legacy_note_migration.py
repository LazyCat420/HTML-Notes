import json
import sqlite3
import pytest
from app import database
from scripts.migrate_legacy_note_ownership import run_migration, run_rollback


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    test_db_path = str(tmp_path / "test_migration_notes.db")
    monkeypatch.setattr(database, "DATABASE_URL", test_db_path)
    database.init_db()
    return test_db_path


def test_legacy_note_migration_is_idempotent(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    # Insert legacy notes with session_id = NULL and owner_type = 'session' (pre-migration state)
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'legacy-note-1', 'Legacy Title 1', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '["legacy"]', '[]', '[]', '[]', '<p>Legacy Content 1</p>', 1, NULL, 'session', NULL
        )
    """)
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'legacy-note-2', 'Legacy Title 2', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '["legacy"]', '[]', '[]', '[]', '<p>Legacy Content 2</p>', 1, NULL, 'session', NULL
        )
    """)
    conn.commit()
    conn.close()

    # First migration run: updates 2 records
    count_1 = run_migration(owner_id="migration-2026-09-19")
    assert count_1 == 2

    # Verify state after first run
    note1 = database.get_note_by_id("legacy-note-1")
    assert note1["owner_type"] == "legacy_unclaimed"
    assert note1["owner_id"] == "migration-2026-09-19"
    assert note1["session_id"] is None

    # Second migration run: should update 0 records (idempotent)
    count_2 = run_migration(owner_id="migration-2026-09-19")
    assert count_2 == 0

    # Verify state remains legacy_unclaimed
    note1_again = database.get_note_by_id("legacy-note-1")
    assert note1_again["owner_type"] == "legacy_unclaimed"
    assert note1_again["owner_id"] == "migration-2026-09-19"


def test_migration_marks_legacy_notes_unclaimed(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    # Insert 1 legacy note (session_id = NULL) and 1 active session note (session_id = 'session-abc')
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'legacy-target', 'Legacy Target', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Unclaimed</p>', 1, NULL, 'session', NULL
        )
    """)
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'active-session-note', 'Active Note', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Active</p>', 1, 'session-abc', 'session', 'session-abc'
        )
    """)
    conn.commit()
    conn.close()

    count = run_migration(owner_id="migration-2026-09-19")
    assert count == 1

    migrated_note = database.get_note_by_id("legacy-target")
    assert migrated_note["owner_type"] == "legacy_unclaimed"
    assert migrated_note["owner_id"] == "migration-2026-09-19"
    assert migrated_note["session_id"] is None

    active_note = database.get_note_by_id("active-session-note")
    assert active_note["owner_type"] == "session"
    assert active_note["session_id"] == "session-abc"


def test_migration_preserves_note_html_tags_and_backlinks(isolated_db):
    test_tags = ["architecture", "research", "v1.2"]
    test_links = ["target-note-42", "target-note-99"]
    test_blocks = [{"type": "paragraph", "content": "Critical text block"}]
    test_html = "<div class='note'><p>Rich <b>HTML</b> content with <a href='/notes/target-note-42'>Backlink</a></p></div>"

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'preserved-note', 'Preserved Note Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            ?, ?, '["msg_1"]', ?, ?, 1, NULL, 'session', NULL
        )
    """, (
        json.dumps(test_tags),
        json.dumps(test_links),
        json.dumps(test_blocks),
        test_html
    ))
    conn.commit()
    conn.close()

    run_migration(owner_id="migration-2026-09-19")

    note = database.get_note_by_id("preserved-note")
    assert note["title"] == "Preserved Note Title"
    assert note["rendered_html"] == test_html
    assert note["tags"] == test_tags
    assert note["links"] == test_links
    assert note["canonical_blocks"] == test_blocks
    assert note["owner_type"] == "legacy_unclaimed"
    assert note["owner_id"] == "migration-2026-09-19"
