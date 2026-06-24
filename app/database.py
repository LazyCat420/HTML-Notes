import sqlite3
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
    
    # Merge values
    updated_title = title if title is not None else note["title"]
    updated_tags = tags if tags is not None else note["tags"]
    updated_links = links if links is not None else note["links"]
    updated_blocks = canonical_blocks if canonical_blocks is not None else note["canonical_blocks"]
    updated_html = rendered_html if rendered_html is not None else note["rendered_html"]
    
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
