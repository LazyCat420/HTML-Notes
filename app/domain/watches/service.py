import logging
from typing import Any, Dict, List
from app.services.watches import list_watches, get_watch, set_watch, delete_watch

logger = logging.getLogger(__name__)


class WatchesDomainService:
    """
    Domain service for background watches and triggers.
    """

    @staticmethod
    def list_all_watches() -> List[Dict[str, Any]]:
        return list_watches()

    @staticmethod
    def get_watch_by_id(watch_id: str) -> Dict[str, Any]:
        watch = get_watch(watch_id)
        if not watch:
            return {"error": f"Watch '{watch_id}' not found", "is_error": True}
        return watch

    @staticmethod
    def upsert_watch(watch_id: str, watch_data: Dict[str, Any]) -> Dict[str, Any]:
        set_watch(watch_id, watch_data)
        return {"success": True, "watch_id": watch_id}

    @staticmethod
    def remove_watch(watch_id: str) -> Dict[str, Any]:
        delete_watch(watch_id)
        return {"success": True, "removed": watch_id}

watches_service = WatchesDomainService()
