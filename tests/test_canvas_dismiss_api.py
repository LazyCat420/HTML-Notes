"""Tests for server-side canvas dismiss/restore undo stack and session LRU pruning."""
import pytest
from fastapi.testclient import TestClient

from app import main, canvas_manager


client = TestClient(main.app)


def _grid_with(*widget_specs):
    cards = "".join(
        f'<div id="{wid}" class="widget-container" data-widget-type="{wtype}" '
        f'data-widget-title="{title}"><h3>{title}</h3><p>body</p></div>'
        for wid, wtype, title in widget_specs
    )
    return f'<div id="dashboard-grid" class="dashboard-grid">{cards}</div>'


def test_dismiss_and_restore_roundtrip_updates_version_and_history():
    sid = "sess-dismiss-roundtrip"
    main._session_dismissed_widgets.pop(sid, None)
    html = _grid_with(("w-1", "weather", "SF Weather"), ("w-2", "clock", "Tokyo Clock"))
    v0 = canvas_manager.set_session_canvas(sid, html)

    bad = client.post(f"/api/session/{sid}/canvas/dismiss", json={})
    assert bad.status_code == 400

    res = client.post(f"/api/session/{sid}/canvas/dismiss", json={"widget_id": "w-1"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["removed"] is True
    assert body["version"] > v0
    assert [x["id"] for x in body["closed"]] == ["w-1"]
    assert 'id="w-1"' not in canvas_manager.get_session_canvas(sid)
    assert 'id="w-2"' in canvas_manager.get_session_canvas(sid)

    hist = client.get(f"/session/{sid}/history")
    assert hist.status_code == 200
    assert [x["id"] for x in hist.json()["closed_widgets"]] == ["w-1"]

    restored = client.post(f"/api/session/{sid}/canvas/restore", json={"widget_id": "w-1"})
    assert restored.status_code == 200
    rbody = restored.json()
    assert rbody["ok"] is True and rbody["restored_id"] == "w-1"
    assert rbody["version"] > body["version"]
    assert rbody["closed"] == []
    assert 'id="w-1"' in canvas_manager.get_session_canvas(sid)

    missing = client.post(f"/api/session/{sid}/canvas/restore", json={})
    assert missing.status_code == 404


def test_dismiss_stack_is_capped_at_10_newest_widgets():
    sid = "sess-dismiss-cap"
    main._session_dismissed_widgets.pop(sid, None)
    specs = [(f"w-{i}", "note", f"Widget {i}") for i in range(12)]
    canvas_manager.set_session_canvas(sid, _grid_with(*specs))

    for i in range(12):
        r = client.post(f"/api/session/{sid}/canvas/dismiss", json={"widget_id": f"w-{i}"})
        assert r.status_code == 200

    listed = client.get(f"/api/session/{sid}/canvas/dismissed").json()["closed"]
    assert len(listed) == 10
    assert listed[0]["id"] == "w-11"
    assert listed[-1]["id"] == "w-2"


def test_prune_session_maps_evicts_oldest_idle_sessions_only():
    saved_lru = dict(main._session_lru)
    saved_canvas = dict(main._session_canvas)
    saved_ver = dict(main._session_canvas_version)
    saved_locks = dict(main._session_locks)
    saved_dismissed = dict(main._session_dismissed_widgets)
    saved_inflight = dict(main._session_inflight)
    created = [f"prune-test-{i}" for i in range(6)]
    try:
        main._session_lru.clear()
        main._session_canvas.clear()
        main._session_canvas_version.clear()
        main._session_locks.clear()
        main._session_dismissed_widgets.clear()
        main._session_inflight.clear()

        for idx, sid in enumerate(created):
            canvas_manager.set_session_canvas(sid, f"<div>{idx}</div>")
            main._session_lru[sid] = 100.0 + idx

        # Pin the oldest session as inflight so LRU must skip it and evict later idle ones.
        main._session_inflight[created[0]] = 1
        canvas_manager._prune_session_maps(max_sessions=3)

        assert created[0] in main._session_canvas
        assert created[1] not in main._session_canvas
        assert created[-1] in main._session_canvas
    finally:
        for d, snap in (
            (main._session_lru, saved_lru),
            (main._session_canvas, saved_canvas),
            (main._session_canvas_version, saved_ver),
            (main._session_locks, saved_locks),
            (main._session_dismissed_widgets, saved_dismissed),
            (main._session_inflight, saved_inflight),
        ):
            d.clear()
            d.update(snap)
