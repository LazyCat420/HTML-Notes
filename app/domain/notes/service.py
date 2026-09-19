import uuid
import logging
from typing import Any, Dict, List, Optional
from app import database
from app.agents.auditor import audit_html_fragment

logger = logging.getLogger(__name__)


class NotesDomainService:
    """
    Domain service for Notes CRUD, link relationships, and HTML fragment validation.
    """

    @staticmethod
    def create_note(
        title: str,
        rendered_html: str,
        tags: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        audit = audit_html_fragment(rendered_html or "")
        if not audit.get("is_valid"):
            return {
                "error": f"HTML audit failed: {audit.get('errors')}",
                "is_error": True
            }

        note_id = f"note_{uuid.uuid4().hex[:8]}"
        note = database.create_note(
            note_id=note_id,
            title=title,
            tags=tags or [],
            links=links or [],
            source_messages=["tool-call"],
            canonical_blocks=[],
            rendered_html=rendered_html,
            session_id=session_id
        )
        return {
            "success": True,
            "note_id": note["id"],
            "title": note["title"],
            "session_id": note.get("session_id")
        }

    @staticmethod
    def update_note(note_id: str, session_id: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
        existing = database.get_note_by_id(note_id)
        if not existing:
            return {"error": f"Note '{note_id}' not found", "is_error": True}

        # Legacy unclaimed note check
        owner_type = existing.get("owner_type")
        note_session = existing.get("session_id")
        if owner_type == "legacy_unclaimed" or (note_session is None and owner_type != "session"):
            return {
                "error": f"Unauthorized: note '{note_id}' is legacy unclaimed and must be claimed before updating",
                "is_error": True,
                "code": "NOTE_UNCLAIMED"
            }

        # Cross-session isolation check
        if note_session and session_id and note_session != session_id:
            return {
                "error": f"Unauthorized: note '{note_id}' belongs to another session",
                "is_error": True,
                "code": "NOTE_SESSION_MISMATCH"
            }

        if "rendered_html" in fields and fields["rendered_html"]:
            audit = audit_html_fragment(fields["rendered_html"])
            if not audit.get("is_valid"):
                return {
                    "error": f"HTML audit failed: {audit.get('errors')}",
                    "is_error": True
                }

        note = database.update_note(note_id=note_id, **{k: v for k, v in fields.items() if k not in ("note_id", "session_id")})
        if not note:
            return {"error": f"Note '{note_id}' not found", "is_error": True}
        return {"success": True, "note_id": note_id}

    @staticmethod
    def claim_note(note_id: str, session_id: str, owner_id: Optional[str] = None) -> Dict[str, Any]:
        """Explicitly claims an unclaimed or legacy note, binding it to the current session/owner."""
        existing = database.get_note_by_id(note_id)
        if not existing:
            return {"error": f"Note '{note_id}' not found", "is_error": True}

        # If already claimed by another session, reject
        current_session = existing.get("session_id")
        if current_session and current_session != session_id:
            return {
                "error": f"Unauthorized: note '{note_id}' is already claimed by another session",
                "is_error": True,
                "code": "NOTE_ALREADY_CLAIMED"
            }

        claimed = database.claim_note(note_id=note_id, session_id=session_id, owner_id=owner_id)
        if not claimed:
            return {"error": f"Failed to claim note '{note_id}'", "is_error": True}

        return {
            "success": True,
            "note_id": note_id,
            "session_id": session_id,
            "owner_id": claimed.get("owner_id"),
            "claimed_at": claimed.get("claimed_at"),
            "version": claimed.get("version"),
        }

    @staticmethod
    def get_note(note_id: str) -> Dict[str, Any]:
        note = database.get_note_by_id(note_id)
        if not note:
            return {"error": f"Note '{note_id}' not found", "is_error": True}
        return note

    @staticmethod
    def search_notes(query: str) -> Dict[str, Any]:
        results = database.search_notes(query or "")
        return {"results": results, "count": len(results)}

    @staticmethod
    def link_notes(source_note_id: str, target_note_id: str) -> Dict[str, Any]:
        note_a = database.get_note_by_id(source_note_id)
        if not note_a:
            return {"error": f"Source note '{source_note_id}' not found", "is_error": True}

        links = list(note_a.get("links") or [])
        if target_note_id not in links:
            links.append(target_note_id)
            database.update_note(note_id=source_note_id, links=links)
        return {"success": True, "source": source_note_id, "target": target_note_id}

notes_service = NotesDomainService()
