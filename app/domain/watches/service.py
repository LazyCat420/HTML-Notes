import logging
from typing import Any, Dict, List, Optional
from app import database
from app.services.watches import create_watch as create_watch_svc, WATCH_KINDS

logger = logging.getLogger(__name__)


class WatchesDomainService:
    """
    Domain service for background watches and reactive triggers.
    Enforces session scoping and mandatory lifetime / expiry rules.
    """

    @staticmethod
    def create_watch(
        session_id: str,
        kind: str,
        spec: Dict[str, Any],
        label: str = "",
        interval_s: Optional[int] = None,
        expires: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Creates a new background watch scoped to session_id with mandatory expiry.
        """
        if not session_id:
            return {"error": "Missing mandatory session_id for watch creation", "is_error": True}

        if kind not in WATCH_KINDS:
            return {
                "error": f"Invalid watch kind '{kind}'. Must be one of {list(WATCH_KINDS.keys())}",
                "is_error": True
            }

        res = create_watch_svc(
            session_id=session_id,
            kind=kind,
            spec=spec or {},
            label=label,
            interval_s=interval_s
        )
        if not res:
            return {
                "error": f"Watch creation refused (session or global limit reached for {session_id})",
                "is_error": True
            }

        return {
            "success": True,
            "watch": res,
            "watch_id": res["id"],
            "expires": res.get("expires")
        }

    @staticmethod
    def list_watches(session_id: str) -> Dict[str, Any]:
        """
        Lists all active watches scoped to session_id.
        """
        if not session_id:
            return {"error": "Missing mandatory session_id to list watches", "is_error": True}
        watches = database.list_watches(session_id)
        return {
            "watches": watches,
            "count": len(watches)
        }

    @staticmethod
    def cancel_watch(watch_id: str, session_id: str) -> Dict[str, Any]:
        """
        Cancels a background watch. Rejects if watch belongs to another session.
        """
        if not session_id:
            return {"error": "Missing mandatory session_id for watch cancellation", "is_error": True}

        watch = database.get_watch(watch_id)
        if not watch:
            return {"error": f"Watch '{watch_id}' not found", "is_error": True}

        if watch.get("session_id") != session_id:
            return {
                "error": f"Unauthorized: watch '{watch_id}' belongs to another session",
                "is_error": True
            }

        deleted = database.delete_watch(watch_id, session_id)
        return {
            "success": deleted,
            "removed": watch_id
        }

    @staticmethod
    def get_watch(watch_id: str) -> Optional[Dict[str, Any]]:
        return database.get_watch(watch_id)


watches_service = WatchesDomainService()
