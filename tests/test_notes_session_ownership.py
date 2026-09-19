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


def test_session_id_required_for_create_and_update(isolated_db):
    # Create note without session_id must fail
    res_create = NotesDomainService.create_note(
        title="Sessionless Note",
        rendered_html="<p>Test</p>",
        session_id=None
    )
    assert res_create.get("is_error") is True
    assert res_create.get("code") == "NOTE_SESSION_REQUIRED"

    # Create note with valid session
    res_create_ok = NotesDomainService.create_note(
        title="Owned Note",
        rendered_html="<p>Owned</p>",
        session_id="session_owner_1"
    )
    assert res_create_ok.get("success") is True
    note_id = res_create_ok["note_id"]

    # Update without session_id must fail
    res_update_none = NotesDomainService.update_note(
        note_id=note_id,
        session_id=None,
        title="New Title"
    )
    assert res_update_none.get("is_error") is True
    assert res_update_none.get("code") == "NOTE_SESSION_REQUIRED"

    # Update with empty string must fail
    res_update_empty = NotesDomainService.update_note(
        note_id=note_id,
        session_id="   ",
        title="New Title"
    )
    assert res_update_empty.get("is_error") is True
    assert res_update_empty.get("code") == "NOTE_SESSION_REQUIRED"


def test_link_notes_validates_both_ends_and_ownership(isolated_db):
    # Setup notes
    # 1. Note 1 owned by session_1
    n1 = NotesDomainService.create_note("Note 1", "<p>1</p>", session_id="session_1")["note_id"]
    # 2. Note 2 owned by session_1
    n2 = NotesDomainService.create_note("Note 2", "<p>2</p>", session_id="session_1")["note_id"]
    # 3. Note 3 owned by session_2
    n3 = NotesDomainService.create_note("Note 3", "<p>3</p>", session_id="session_2")["note_id"]
    # 4. Unclaimed note
    conn = database.get_connection()
    conn.execute("""
        INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version, session_id, owner_type)
        VALUES ('unclaimed_link_note', 'Unclaimed', '2026-09-01', '2026-09-01', '[]', '[]', '[]', '[]', '<p>U</p>', 1, NULL, 'legacy_unclaimed')
    """)
    conn.commit()
    conn.close()

    # Link without session_id fails
    res_no_sess = NotesDomainService.link_notes(n1, n2, session_id=None)
    assert res_no_sess.get("is_error") is True
    assert res_no_sess.get("code") == "NOTE_SESSION_REQUIRED"

    # Link with non-existent source fails
    res_no_src = NotesDomainService.link_notes("non_existent_id", n2, session_id="session_1")
    assert res_no_src.get("is_error") is True
    assert res_no_src.get("code") == "SOURCE_NOTE_NOT_FOUND"

    # Link with non-existent target fails
    res_no_tgt = NotesDomainService.link_notes(n1, "non_existent_id", session_id="session_1")
    assert res_no_tgt.get("is_error") is True
    assert res_no_tgt.get("code") == "TARGET_NOTE_NOT_FOUND"

    # Link with foreign source fails
    res_foreign_src = NotesDomainService.link_notes(n3, n1, session_id="session_1")
    assert res_foreign_src.get("is_error") is True
    assert res_foreign_src.get("code") == "NOTE_SESSION_MISMATCH"

    # Link with foreign target fails
    res_foreign_tgt = NotesDomainService.link_notes(n1, n3, session_id="session_1")
    assert res_foreign_tgt.get("is_error") is True
    assert res_foreign_tgt.get("code") == "NOTE_SESSION_MISMATCH"

    # Link with unclaimed source fails
    res_unclaimed_src = NotesDomainService.link_notes("unclaimed_link_note", n1, session_id="session_1")
    assert res_unclaimed_src.get("is_error") is True
    assert res_unclaimed_src.get("code") == "NOTE_UNCLAIMED"

    # Link with unclaimed target fails
    res_unclaimed_tgt = NotesDomainService.link_notes(n1, "unclaimed_link_note", session_id="session_1")
    assert res_unclaimed_tgt.get("is_error") is True
    assert res_unclaimed_tgt.get("code") == "NOTE_UNCLAIMED"

    # Legitimate linking where both ends belong to session_1 succeeds
    res_ok = NotesDomainService.link_notes(n1, n2, session_id="session_1")
    assert res_ok.get("success") is True
    assert res_ok["source"] == n1
    assert res_ok["target"] == n2

    # Verify link persistence in database
    n1_db = database.get_note_by_id(n1)
    assert n2 in n1_db["links"]


def test_atomic_claim_concurrency_race(isolated_db):
    import concurrent.futures

    # Insert an unclaimed note
    note_id = "race_candidate_note"
    conn = database.get_connection()
    conn.execute("""
        INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version, session_id, owner_type)
        VALUES (?, 'Race Title', '2026-09-01', '2026-09-01', '[]', '[]', '[]', '[]', '<p>Race</p>', 1, NULL, 'legacy_unclaimed')
    """, (note_id,))
    conn.commit()
    conn.close()

    results = []
    def attempt_claim(worker_idx: int):
        session_id = f"racing_session_{worker_idx}"
        return NotesDomainService.claim_note(
            note_id=note_id,
            session_id=session_id,
            owner_id=f"user_{worker_idx}"
        )

    # 10 concurrent threads racing to claim the single note
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(attempt_claim, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly one claim must succeed
    successes = [r for r in results if r.get("success") is True]
    conflicts = [r for r in results if r.get("code") == "NOTE_ALREADY_CLAIMED"]
    assert len(successes) == 1
    assert len(conflicts) == 9

    winner = successes[0]
    # Check that note in database is owned by the winning session
    note_in_db = database.get_note_by_id(note_id)
    assert note_in_db["session_id"] == winner["session_id"]
    assert note_in_db["version"] == 2

    # Check that note_versions has a version record for the claim
    history = database.get_note_history(note_id)
    assert len(history) >= 1
    assert history[0]["version"] == 2


def test_http_routes_ownership_enforcement(isolated_db):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # 1. /notes/create requires session_id
    res = client.post("/notes/create", json={"title": "T", "rendered_html": "<p>T</p>"})
    assert res.status_code == 400
    assert "session_id is required" in res.text

    # /notes/create with X-Session-ID header succeeds
    res = client.post(
        "/notes/create",
        json={"title": "T1", "rendered_html": "<p>T1</p>"},
        headers={"x-session-id": "http_session_1"}
    )
    assert res.status_code == 200
    n1 = res.json()["id"]

    # 2. /notes/update without session_id is rejected
    res_up = client.post("/notes/update", json={"note_id": n1, "title": "T2"})
    assert res_up.status_code == 400

    # /notes/update with foreign session is rejected with 403 Forbidden
    res_up_foreign = client.post(
        "/notes/update",
        json={"note_id": n1, "title": "Hijacked"},
        headers={"x-session-id": "foreign_session"}
    )
    assert res_up_foreign.status_code == 403

    # /notes/update with correct session succeeds
    res_up_ok = client.post(
        "/notes/update",
        json={"note_id": n1, "title": "Updated Title"},
        headers={"x-session-id": "http_session_1"}
    )
    assert res_up_ok.status_code == 200
    assert res_up_ok.json()["title"] == "Updated Title"

    # 3. /notes/link requires session_id
    # Create note 2 in session 1
    res_n2 = client.post(
        "/notes/create",
        json={"title": "T2", "rendered_html": "<p>T2</p>"},
        headers={"x-session-id": "http_session_1"}
    )
    n2 = res_n2.json()["id"]

    # Link without session_id
    res_link_no_sess = client.post("/notes/link", json={"source_note_id": n1, "target_note_id": n2})
    assert res_link_no_sess.status_code == 400

    # Link with foreign session
    res_link_foreign = client.post(
        "/notes/link",
        json={"source_note_id": n1, "target_note_id": n2},
        headers={"x-session-id": "foreign_session"}
    )
    assert res_link_foreign.status_code == 403

    # Link with correct session
    res_link_ok = client.post(
        "/notes/link",
        json={"source_note_id": n1, "target_note_id": n2},
        headers={"x-session-id": "http_session_1"}
    )
    assert res_link_ok.status_code == 200

    # 4. /notes/claim endpoint
    # Insert legacy unclaimed note
    conn = database.get_connection()
    conn.execute("""
        INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version, session_id, owner_type)
        VALUES ('http_unclaimed', 'Unclaimed', '2026-09-01', '2026-09-01', '[]', '[]', '[]', '[]', '<p>U</p>', 1, NULL, 'legacy_unclaimed')
    """)
    conn.commit()
    conn.close()

    # Claim without session_id
    res_claim_no_sess = client.post("/notes/claim", json={"note_id": "http_unclaimed"})
    assert res_claim_no_sess.status_code == 400

    # Claim non-existent note
    res_claim_missing = client.post(
        "/notes/claim",
        json={"note_id": "ghost_note"},
        headers={"x-session-id": "session_claim"}
    )
    assert res_claim_missing.status_code == 404

    # Legitimate claim
    res_claim_ok = client.post(
        "/notes/claim",
        json={"note_id": "http_unclaimed"},
        headers={"x-session-id": "session_claim"}
    )
    assert res_claim_ok.status_code == 200
    assert res_claim_ok.json()["success"] is True

    # Second claim by another session returns 409 Conflict
    res_claim_conflict = client.post(
        "/notes/claim",
        json={"note_id": "http_unclaimed"},
        headers={"x-session-id": "competing_session"}
    )
    assert res_claim_conflict.status_code == 409


def test_internal_execute_ownership_enforcement(isolated_db):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # 1. Create note via internal execute requires session_id
    res = client.post("/internal/execute", json={
        "tool": "html_notes_create_note",
        "args": {"title": "Internal Note", "rendered_html": "<p>Internal</p>"}
    })
    data = res.json()
    assert data.get("is_error") is True
    assert data.get("code") == "NOTE_SESSION_REQUIRED"

    # Create note with session_id in args
    res_ok = client.post("/internal/execute", json={
        "tool": "html_notes_create_note",
        "args": {"title": "Internal Note", "rendered_html": "<p>Internal</p>", "session_id": "sess_internal_1"}
    })
    assert res_ok.json().get("success") is True
    note_id = res_ok.json()["note_id"]

    # 2. Update note via internal execute without session_id
    res_up_no_sess = client.post("/internal/execute", json={
        "tool": "html_notes_update_note",
        "args": {"note_id": note_id, "title": "Updated"}
    })
    assert res_up_no_sess.json().get("code") == "NOTE_SESSION_REQUIRED"

    # Update note via internal execute with foreign session_id
    res_up_foreign = client.post("/internal/execute", json={
        "tool": "html_notes_update_note",
        "args": {"note_id": note_id, "title": "Hacked", "session_id": "foreign_sess"}
    })
    assert res_up_foreign.json().get("code") == "NOTE_SESSION_MISMATCH"

    # Update note via internal execute with correct session_id
    res_up_ok = client.post("/internal/execute", json={
        "tool": "html_notes_update_note",
        "args": {"note_id": note_id, "title": "Valid Update", "session_id": "sess_internal_1"}
    })
    assert res_up_ok.json().get("success") is True

    # 3. modify_dom via internal execute checks session ownership
    res_dom_foreign = client.post("/internal/execute", json={
        "tool": "html_notes_modify_dom",
        "args": {
            "note_id": note_id,
            "session_id": "foreign_sess",
            "css_selector": "p",
            "action": "replace",
            "html_snippet": "<p>Injected</p>"
        }
    })
    assert res_dom_foreign.json().get("code") == "NOTE_SESSION_MISMATCH"

    res_dom_ok = client.post("/internal/execute", json={
        "tool": "html_notes_modify_dom",
        "args": {
            "note_id": note_id,
            "session_id": "sess_internal_1",
            "css_selector": "p",
            "action": "replace",
            "html_snippet": "<p>Replaced</p>"
        }
    })
    assert res_dom_ok.json().get("success") is True


def test_migration_batch_and_rollback_preserves_claimed_notes(isolated_db):
    from scripts import migrate_legacy_note_ownership

    # Seed 2 legacy notes with session_id = NULL
    conn = database.get_connection()
    conn.execute("""
        INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version, session_id, owner_type)
        VALUES
        ('mig_1', 'Mig 1', '2026-09-01', '2026-09-01', '[]', '[]', '[]', '[]', '<p>1</p>', 1, NULL, 'session'),
        ('mig_2', 'Mig 2', '2026-09-01', '2026-09-01', '[]', '[]', '[]', '[]', '<p>2</p>', 1, NULL, 'session')
    """)
    conn.commit()
    conn.close()

    # Dry-run migration
    dry_count = migrate_legacy_note_ownership.run_migration(owner_id="test-mig", dry_run=True)
    assert dry_count == 2
    # Verify no database mutation happened during dry-run
    n1 = database.get_note_by_id("mig_1")
    assert n1["owner_type"] == "session"

    # Actual migration
    count = migrate_legacy_note_ownership.run_migration(owner_id="test-mig", dry_run=False)
    assert count == 2
    n1_migrated = database.get_note_by_id("mig_1")
    assert n1_migrated["owner_type"] == "legacy_unclaimed"
    assert n1_migrated["owner_id"] == "test-mig"

    # Active user claims mig_1
    claim_res = NotesDomainService.claim_note("mig_1", session_id="user_active_session")
    assert claim_res.get("success") is True
    assert database.get_note_by_id("mig_1")["session_id"] == "user_active_session"

    # Rollback migration
    rb_count = migrate_legacy_note_ownership.run_rollback(owner_id="test-mig", dry_run=False)
    # Only mig_2 should be rolled back, because mig_1 was claimed by an active session!
    assert rb_count == 1

    # mig_2 is rolled back to session ownership with NULL owner_id
    n2_rb = database.get_note_by_id("mig_2")
    assert n2_rb["owner_type"] == "session"
    assert n2_rb["owner_id"] is None

    # mig_1 is still intact and owned by user_active_session
    n1_preserved = database.get_note_by_id("mig_1")
    assert n1_preserved["session_id"] == "user_active_session"
    assert n1_preserved["owner_type"] == "session"

