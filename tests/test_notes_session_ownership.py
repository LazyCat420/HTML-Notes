import pytest
from app import database
from app.domain.notes.service import NotesDomainService


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    test_db_path = str(tmp_path / "test_notes_ownership.db")
    monkeypatch.setattr(database, "DATABASE_URL", test_db_path)
    database.init_db()
    return test_db_path


def test_legacy_null_session_note_is_not_cross_session_mutable(isolated_db):
    # Insert a legacy note with session_id = NULL and owner_type = 'legacy_unclaimed'
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'legacy-note-sec-1', 'Legacy Original Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Original</p>', 1, NULL, 'legacy_unclaimed', 'migration-2026-09-19'
        )
    """)
    conn.commit()
    conn.close()

    # Any session attempting to update this legacy unclaimed note must be rejected
    result = NotesDomainService.update_note(
        note_id="legacy-note-sec-1",
        session_id="foreign_session_attempt",
        title="Unauthorized Hijack Title",
        rendered_html="<p>Hacked</p>"
    )
    assert result.get("is_error") is True
    assert result.get("code") == "NOTE_UNCLAIMED"

    # Verify database was NOT mutated
    persisted = database.get_note_by_id("legacy-note-sec-1")
    assert persisted["title"] == "Legacy Original Title"
    assert persisted["rendered_html"] == "<p>Original</p>"


def test_unclaimed_note_rejects_update(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'unclaimed-note-test', 'Unclaimed Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Unclaimed</p>', 1, NULL, 'legacy_unclaimed', 'migration-2026-09-19'
        )
    """)
    conn.commit()
    conn.close()

    # Attempting to update without claiming first
    res = NotesDomainService.update_note(
        note_id="unclaimed-note-test",
        session_id="any_session",
        title="New Title"
    )
    assert res.get("is_error") is True
    assert res.get("code") == "NOTE_UNCLAIMED"
    assert "must be claimed before updating" in res.get("error", "")

    # Check database state untouched
    note = database.get_note_by_id("unclaimed-note-test")
    assert note["title"] == "Unclaimed Title"


def test_explicit_claim_binds_note_to_current_owner(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'claimable-note-1', 'Initial Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Body</p>', 1, NULL, 'legacy_unclaimed', 'migration-2026-09-19'
        )
    """)
    conn.commit()
    conn.close()

    # Explicitly claim the note
    claim_res = NotesDomainService.claim_note(
        note_id="claimable-note-1",
        session_id="session_legitimate_owner",
        owner_id="user_admin_durable"
    )
    assert claim_res.get("success") is True
    assert claim_res.get("session_id") == "session_legitimate_owner"
    assert claim_res.get("owner_id") == "user_admin_durable"

    # Verify database persistence
    claimed_record = database.get_note_by_id("claimable-note-1")
    assert claimed_record["owner_type"] in ("session", "claimed")
    assert claimed_record["owner_id"] == "user_admin_durable"
    assert claimed_record["session_id"] == "session_legitimate_owner"
    assert claimed_record["claimed_at"] is not None

    # Now legitimate owner can successfully update the note
    update_res = NotesDomainService.update_note(
        note_id="claimable-note-1",
        session_id="session_legitimate_owner",
        title="Updated Claimed Title",
        rendered_html="<p>Updated Body</p>"
    )
    assert update_res.get("success") is True

    persisted = database.get_note_by_id("claimable-note-1")
    assert persisted["title"] == "Updated Claimed Title"
    assert persisted["rendered_html"] == "<p>Updated Body</p>"


def test_claimed_note_rejects_foreign_session_update(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'claimed-by-session-a', 'Session A Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Session A Content</p>', 1, 'session_A', 'claimed', 'user_A'
        )
    """)
    conn.commit()
    conn.close()

    # Session B attempts to update Session A's note
    res = NotesDomainService.update_note(
        note_id="claimed-by-session-a",
        session_id="session_B",
        title="Session B Hijack"
    )
    assert res.get("is_error") is True
    assert res.get("code") == "NOTE_SESSION_MISMATCH"
    assert "belongs to another session" in res.get("error", "")

    # Note remains unmodified
    note = database.get_note_by_id("claimed-by-session-a")
    assert note["title"] == "Session A Title"
