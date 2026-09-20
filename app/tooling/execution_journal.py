"""Persistent exactly-once boundary for runtime-admitted local mutations."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app import database


@dataclass(frozen=True)
class ClaimResult:
    state: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ExecutionJournal:
    def _ensure(self, conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_tool_executions (
                app_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                nonce TEXT NOT NULL UNIQUE,
                tool_id TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                PRIMARY KEY (app_id, session_id, run_id, tool_call_id)
            )
        """)

    def claim(self, *, app_id: str, session_id: str, run_id: str, tool_call_id: str,
              nonce: str, tool_id: str, arguments_hash: str) -> ClaimResult:
        conn = database.get_connection()
        try:
            self._ensure(conn)
            now = datetime.now(timezone.utc).isoformat()
            try:
                conn.execute(
                    "INSERT INTO runtime_tool_executions "
                    "(app_id,session_id,run_id,tool_call_id,nonce,tool_id,arguments_hash,state,created_at) "
                    "VALUES (?,?,?,?,?,?,?,'pending',?)",
                    (app_id, session_id, run_id, tool_call_id, nonce, tool_id, arguments_hash, now),
                )
                conn.commit()
                return ClaimResult("claimed")
            except Exception:
                conn.rollback()
            row = conn.execute(
                "SELECT nonce,tool_id,arguments_hash,state,result_json FROM runtime_tool_executions "
                "WHERE app_id=? AND session_id=? AND run_id=? AND tool_call_id=?",
                (app_id, session_id, run_id, tool_call_id),
            ).fetchone()
            if row is None:
                nonce_row = conn.execute(
                    "SELECT 1 FROM runtime_tool_executions WHERE nonce=?", (nonce,),
                ).fetchone()
                if nonce_row is not None:
                    return ClaimResult("replayed", error="Authorization nonce was already used by another tool call")
                return ClaimResult("conflict", error="Persistent execution claim could not be created")
            if row["nonce"] != nonce or row["tool_id"] != tool_id or row["arguments_hash"] != arguments_hash:
                return ClaimResult("conflict", error="Tool call identity was reused with different authorization or arguments")
            if row["state"] == "completed" and row["result_json"]:
                return ClaimResult("completed", result=json.loads(row["result_json"]))
            return ClaimResult("pending", error="A prior mutation may have executed before its outcome was durably recorded")
        finally:
            conn.close()

    def complete(self, *, app_id: str, session_id: str, run_id: str, tool_call_id: str,
                 result: Dict[str, Any]) -> None:
        conn = database.get_connection()
        try:
            self._ensure(conn)
            updated = conn.execute(
                "UPDATE runtime_tool_executions SET state='completed',result_json=?,completed_at=? "
                "WHERE app_id=? AND session_id=? AND run_id=? AND tool_call_id=? AND state='pending'",
                (json.dumps(result, sort_keys=True, separators=(",", ":"), default=str),
                 datetime.now(timezone.utc).isoformat(), app_id, session_id, run_id, tool_call_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("Persistent tool execution claim was lost before completion")
            conn.commit()
        finally:
            conn.close()


execution_journal = ExecutionJournal()
