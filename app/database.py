import sqlite3
import time
import json
import os
from contextlib import closing
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from app.config import DATABASE_URL

_DEFAULT_DB_URL = DATABASE_URL


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _resolve_db_url() -> str:
    if DATABASE_URL != _DEFAULT_DB_URL:
        return DATABASE_URL
    return os.getenv("DATABASE_URL") or DATABASE_URL


def get_connection():
    db_url = _resolve_db_url()
    if db_url != ":memory:":
        parent = os.path.dirname(db_url)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_url)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        _init_db_tables(cursor)
        conn.commit()


def _init_db_tables(cursor):
    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Create notes table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        tags TEXT NOT NULL, -- JSON array of strings
        links TEXT NOT NULL, -- JSON array of target note IDs
        source_messages TEXT NOT NULL, -- JSON array of message IDs
        canonical_blocks TEXT NOT NULL, -- JSON array of semantic blocks
        rendered_html TEXT NOT NULL, -- Sanitized HTML output
        version INTEGER NOT NULL DEFAULT 1,
        session_id TEXT
    );
    """)
    try:
        cursor.execute("ALTER TABLE notes ADD COLUMN session_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE notes ADD COLUMN owner_type TEXT DEFAULT 'session'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE notes ADD COLUMN owner_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE notes ADD COLUMN claimed_at TEXT")
    except sqlite3.OperationalError:
        pass
    
    # Create note_versions table for tracking history
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS note_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        title TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        tags TEXT NOT NULL,
        links TEXT NOT NULL,
        canonical_blocks TEXT NOT NULL,
        rendered_html TEXT NOT NULL,
        FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    """)
    
    # Create chat sessions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    
    # Create chat messages table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    );
    """)

    # Persistent agent memory — a general key/value store that survives across
    # sessions and container restarts. First use: video/channel blocklists so
    # "this one sucks, find another" / "this channel sucks" are remembered
    # forever. Kept generic (category + key + JSON value) so future preferences
    # and remembered facts can reuse it instead of growing one table per feature.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watches (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        spec TEXT NOT NULL,
        label TEXT,
        interval_s INTEGER NOT NULL,
        next_run REAL NOT NULL,
        expires REAL NOT NULL,
        created_at TEXT NOT NULL,
        last_fired REAL,
        fire_count INTEGER DEFAULT 0,
        last_fp TEXT,
        active INTEGER DEFAULT 1
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS watches_due ON watches(active, next_run)")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,   -- e.g. 'blocked_video', 'blocked_channel'
        key TEXT NOT NULL,        -- video_id, channel name, preference key
        value TEXT,               -- optional JSON/text payload (reason, etc.)
        created_at TEXT NOT NULL,
        UNIQUE(category, key)
    );
    """)

    # Research Protocol Ledger Tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS research_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        intent_json TEXT NOT NULL,
        budget_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        foreground_deadline_at TEXT,
        background_deadline_at TEXT,
        completed_at TEXT,
        metrics_json TEXT
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_session ON research_runs(session_id, created_at);")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS research_tasks (
        task_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        task_type TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        state TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        deadline_ms INTEGER,
        cache_hit INTEGER DEFAULT 0,
        evidence_ids TEXT,
        error_class TEXT,
        details_json TEXT,
        FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_research_tasks_run ON research_tasks(run_id, state);")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS research_evidence (
        evidence_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        url TEXT NOT NULL,
        canonical_url TEXT,
        title TEXT NOT NULL,
        publisher TEXT,
        tier TEXT,
        published_at TEXT,
        extracted_text TEXT,
        quality_score REAL,
        freshness TEXT,
        source_provider TEXT,
        metadata_json TEXT,
        FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_research_evidence_run ON research_evidence(run_id);")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS research_answer_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        text TEXT NOT NULL,
        evidence_ids TEXT,
        delta_summary TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES research_runs(run_id) ON DELETE CASCADE
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_research_answers_run ON research_answer_versions(run_id, version);")
    cursor.execute("""
    DELETE FROM note_versions
    WHERE id NOT IN (SELECT MAX(id) FROM note_versions GROUP BY note_id, version);
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_note_versions_unique ON note_versions(note_id, version);")


# Initialize DB on load
init_db()


def _row_to_note(row) -> Dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"], "title": row["title"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "tags": json.loads(row["tags"]), "links": json.loads(row["links"]),
        "source_messages": json.loads(row["source_messages"]),
        "canonical_blocks": json.loads(row["canonical_blocks"]),
        "rendered_html": row["rendered_html"], "version": row["version"],
        "session_id": row["session_id"] if "session_id" in keys else None,
        "owner_type": row["owner_type"] if "owner_type" in keys else "session",
        "owner_id": row["owner_id"] if "owner_id" in keys else None,
        "claimed_at": row["claimed_at"] if "claimed_at" in keys else None,
    }


# DB Functions for Notes


def create_note(
    note_id: str,
    title: str,
    tags: List[str],
    links: List[str],
    source_messages: List[str],
    canonical_blocks: List[Dict[str, Any]],
    rendered_html: str,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                note_id, title, now, now, json.dumps(tags), json.dumps(links),
                json.dumps(source_messages), json.dumps(canonical_blocks), rendered_html, session_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO note_versions (note_id, version, title, updated_at, tags, links, canonical_blocks, rendered_html)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            """,
            (note_id, title, now, json.dumps(tags), json.dumps(links), json.dumps(canonical_blocks), rendered_html),
        )
        conn.commit()
    return get_note_by_id(note_id)


def update_note(
    note_id: str,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
    links: Optional[List[str]] = None,
    canonical_blocks: Optional[List[Dict[str, Any]]] = None,
    rendered_html: Optional[str] = None,
    source_message: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        conn.isolation_level = None
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            cursor.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("ROLLBACK")
                return None
            note = _row_to_note(row)
            new_version = note["version"] + 1
            updated_title = title if title is not None and str(title).strip() else note["title"]
            updated_tags = tags if tags is not None else note["tags"]
            updated_links = links if links is not None else note["links"]
            updated_blocks = canonical_blocks if canonical_blocks is not None else note["canonical_blocks"]
            updated_html = rendered_html if rendered_html is not None and str(rendered_html).strip() else note["rendered_html"]
            source_messages = list(note["source_messages"])
            if source_message and source_message not in source_messages:
                source_messages.append(source_message)
            cursor.execute(
                """
                UPDATE notes
                SET title = ?, updated_at = ?, tags = ?, links = ?, source_messages = ?, canonical_blocks = ?, rendered_html = ?, version = ?
                WHERE id = ?
                """,
                (
                    updated_title, now, json.dumps(updated_tags), json.dumps(updated_links),
                    json.dumps(source_messages), json.dumps(updated_blocks), updated_html, new_version, note_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO note_versions (note_id, version, title, updated_at, tags, links, canonical_blocks, rendered_html)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id, new_version, updated_title, now, json.dumps(updated_tags),
                    json.dumps(updated_links), json.dumps(updated_blocks), updated_html,
                ),
            )
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
    return get_note_by_id(note_id)


def get_note_by_id(note_id: str) -> Optional[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
    return _row_to_note(row) if row else None


def claim_note(
    note_id: str,
    session_id: str,
    owner_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Atomically binds an unclaimed or legacy note to the specified session and owner."""
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        conn.isolation_level = None
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            cursor.execute(
                """
                UPDATE notes
                SET session_id = ?, owner_type = 'session', owner_id = ?, claimed_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND (session_id IS NULL OR owner_type = 'legacy_unclaimed' OR session_id = ?)
                """,
                (session_id, owner_id or session_id, now, now, note_id, session_id),
            )
            if cursor.rowcount == 0:
                cursor.execute("ROLLBACK")
                return None
            cursor.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    """
                    INSERT INTO note_versions (note_id, version, title, updated_at, tags, links, canonical_blocks, rendered_html)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        note_id, row["version"], row["title"], now,
                        row["tags"], row["links"], row["canonical_blocks"], row["rendered_html"],
                    ),
                )
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
    return get_note_by_id(note_id)


def list_legacy_unclaimed_notes() -> List[Dict[str, Any]]:
    """Returns all notes currently in legacy_unclaimed state or with NULL session_id."""
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT * FROM notes WHERE session_id IS NULL OR owner_type = 'legacy_unclaimed'").fetchall()
    return [_row_to_note(r) for r in rows]


def migrate_legacy_notes_batch(
    owner_id: str = "migration-2026-09-19"
) -> int:
    """Idempotent batch migration for legacy NULL session_id notes."""
    with closing(get_connection()) as conn:
        cur = conn.execute(
            """
            UPDATE notes
            SET owner_type = 'legacy_unclaimed', owner_id = ?
            WHERE session_id IS NULL AND (owner_type IS NULL OR owner_type != 'legacy_unclaimed' OR owner_id IS NULL)
            """,
            (owner_id,),
        )
        conn.commit()
        return cur.rowcount


def rollback_legacy_notes_migration(
    owner_id: str = "migration-2026-09-19"
) -> int:
    """Rolls back notes tagged with the migration owner ID."""
    with closing(get_connection()) as conn:
        cur = conn.execute(
            """
            UPDATE notes
            SET owner_type = 'session', owner_id = NULL
            WHERE owner_id = ? AND session_id IS NULL
            """,
            (owner_id,),
        )
        conn.commit()
        return cur.rowcount


def get_note_history(note_id: str) -> List[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT * FROM note_versions WHERE note_id = ? ORDER BY version DESC", (note_id,)).fetchall()
    return [
        {
            "version": row["version"], "title": row["title"], "updated_at": row["updated_at"],
            "tags": json.loads(row["tags"]), "links": json.loads(row["links"]),
            "canonical_blocks": json.loads(row["canonical_blocks"]), "rendered_html": row["rendered_html"],
        }
        for row in rows
    ]


def list_all_notes(tag: Optional[str] = None) -> List[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT id, title, created_at, updated_at, tags, links, version FROM notes ORDER BY updated_at DESC").fetchall()
    notes = []
    for row in rows:
        tags_list = json.loads(row["tags"])
        if tag and tag not in tags_list:
            continue
        notes.append({
            "id": row["id"], "title": row["title"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "tags": tags_list,
            "links": json.loads(row["links"]), "version": row["version"],
        })
    return notes


def search_notes(query: str) -> List[Dict[str, Any]]:
    like_query = f"%{query}%"
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT id, title, created_at, updated_at, tags, links, version, rendered_html
            FROM notes WHERE title LIKE ? OR tags LIKE ? OR rendered_html LIKE ?
            ORDER BY updated_at DESC
            """,
            (like_query, like_query, like_query),
        ).fetchall()
    return [
        {
            "id": row["id"], "title": row["title"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "tags": json.loads(row["tags"]),
            "links": json.loads(row["links"]), "version": row["version"],
        }
        for row in rows
    ]


def create_chat_session(session_id: str, title: str) -> Dict[str, Any]:
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        conn.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)", (session_id, title, now))
        conn.commit()
    return {"id": session_id, "title": title, "created_at": now}


def get_chat_session(session_id: str) -> Optional[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "title": row["title"], "created_at": row["created_at"]}


def save_chat_message(message_id: str, session_id: str, role: str, content: str) -> Dict[str, Any]:
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (session_id, f"Session {session_id[:8]}", now),
            )
        cursor.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (message_id, session_id, role, content, now),
        )
        conn.commit()
    return {"id": message_id, "session_id": session_id, "role": role, "content": content, "created_at": now}


def get_session_messages(session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        if limit is not None and limit > 0:
            rows = list(reversed(conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()))
        else:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
    return [
        {"id": row["id"], "role": row["role"], "content": row["content"], "created_at": row["created_at"]}
        for row in rows
    ]


# ── Persistent agent memory ────────────────────────────────────────────────
def add_agent_memory(category: str, key: str, value: Optional[str] = None) -> None:
    """Remember one (category, key) fact forever. Idempotent."""
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO agent_memory (category, key, value, created_at) VALUES (?, ?, ?, ?)",
            (category, key, value, now),
        )
        conn.commit()


def remove_agent_memory(category: str, key: str) -> None:
    """Forget one (category, key) fact — e.g. unblock a channel."""
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM agent_memory WHERE category = ? AND key = ?", (category, key))
        conn.commit()


def list_agent_memory(category: str) -> List[Dict[str, Any]]:
    """All remembered facts in a category, newest first."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT category, key, value, created_at FROM agent_memory WHERE category = ? ORDER BY created_at DESC",
            (category,),
        ).fetchall()
    return [dict(r) for r in rows]


_WIDGET_STATE_CAT = "widget_state"


def set_widget_state(key: str, value: str) -> None:
    """Persist (overwriting) a widget's JSON state under a stable purpose key."""
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT INTO agent_memory (category, key, value, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(category, key) DO UPDATE SET value = excluded.value,
                                                     created_at = excluded.created_at
            """,
            (_WIDGET_STATE_CAT, key, value, now),
        )
        conn.commit()


def get_widget_state(key: str) -> Optional[str]:
    """The stored JSON state for a widget purpose key, or None."""
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT value FROM agent_memory WHERE category = ? AND key = ?",
            (_WIDGET_STATE_CAT, key),
        ).fetchone()
    return row["value"] if row and row["value"] is not None else None


def list_widget_states(prefix: str = "") -> List[Dict[str, Any]]:
    """All persisted widget states whose key starts with `prefix`, newest first."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT key, value, created_at FROM agent_memory "
            "WHERE category = ? AND key LIKE ? ORDER BY created_at DESC",
            (_WIDGET_STATE_CAT, f"{prefix}%"),
        ).fetchall()
    return [dict(r) for r in rows]


_USER_PROFILE_CAT = "user_profile"


def set_user_fact(key: str, value: str) -> None:
    """Remember (overwriting) one fact about the user."""
    now = _utc_now_iso()
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT INTO agent_memory (category, key, value, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(category, key) DO UPDATE SET value = excluded.value,
                                                     created_at = excluded.created_at
            """,
            (_USER_PROFILE_CAT, key, value, now),
        )
        conn.commit()


def get_user_facts() -> Dict[str, str]:
    """The whole user profile as a {key: value} dict (empty if nothing known)."""
    with closing(get_connection()) as conn:
        rows = conn.execute("SELECT key, value FROM agent_memory WHERE category = ?", (_USER_PROFILE_CAT,)).fetchall()
    return {r["key"]: r["value"] for r in rows if r["value"] is not None}


def wipe_user_facts() -> int:
    """Forget everything about the user. Returns the number of facts removed."""
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM agent_memory WHERE category = ?", (_USER_PROFILE_CAT,))
        n = cur.rowcount
        conn.commit()
    return n


# ── Watches (standing asks) — see app/services/watches.py ──────────────────


def _watch_row(r) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["spec"] = json.loads(d.get("spec") or "{}")
    except Exception:
        d["spec"] = {}
    return d


def create_watch(row: Dict[str, Any]) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """INSERT INTO watches (id, session_id, kind, spec, label, interval_s, next_run, expires,
                                    created_at, fire_count, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
            (row["id"], row["session_id"], row["kind"], json.dumps(row.get("spec") or {}),
             row.get("label") or "", int(row["interval_s"]), float(row["next_run"]),
             float(row["expires"]), _utc_now_iso()),
        )
        conn.commit()


def get_watch(watch_id: str) -> Optional[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        r = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return _watch_row(r) if r else None


def list_watches(session_id: str) -> List[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM watches WHERE session_id = ? AND active = 1 ORDER BY created_at",
            (session_id,),
        ).fetchall()
    return [_watch_row(r) for r in rows]


def count_watches() -> int:
    with closing(get_connection()) as conn:
        n = conn.execute("SELECT COUNT(*) FROM watches WHERE active = 1").fetchone()[0]
    return int(n or 0)


def due_watches(now: float) -> List[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM watches WHERE active = 1 AND next_run <= ? AND expires > ? ORDER BY next_run",
            (float(now), float(now)),
        ).fetchall()
    return [_watch_row(r) for r in rows]


def mark_watch_run(watch_id: str, next_run: float, last_fp: Optional[str], fired: bool) -> None:
    with closing(get_connection()) as conn:
        if fired:
            conn.execute(
                "UPDATE watches SET next_run = ?, last_fp = ?, last_fired = ?, fire_count = fire_count + 1 WHERE id = ?",
                (float(next_run), last_fp, time.time(), watch_id),
            )
        else:
            conn.execute("UPDATE watches SET next_run = ?, last_fp = ? WHERE id = ?", (float(next_run), last_fp, watch_id))
        conn.commit()


def delete_watch(watch_id: str, session_id: str) -> bool:
    """Session-scoped: a watch can only be cancelled by the session that owns it."""
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM watches WHERE id = ? AND session_id = ?", (watch_id, session_id))
        conn.commit()
        return cur.rowcount > 0


def expire_watches(now: float) -> int:
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM watches WHERE expires <= ?", (float(now),))
        conn.commit()
        return cur.rowcount


# ── Research Protocol Ledger Helpers ─────────────────────────────────────────


def save_research_run(run: Dict[str, Any]) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO research_runs (
                run_id, session_id, message_id, mode, status, intent_json, budget_json,
                created_at, foreground_deadline_at, background_deadline_at, completed_at, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"], run["session_id"], run["message_id"],
                run["intent"]["mode"] if isinstance(run.get("intent"), dict) else str(run.get("mode", "")),
                run.get("status", "foreground_running"),
                json.dumps(run.get("intent", {})), json.dumps(run.get("budget", {})),
                run.get("created_at", _utc_now_iso()),
                run.get("foreground_deadline_at"), run.get("background_deadline_at"),
                run.get("completed_at"), json.dumps(run.get("metrics", {})),
            ),
        )
        conn.commit()


def update_research_run_status(run_id: str, status: str, completed_at: Optional[str] = None,
                               metrics: Optional[Dict[str, Any]] = None) -> None:
    with closing(get_connection()) as conn:
        if metrics:
            conn.execute(
                "UPDATE research_runs SET status = ?, completed_at = COALESCE(?, completed_at), metrics_json = ? WHERE run_id = ?",
                (status, completed_at, json.dumps(metrics), run_id),
            )
        else:
            conn.execute(
                "UPDATE research_runs SET status = ?, completed_at = COALESCE(?, completed_at) WHERE run_id = ?",
                (status, completed_at, run_id),
            )
        conn.commit()


def save_research_task(task: Dict[str, Any], run_id: str) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO research_tasks (
                task_id, run_id, task_type, worker_id, state, started_at, completed_at,
                deadline_ms, cache_hit, evidence_ids, error_class, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["task_id"], run_id, task.get("task_type", ""), task.get("worker_id", ""),
                task.get("state", "pending"), task.get("started_at"), task.get("completed_at"),
                task.get("deadline_ms", 5000), 1 if task.get("cache_hit") else 0,
                json.dumps(task.get("evidence_ids", [])), task.get("error_class"),
                json.dumps(task.get("details", {})),
            ),
        )
        conn.commit()


def save_research_evidence(item: Dict[str, Any], run_id: str) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO research_evidence (
                evidence_id, run_id, url, canonical_url, title, publisher, tier,
                published_at, extracted_text, quality_score, freshness, source_provider, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["evidence_id"], run_id, item.get("url", ""), item.get("canonical_url", ""),
                item.get("title", ""), item.get("publisher", ""), item.get("tier", "secondary"),
                item.get("published_at"), item.get("extracted_text", ""),
                float(item.get("quality_score", 1.0)), item.get("freshness", "fresh"),
                item.get("source_provider", ""),
                json.dumps({
                    "age_seconds": item.get("age_seconds"),
                    "related_tickers": item.get("related_tickers", []),
                    "entity_relevance": item.get("entity_relevance", 1.0),
                }),
            ),
        )
        conn.commit()


def save_research_answer_version(answer: Dict[str, Any], run_id: str) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO research_answer_versions (run_id, version, status, text, evidence_ids, delta_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, int(answer.get("version", 1)), answer.get("status", "preliminary"),
                answer.get("text", ""), json.dumps(answer.get("evidence_ids", [])),
                answer.get("delta_summary"), answer.get("created_at", _utc_now_iso()),
            ),
        )
        conn.commit()


def get_research_run_full(run_id: str) -> Optional[Dict[str, Any]]:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        run_row = cur.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run_row:
            return None
        task_rows = cur.execute("SELECT * FROM research_tasks WHERE run_id = ?", (run_id,)).fetchall()
        evidence_rows = cur.execute("SELECT * FROM research_evidence WHERE run_id = ?", (run_id,)).fetchall()
        answer_rows = cur.execute("SELECT * FROM research_answer_versions WHERE run_id = ? ORDER BY version ASC", (run_id,)).fetchall()
    return {
        "run_id": run_row["run_id"], "session_id": run_row["session_id"],
        "message_id": run_row["message_id"], "mode": run_row["mode"],
        "status": run_row["status"], "intent": json.loads(run_row["intent_json"]),
        "budget": json.loads(run_row["budget_json"]), "created_at": run_row["created_at"],
        "foreground_deadline_at": run_row["foreground_deadline_at"],
        "background_deadline_at": run_row["background_deadline_at"],
        "completed_at": run_row["completed_at"],
        "metrics": json.loads(run_row["metrics_json"] or "{}"),
        "tasks": [dict(t) for t in task_rows],
        "evidence": [dict(e) for e in evidence_rows],
        "answer_versions": [dict(a) for a in answer_rows],
    }

