"""Session LRU eviction and per-session canvas dismiss/restore stack helpers."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from bs4 import BeautifulSoup

_DISMISSED_STACK_CAP = 10


def sanitize_widget_id(raw_id: Any) -> str:
    """Restrict widget ids to [A-Za-z0-9_-] so an id can never inject DOM attributes."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", str(raw_id or "").lstrip("#").strip()).strip("-")[:80]
    return cleaned or f"widget-{uuid.uuid4().hex[:8]}"


def resolve_stream_widget_id(
    session_id: str,
    widget_type: str,
    id_prefix: str,
    explicit_id: str | None,
    message: str,
    title: str,
    *,
    focus_id: str | None,
    finders: dict,
) -> str:
    """Resolve an existing target id untouched, else sanitize a new/explicit id."""
    existing = finders["on_canvas"](session_id, focus_id or "", widget_type)
    if existing:
        return existing
    if explicit_id:
        return sanitize_widget_id(explicit_id)
    reused = finders["reuse_target"](session_id, widget_type, message, subject=title)
    if reused:
        return reused
    if id_prefix in finders["singleton_prefixes"]:
        by_prefix = finders["by_prefix"](session_id, id_prefix)
        if by_prefix:
            return by_prefix
    return sanitize_widget_id(f"{id_prefix}-{uuid.uuid4().hex[:8]}")


def _evictable_session(cm, sid: str) -> bool:
    """True when session `sid` has no inflight turn and no held canvas lock."""
    if cm._session_inflight.get(sid, 0) > 0:
        return False
    lock = cm._session_locks.get(sid)
    return lock is None or not lock.locked()


def _prune_session_maps(max_sessions: int = 200) -> None:
    """Bound per-session in-memory caches while sparing active sessions."""
    import app.canvas_manager as cm

    if len(cm._session_lru) <= max_sessions:
        return
    ordered = sorted(cm._session_lru.items(), key=lambda kv: kv[1])
    overflow = len(ordered) - max_sessions
    for sid, _ts in ordered:
        if overflow <= 0:
            break
        if not _evictable_session(cm, sid):
            continue
        cm._session_lru.pop(sid, None)
        cm._session_canvas.pop(sid, None)
        cm._session_canvas_version.pop(sid, None)
        cm._session_locks.pop(sid, None)
        cm._session_dismissed_widgets.pop(sid, None)
        overflow -= 1


def list_dismissed_widgets(session_id: str) -> list:
    """Return metadata for recently closed widgets in this session (newest first)."""
    import app.canvas_manager as cm

    stack = cm._session_dismissed_widgets.get(session_id) or []
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "widget_type": item["widget_type"],
            "dismissed_at": item["dismissed_at"],
        }
        for item in reversed(stack)
    ]


def _extract_widget_title(target, fallback_id: str) -> str:
    title = (target.get("data-widget-title") or "").strip()
    if title:
        return title[:80]
    heading = target.find(["h2", "h3", "h4"])
    return (heading.get_text(" ", strip=True) if heading else fallback_id)[:80]


async def dismiss_canvas_widget(session_id: str, widget_id: str) -> dict:
    """Remove a widget from the session canvas and push it onto a bounded undo stack."""
    import app.canvas_manager as cm

    wid = sanitize_widget_id(widget_id)
    removed = False
    async with cm._canvas_lock(session_id):
        html = cm.get_session_canvas(session_id) or ""
        soup = BeautifulSoup(html, "html.parser")
        target = soup.find(id=wid)
        if target is not None:
            stack = cm._session_dismissed_widgets.setdefault(session_id, [])
            stack[:] = [item for item in stack if item.get("id") != wid]
            stack.append({
                "id": wid,
                "title": _extract_widget_title(target, wid),
                "widget_type": (target.get("data-widget-type") or "widget").strip(),
                "html": str(target),
                "dismissed_at": time.time(),
            })
            if len(stack) > _DISMISSED_STACK_CAP:
                del stack[:-_DISMISSED_STACK_CAP]
            target.decompose()
            version = cm.set_session_canvas(session_id, str(soup), bump_version=True)
            removed = True
        else:
            version = cm._session_canvas_version.get(session_id, 0)
            cm._session_lru[session_id] = time.time()
    return {
        "ok": True,
        "removed": removed,
        "widget_id": wid,
        "version": version,
        "closed": list_dismissed_widgets(session_id),
    }


def _pop_dismissed_entry(stack: list, target_id: str) -> dict:
    """Pop matching widget id or top entry from a non-empty dismissed stack."""
    if not target_id:
        return stack.pop()
    for idx in range(len(stack) - 1, -1, -1):
        if stack[idx].get("id") == target_id:
            return stack.pop(idx)
    raise KeyError(f"dismissed widget not found: {target_id}")


def _prepend_restored_widget(soup: BeautifulSoup, entry: dict) -> None:
    """Insert restored widget HTML at the top of #dashboard-grid if not already present."""
    grid = soup.find(id="dashboard-grid")
    if grid is None:
        grid = soup.new_tag("div", id="dashboard-grid", attrs={"class": "dashboard-grid"})
        soup.append(grid)
    welcome = grid.find(id="welcome-message")
    if welcome is not None:
        welcome.decompose()
    if grid.find(id=entry["id"]) is not None:
        return
    frag = BeautifulSoup(entry["html"], "html.parser")
    for child in reversed(list(frag.contents)):
        if getattr(child, "name", None) or str(child).strip():
            grid.insert(0, child)


async def restore_canvas_widget(session_id: str, widget_id: str = "") -> dict:
    """Restore a previously dismissed widget (specific id or most recent) back to the canvas."""
    import app.canvas_manager as cm

    stack = cm._session_dismissed_widgets.get(session_id) or []
    if not stack:
        raise KeyError("no dismissed widget to restore")
    target_id = sanitize_widget_id(widget_id) if widget_id else ""
    entry = _pop_dismissed_entry(stack, target_id)

    async with cm._canvas_lock(session_id):
        current = cm.get_session_canvas(session_id) or ""
        soup = BeautifulSoup(current, "html.parser")
        _prepend_restored_widget(soup, entry)
        new_html = str(soup)
        version = cm.set_session_canvas(session_id, new_html, bump_version=True)
    return {
        "ok": True,
        "restored_id": entry["id"],
        "content": new_html,
        "version": version,
        "closed": list_dismissed_widgets(session_id),
    }
