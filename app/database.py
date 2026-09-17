import sqlite3
import time
import json
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import DATABASE_URL

def get_connection():
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
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
        version INTEGER NOT NULL DEFAULT 1
    );
    """)
    
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

    conn.commit()
    conn.close()

# Initialize DB on load
init_db()

# DB Functions for Notes

def create_note(
    note_id: str,
    title: str,
    tags: List[str],
    links: List[str],
    source_messages: List[str],
    canonical_blocks: List[Dict[str, Any]],
    rendered_html: str
) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        """
        INSERT INTO notes (id, title, created_at, updated_at, tags, links, source_messages, canonical_blocks, rendered_html, version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            note_id,
            title,
            now,
            now,
            json.dumps(tags),
            json.dumps(links),
            json.dumps(source_messages),
            json.dumps(canonical_blocks),
            rendered_html
        )
    )
    
    # Save the first version to note_versions
    cursor.execute(
        """
        INSERT INTO note_versions (note_id, version, title, updated_at, tags, links, canonical_blocks, rendered_html)
        VALUES (?, 1, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            title,
            now,
            json.dumps(tags),
            json.dumps(links),
            json.dumps(canonical_blocks),
            rendered_html
        )
    )
    
    conn.commit()
    conn.close()
    
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
    note = get_note_by_id(note_id)
    if not note:
        return None
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # Increment version
    new_version = note["version"] + 1
    now = datetime.utcnow().isoformat()
    
    # Merge values. title/rendered_html additionally treat empty/whitespace as
    # "keep existing" — an update carrying "" used to clobber real content and
    # write an empty note_versions row (last line of defense; the auditor now
    # rejects empty fragments upstream too).
    updated_title = title if title is not None and str(title).strip() else note["title"]
    updated_tags = tags if tags is not None else note["tags"]
    updated_links = links if links is not None else note["links"]
    updated_blocks = canonical_blocks if canonical_blocks is not None else note["canonical_blocks"]
    updated_html = rendered_html if rendered_html is not None and str(rendered_html).strip() else note["rendered_html"]
    
    source_messages = note["source_messages"]
    if source_message and source_message not in source_messages:
        source_messages.append(source_message)
        
    cursor.execute(
        """
        UPDATE notes
        SET title = ?, updated_at = ?, tags = ?, links = ?, source_messages = ?, canonical_blocks = ?, rendered_html = ?, version = ?
        WHERE id = ?
        """,
        (
            updated_title,
            now,
            json.dumps(updated_tags),
            json.dumps(updated_links),
            json.dumps(source_messages),
            json.dumps(updated_blocks),
            updated_html,
            new_version,
            note_id
        )
    )
    
    # Save the new version
    cursor.execute(
        """
        INSERT INTO note_versions (note_id, version, title, updated_at, tags, links, canonical_blocks, rendered_html)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            new_version,
            updated_title,
            now,
            json.dumps(updated_tags),
            json.dumps(updated_links),
            json.dumps(updated_blocks),
            updated_html
        )
    )
    
    conn.commit()
    conn.close()
    
    return get_note_by_id(note_id)

def get_note_by_id(note_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "tags": json.loads(row["tags"]),
        "links": json.loads(row["links"]),
        "source_messages": json.loads(row["source_messages"]),
        "canonical_blocks": json.loads(row["canonical_blocks"]),
        "rendered_html": row["rendered_html"],
        "version": row["version"]
    }

def get_note_history(note_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM note_versions WHERE note_id = ? ORDER BY version DESC",
        (note_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for row in rows:
        history.append({
            "version": row["version"],
            "title": row["title"],
            "updated_at": row["updated_at"],
            "tags": json.loads(row["tags"]),
            "links": json.loads(row["links"]),
            "canonical_blocks": json.loads(row["canonical_blocks"]),
            "rendered_html": row["rendered_html"]
        })
    return history

def list_all_notes(tag: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    if tag:
        cursor.execute("SELECT * FROM notes ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        notes = []
        for row in rows:
            tags_list = json.loads(row["tags"])
            if tag in tags_list:
                notes.append({
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "tags": tags_list,
                    "links": json.loads(row["links"]),
                    "version": row["version"]
                })
    else:
        cursor.execute("SELECT id, title, created_at, updated_at, tags, links, version FROM notes ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        notes = []
        for row in rows:
            notes.append({
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "tags": json.loads(row["tags"]),
                "links": json.loads(row["links"]),
                "version": row["version"]
            })
    conn.close()
    return notes

def search_notes(query: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    # Simple like query across title, tags, and rendered_html
    like_query = f"%{query}%"
    cursor.execute(
        """
        SELECT id, title, created_at, updated_at, tags, links, version, rendered_html
        FROM notes
        WHERE title LIKE ? OR tags LIKE ? OR rendered_html LIKE ?
        ORDER BY updated_at DESC
        """,
        (like_query, like_query, like_query)
    )
    rows = cursor.fetchall()
    conn.close()
    
    notes = []
    for row in rows:
        notes.append({
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "tags": json.loads(row["tags"]),
            "links": json.loads(row["links"]),
            "version": row["version"]
        })
    return notes

# DB Functions for Chat Sessions & Messages

def create_chat_session(session_id: str, title: str) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
        (session_id, title, now)
    )
    conn.commit()
    conn.close()
    return {"id": session_id, "title": title, "created_at": now}

def get_chat_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row["id"], "title": row["title"], "created_at": row["created_at"]}

def save_chat_message(message_id: str, session_id: str, role: str, content: str) -> Dict[str, Any]:
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    
    # Ensure session exists
    cursor.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
    if not cursor.fetchone():
        # Auto-create session
        cursor.execute(
            "INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
            (session_id, f"Session {session_id[:8]}", now)
        )
        
    cursor.execute(
        """
        INSERT INTO chat_messages (id, session_id, role, content, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_id, session_id, role, content, now)
    )
    conn.commit()
    conn.close()
    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "created_at": now
    }

def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for row in rows:
        messages.append({
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"]
        })
    return messages


# ── Persistent agent memory ────────────────────────────────────────────────
def add_agent_memory(category: str, key: str, value: Optional[str] = None) -> None:
    """Remember one (category, key) fact forever. Idempotent — re-adding the same
    key just refreshes its value/timestamp instead of erroring on the UNIQUE."""
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO agent_memory (category, key, value, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (category, key, value, now),
    )
    conn.commit()
    conn.close()


def remove_agent_memory(category: str, key: str) -> None:
    """Forget one (category, key) fact — e.g. unblock a channel."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM agent_memory WHERE category = ? AND key = ?", (category, key)
    )
    conn.commit()
    conn.close()


def list_agent_memory(category: str) -> List[Dict[str, Any]]:
    """All remembered facts in a category, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT category, key, value, created_at FROM agent_memory "
        "WHERE category = ? ORDER BY created_at DESC",
        (category,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Persistent widget state ─────────────────────────────────────────────────
# A closed widget's structured state, keyed by purpose (e.g. 'list:grocery-list')
# so it can be restored after the widget is closed, the session ends, or the
# server restarts. Stored in agent_memory under category 'widget_state'. Unlike
# add_agent_memory (INSERT OR IGNORE — keeps the FIRST value, for the blocklist),
# this OVERWRITES in place via upsert, so restoring/editing never bloats the DB.
_WIDGET_STATE_CAT = "widget_state"


def set_widget_state(key: str, value: str) -> None:
    """Persist (overwriting) a widget's JSON state under a stable purpose key."""
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO agent_memory (category, key, value, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category, key) DO UPDATE SET value = excluded.value,
                                                 created_at = excluded.created_at
        """,
        (_WIDGET_STATE_CAT, key, value, now),
    )
    conn.commit()
    conn.close()


def get_widget_state(key: str) -> Optional[str]:
    """The stored JSON state for a widget purpose key, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value FROM agent_memory WHERE category = ? AND key = ?",
        (_WIDGET_STATE_CAT, key),
    )
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row and row["value"] is not None else None


def list_widget_states(prefix: str = "") -> List[Dict[str, Any]]:
    """All persisted widget states whose key starts with `prefix`, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT key, value, created_at FROM agent_memory "
        "WHERE category = ? AND key LIKE ? ORDER BY created_at DESC",
        (_WIDGET_STATE_CAT, f"{prefix}%"),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Persistent user profile ─────────────────────────────────────────────────
# What the user tells us about themselves ("I'm from Seattle", "my name is Alex",
# "I like jazz"). Stored in agent_memory under category 'user_profile', keyed by
# fact (name/location/likes), OVERWRITE-in-place (a changed city must replace the
# old one — so upsert, NOT add_agent_memory's INSERT OR IGNORE). Global + durable.
_USER_PROFILE_CAT = "user_profile"


def set_user_fact(key: str, value: str) -> None:
    """Remember (overwriting) one fact about the user, e.g. set_user_fact('location','Seattle')."""
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO agent_memory (category, key, value, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category, key) DO UPDATE SET value = excluded.value,
                                                 created_at = excluded.created_at
        """,
        (_USER_PROFILE_CAT, key, value, now),
    )
    conn.commit()
    conn.close()


def get_user_facts() -> Dict[str, str]:
    """The whole user profile as a {key: value} dict (empty if nothing known)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT key, value FROM agent_memory WHERE category = ?",
        (_USER_PROFILE_CAT,),
    )
    rows = cursor.fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows if r["value"] is not None}


def wipe_user_facts() -> int:
    """Forget everything about the user. Returns the number of facts removed."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM agent_memory WHERE category = ?", (_USER_PROFILE_CAT,))
    n = cursor.rowcount
    conn.commit()
    conn.close()
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
    conn = get_connection()
    conn.execute(
        """INSERT INTO watches (id, session_id, kind, spec, label, interval_s, next_run, expires,
                                created_at, fire_count, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
        (row["id"], row["session_id"], row["kind"], json.dumps(row.get("spec") or {}),
         row.get("label") or "", int(row["interval_s"]), float(row["next_run"]),
         float(row["expires"]), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_watch(watch_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    r = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    conn.close()
    return _watch_row(r) if r else None


def list_watches(session_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM watches WHERE session_id = ? AND active = 1 ORDER BY created_at",
                        (session_id,)).fetchall()
    conn.close()
    return [_watch_row(r) for r in rows]


def count_watches() -> int:
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) FROM watches WHERE active = 1").fetchone()[0]
    conn.close()
    return int(n or 0)


def due_watches(now: float) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM watches WHERE active = 1 AND next_run <= ? AND expires > ? "
                        "ORDER BY next_run", (float(now), float(now))).fetchall()
    conn.close()
    return [_watch_row(r) for r in rows]


def mark_watch_run(watch_id: str, next_run: float, last_fp: Optional[str], fired: bool) -> None:
    conn = get_connection()
    if fired:
        conn.execute("UPDATE watches SET next_run = ?, last_fp = ?, last_fired = ?, "
                     "fire_count = fire_count + 1 WHERE id = ?",
                     (float(next_run), last_fp, time.time(), watch_id))
    else:
        conn.execute("UPDATE watches SET next_run = ?, last_fp = ? WHERE id = ?",
                     (float(next_run), last_fp, watch_id))
    conn.commit()
    conn.close()


def delete_watch(watch_id: str, session_id: str) -> bool:
    """Session-scoped: a watch can only be cancelled by the session that owns it."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM watches WHERE id = ? AND session_id = ?", (watch_id, session_id))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n > 0


def expire_watches(now: float) -> int:
    conn = get_connection()
    cur = conn.execute("DELETE FROM watches WHERE expires <= ?", (float(now),))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


# ── Research Protocol Ledger Helpers ─────────────────────────────────────────

def save_research_run(run: Dict[str, Any]) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO research_runs (
        run_id, session_id, message_id, mode, status, intent_json, budget_json,
        created_at, foreground_deadline_at, background_deadline_at, completed_at, metrics_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run["run_id"],
        run["session_id"],
        run["message_id"],
        run["intent"]["mode"] if isinstance(run.get("intent"), dict) else str(run.get("mode", "")),
        run.get("status", "foreground_running"),
        json.dumps(run.get("intent", {})),
        json.dumps(run.get("budget", {})),
        run.get("created_at", datetime.utcnow().isoformat()),
        run.get("foreground_deadline_at"),
        run.get("background_deadline_at"),
        run.get("completed_at"),
        json.dumps(run.get("metrics", {}))
    ))
    conn.commit()
    conn.close()


def update_research_run_status(run_id: str, status: str, completed_at: Optional[str] = None,
                               metrics: Optional[Dict[str, Any]] = None) -> None:
    conn = get_connection()
    cur = conn.cursor()
    if metrics:
        cur.execute("""
        UPDATE research_runs SET status = ?, completed_at = COALESCE(?, completed_at),
        metrics_json = ? WHERE run_id = ?
        """, (status, completed_at, json.dumps(metrics), run_id))
    else:
        cur.execute("""
        UPDATE research_runs SET status = ?, completed_at = COALESCE(?, completed_at)
        WHERE run_id = ?
        """, (status, completed_at, run_id))
    conn.commit()
    conn.close()


def save_research_task(task: Dict[str, Any], run_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO research_tasks (
        task_id, run_id, task_type, worker_id, state, started_at, completed_at,
        deadline_ms, cache_hit, evidence_ids, error_class, details_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task["task_id"],
        run_id,
        task.get("task_type", ""),
        task.get("worker_id", ""),
        task.get("state", "pending"),
        task.get("started_at"),
        task.get("completed_at"),
        task.get("deadline_ms", 5000),
        1 if task.get("cache_hit") else 0,
        json.dumps(task.get("evidence_ids", [])),
        task.get("error_class"),
        json.dumps(task.get("details", {}))
    ))
    conn.commit()
    conn.close()


def save_research_evidence(item: Dict[str, Any], run_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO research_evidence (
        evidence_id, run_id, url, canonical_url, title, publisher, tier,
        published_at, extracted_text, quality_score, freshness, source_provider, metadata_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item["evidence_id"],
        run_id,
        item.get("url", ""),
        item.get("canonical_url", ""),
        item.get("title", ""),
        item.get("publisher", ""),
        item.get("tier", "secondary"),
        item.get("published_at"),
        item.get("extracted_text", ""),
        float(item.get("quality_score", 1.0)),
        item.get("freshness", "fresh"),
        item.get("source_provider", ""),
        json.dumps({
            "age_seconds": item.get("age_seconds"),
            "related_tickers": item.get("related_tickers", []),
            "entity_relevance": item.get("entity_relevance", 1.0),
        })
    ))
    conn.commit()
    conn.close()


def save_research_answer_version(answer: Dict[str, Any], run_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO research_answer_versions (
        run_id, version, status, text, evidence_ids, delta_summary, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        int(answer.get("version", 1)),
        answer.get("status", "preliminary"),
        answer.get("text", ""),
        json.dumps(answer.get("evidence_ids", [])),
        answer.get("delta_summary"),
        answer.get("created_at", datetime.utcnow().isoformat())
    ))
    conn.commit()
    conn.close()


def get_research_run_full(run_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,))
    run_row = cur.fetchone()
    if not run_row:
        conn.close()
        return None

    cur.execute("SELECT * FROM research_tasks WHERE run_id = ?", (run_id,))
    task_rows = cur.fetchall()

    cur.execute("SELECT * FROM research_evidence WHERE run_id = ?", (run_id,))
    evidence_rows = cur.fetchall()

    cur.execute("SELECT * FROM research_answer_versions WHERE run_id = ? ORDER BY version ASC", (run_id,))
    answer_rows = cur.fetchall()

    conn.close()

    return {
        "run_id": run_row["run_id"],
        "session_id": run_row["session_id"],
        "message_id": run_row["message_id"],
        "mode": run_row["mode"],
        "status": run_row["status"],
        "intent": json.loads(run_row["intent_json"]),
        "budget": json.loads(run_row["budget_json"]),
        "created_at": run_row["created_at"],
        "foreground_deadline_at": run_row["foreground_deadline_at"],
        "background_deadline_at": run_row["background_deadline_at"],
        "completed_at": run_row["completed_at"],
        "metrics": json.loads(run_row["metrics_json"] or "{}"),
        "tasks": [dict(t) for t in task_rows],
        "evidence": [dict(e) for e in evidence_rows],
        "answer_versions": [dict(a) for a in answer_rows],
    }

