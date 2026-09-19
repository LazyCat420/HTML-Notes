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


def test_update_note_requires_session_id(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'note-session-req', 'Title', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>C</p>', 1, 'session_x', 'session', 'session_x'
        )
    """)
    conn.commit()
    conn.close()

    # None session_id
    res_none = NotesDomainService.update_note(note_id="note-session-req", session_id=None, title="Hacked")
    assert res_none.get("is_error") is True
    assert res_none.get("code") == "SESSION_REQUIRED"

    # Empty string session_id
    res_empty = NotesDomainService.update_note(note_id="note-session-req", session_id="   ", title="Hacked")
    assert res_empty.get("is_error") is True
    assert res_empty.get("code") == "SESSION_REQUIRED"


def test_link_notes_requires_session_and_validates_both_ends(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id)
        VALUES
        ('note-s1', 'Note S1', '2026-09-01T00:00:00', '2026-09-01T00:00:00', '[]', '[]', '[]', '[]', '<p>S1</p>', 1, 'session_1', 'session', 'session_1'),
        ('note-s1-b', 'Note S1 B', '2026-09-01T00:00:00', '2026-09-01T00:00:00', '[]', '[]', '[]', '[]', '<p>S1B</p>', 1, 'session_1', 'session', 'session_1'),
        ('note-s2', 'Note S2', '2026-09-01T00:00:00', '2026-09-01T00:00:00', '[]', '[]', '[]', '[]', '<p>S2</p>', 1, 'session_2', 'session', 'session_2'),
        ('note-unclaimed', 'Unclaimed', '2026-09-01T00:00:00', '2026-09-01T00:00:00', '[]', '[]', '[]', '[]', '<p>U</p>', 1, NULL, 'legacy_unclaimed', 'mig')
    """)
    conn.commit()
    conn.close()

    # Missing session_id
    res_no_sess = NotesDomainService.link_notes("note-s1", "note-s1-b", session_id=None)
    assert res_no_sess.get("is_error") is True
    assert res_no_sess.get("code") == "SESSION_REQUIRED"

    # Cross-session link attempt (s1 -> s2)
    res_cross = NotesDomainService.link_notes("note-s1", "note-s2", session_id="session_1")
    assert res_cross.get("is_error") is True
    assert res_cross.get("code") == "NOTE_SESSION_MISMATCH"

    # Unclaimed note link attempt (s1 -> unclaimed)
    res_uncl = NotesDomainService.link_notes("note-s1", "note-unclaimed", session_id="session_1")
    assert res_uncl.get("is_error") is True
    assert res_uncl.get("code") == "NOTE_UNCLAIMED"

    # Valid link within session_1
    res_valid = NotesDomainService.link_notes("note-s1", "note-s1-b", session_id="session_1")
    assert res_valid.get("success") is True
    note_a = database.get_note_by_id("note-s1")
    assert "note-s1-b" in note_a["links"]


def test_atomic_claim_race_and_reclaim_protection(isolated_db):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notes (
            id, title, created_at, updated_at, tags, links,
            source_messages, canonical_blocks, rendered_html, version, session_id, owner_type, owner_id
        ) VALUES (
            'race-target', 'Race Target', '2026-09-01T00:00:00', '2026-09-01T00:00:00',
            '[]', '[]', '[]', '[]', '<p>Race</p>', 1, NULL, 'legacy_unclaimed', 'mig'
        )
    """)
    conn.commit()
    conn.close()

    # Session 1 claims the note
    claim_1 = NotesDomainService.claim_note("race-target", session_id="session_fast", owner_id="user_fast")
    assert claim_1.get("success") is True
    assert claim_1.get("session_id") == "session_fast"

    # Session 2 attempts to claim the same note -> rejected atomically
    claim_2 = NotesDomainService.claim_note("race-target", session_id="session_slow", owner_id="user_slow")
    assert claim_2.get("is_error") is True
    assert claim_2.get("code") == "NOTE_ALREADY_CLAIMED"

    # Verify session_fast is still the sole owner
    persisted = database.get_note_by_id("race-target")
    assert persisted["session_id"] == "session_fast"


def test_http_routes_enforce_session_ownership(isolated_db):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    # 1. Create a note with session_1
    res_create = client.post("/notes/create", json={
        "title": "HTTP Route Test",
        "rendered_html": "<p>Content</p>",
        "session_id": "session_http_1"
    })
    assert res_create.status_code == 200
    note_id = res_create.json()["id"]

    # 2. Update without session_id -> 401
    res_no_sess = client.post("/notes/update", json={
        "note_id": note_id,
        "title": "No Session"
    })
    assert res_no_sess.status_code == 401

    # 3. Update with foreign session_id -> 403
    res_foreign = client.post("/notes/update", json={
        "note_id": note_id,
        "session_id": "session_foreign",
        "title": "Foreign Hijack"
    })
    assert res_foreign.status_code == 403

    # 4. Update with correct session_id -> 200
    res_valid = client.post("/notes/update", json={
        "note_id": note_id,
        "session_id": "session_http_1",
        "title": "Authorized Update"
    })
    assert res_valid.status_code == 200
    assert res_valid.json()["title"] == "Authorized Update"

    # 5. Link without session_id -> 401
    res_link_no_sess = client.post("/notes/link", json={
        "source_note_id": note_id,
        "target_note_id": note_id
    })
    assert res_link_no_sess.status_code == 401

    # 6. Claim without session_id -> 401
    res_claim_no_sess = client.post("/notes/claim", json={
        "note_id": note_id,
        "session_id": ""
    })
    assert res_claim_no_sess.status_code == 401

    # 7. Competing claim on already claimed note -> 409
    res_competing_claim = client.post("/notes/claim", json={
        "note_id": note_id,
        "session_id": "session_usurper"
    })
    assert res_competing_claim.status_code == 409


@pytest.mark.asyncio
async def test_internal_tools_enforce_session_ownership(isolated_db):
    from app.routes.internal import internal_tool_execute
    from app.main import InternalToolRequest

    # Create note in session_int_1
    req_create = InternalToolRequest(
        tool="html_notes_create_note",
        args={"title": "Internal Note", "rendered_html": "<p>Int</p>"},
        session_id="session_int_1"
    )
    res_create = await internal_tool_execute(req_create)
    assert res_create.get("success") is True
    note_id = res_create["note_id"]

    # Update without session -> rejected
    req_up_no_sess = InternalToolRequest(
        tool="html_notes_update_note",
        args={"note_id": note_id, "title": "Bypass"}
    )
    res_up_no_sess = await internal_tool_execute(req_up_no_sess)
    assert res_up_no_sess.get("is_error") is True
    assert res_up_no_sess.get("code") == "SESSION_REQUIRED"

    # Update with foreign session -> rejected
    req_up_foreign = InternalToolRequest(
        tool="html_notes_update_note",
        args={"note_id": note_id, "title": "Foreign Int"},
        session_id="session_foreign"
    )
    res_up_foreign = await internal_tool_execute(req_up_foreign)
    assert res_up_foreign.get("is_error") is True
    assert res_up_foreign.get("code") == "NOTE_SESSION_MISMATCH"

    # Update with authorized session -> success
    req_up_valid = InternalToolRequest(
        tool="html_notes_update_note",
        args={"note_id": note_id, "title": "Authorized Int Update"},
        session_id="session_int_1"
    )
    res_up_valid = await internal_tool_execute(req_up_valid)
    assert res_up_valid.get("success") is True
