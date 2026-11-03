"""Music asks are UNDERSTOOD by the LLM router, not guessed by a regex lane.

2026-11-03: "buckethead instrumental music" built a GENRE mix for
"buckethead instrumental" because the music fast-path hardcoded kind="genre"
for every ask containing "music" — 40+ of the owner's real asks are
"<artist> music". The fast lane now only DETECTS a music ask and hands it to
route_with_llm; its own spec is the router-down fallback (kind "", never an
invented genre).

Every test drives the real POST /session/message → send_message → router →
build_router_widget → render_mini_music_player chain, with only the model call
faked (patch_server), and reads what the browser would receive: the debug
frame and the data-hn-config the server stamped on the widget.
"""
import html as _html
import json
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import database
from app import main as m
from app.main import app

client = TestClient(app)
SESSION = "music-understanding"
EMPTY = '<div id="dashboard-grid" class="dashboard-grid"></div>'


@pytest.fixture(autouse=True)
def _own_db(monkeypatch, tmp_path):
    # A throwaway DB per test: never the dev notes.db.
    monkeypatch.setattr(database, "DATABASE_URL", str(tmp_path / "music_understanding.db"))


def _seed():
    database.init_db()
    conn = database.get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Music", "2026-11-03T00:00:00Z"))
    conn.commit(); conn.close()


def _drive(message, router, patch_server):
    _seed()
    calls = []

    async def fake_router(message_, context_block):
        calls.append(message_)
        return router(message_) if callable(router) else router

    patch_server("route_with_llm", fake_router)
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": message, "current_canvas": EMPTY})
    assert res.status_code == 200
    events = []
    for line in res.text.split("\n"):
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except Exception:
                pass
    debug = next((e for e in events if e.get("type") == "debug"), {})
    cfg = None
    for e in events:
        if e.get("type") == "component":
            mm = re.search(r'data-hn-config=(?:"([^"]*)"|\'([^\']*)\')', e.get("html") or e.get("content") or "")
            if mm:
                cfg = json.loads(_html.unescape(mm.group(1) or mm.group(2)))
    return calls, debug, cfg


def test_artist_music_ask_reaches_the_router_and_plays_the_artist(patch_server):
    plan = {"widgets": [{"type": "music", "query": "buckethead",
                         "modifiers": {"kind": "artist"}, "target": None}],
            "reason": "music", "checks": {}}
    calls, debug, cfg = _drive("buckethead instrumental music", plan, patch_server)
    assert calls == ["buckethead instrumental music"], "the router must see music asks"
    assert debug.get("path") == "router"
    assert debug.get("modifiers") == [{"kind": "artist"}]
    assert cfg["genre"] == "buckethead" and cfg["kind"] == "artist"


def test_router_down_falls_back_to_a_music_widget_without_guessing_kind(patch_server):
    calls, debug, cfg = _drive("buckethead instrumental music", None, patch_server)
    assert calls, "router was asked first"
    assert debug.get("path") == "fast-path" and debug.get("fallback") == "router-unavailable"
    assert cfg["kind"] == "", "fallback must not call an artist a genre"
    assert cfg["genre"] == "buckethead instrumental"


def test_router_down_genre_ask_gets_kind_from_the_genre_list(patch_server):
    _calls, _debug, cfg = _drive("play smooth jazz", None, patch_server)
    assert cfg["genre"] == "smooth jazz" and cfg["kind"] == "genre"


def test_router_deferring_a_listen_ask_still_plays(patch_server):
    plan = {"defer": True, "reason": "x", "checks": {"wants": "listen"}}
    _calls, debug, cfg = _drive("music", plan, patch_server)
    assert cfg is not None and debug.get("fallback") == "router-unavailable"


def test_hallucinated_router_query_is_discarded(patch_server):
    """Live 2026-11-03: "heavy metal music" came back as artist "buckethead"."""
    plan = {"widgets": [{"type": "music", "query": "buckethead",
                         "modifiers": {"kind": "artist"}, "target": None}],
            "reason": "music", "checks": {}}
    _calls, _debug, cfg = _drive("heavy metal music", plan, patch_server)
    assert cfg["genre"] == "heavy metal" and cfg["kind"] == "genre"
    plan["widgets"][0]["query"] = "John Coltrane"
    _calls, _debug, cfg = _drive("John coletrain music", plan, patch_server)
    assert cfg["genre"] == "John Coltrane", "a typo fix sharing a word is kept"


def test_bare_play_music_never_invents_a_genre(patch_server):
    _calls, _debug, cfg = _drive("play music", None, patch_server)
    assert cfg is not None and cfg["genre"] == "", "no 'lofi' default"
    plan = {"widgets": [{"type": "music", "query": "", "modifiers": {}, "target": None}],
            "reason": "music", "checks": {}}
    _calls, _debug, cfg = _drive("play some music", plan, patch_server)
    assert cfg["genre"] == ""


def test_router_query_is_used_verbatim_not_re_stripped(patch_server):
    plan = {"widgets": [{"type": "music", "query": "Four Tet",
                         "modifiers": {"kind": "artist"}, "target": None}],
            "reason": "music", "checks": {}}
    _calls, _debug, cfg = _drive("Play four tet music please", plan, patch_server)
    assert cfg["genre"] == "Four Tet"


def test_missing_kind_is_resolved_from_the_musicbrainz_genre_list(patch_server):
    plan = {"widgets": [{"type": "music", "query": "smooth jazz", "modifiers": {},
                         "target": None}], "reason": "music", "checks": {}}
    _calls, _debug, cfg = _drive("play smooth jazz please", plan, patch_server)
    assert cfg["kind"] == "genre"


def test_play_some_music_ask_is_not_stolen_by_the_answer_or_build_lanes(patch_server):
    """With the music lane no longer returning, the lanes after it (list,
    notes, compose, answer, build→agent) must not claim a music ask."""
    plan = {"widgets": [{"type": "music", "query": "jazz", "modifiers": {"kind": "genre"},
                         "target": None}], "reason": "music", "checks": {}}
    for ask in ("can you play some primus please.",
                "Add a Mini Music Player widget and play some jazz music"):
        calls, debug, cfg = _drive(ask, plan, patch_server)
        assert calls == [ask], f"{ask!r} must reach the router"
        assert cfg is not None, f"{ask!r} must build the music widget"


def _set_budget(monkeypatch, budget, refine):
    import sys
    for mod in list(sys.modules.values()):
        name = getattr(mod, "__name__", "") or ""
        if name == "app" or name.startswith("app."):
            if hasattr(mod, "MUSIC_ROUTER_BUDGET_S"):
                monkeypatch.setattr(mod, "MUSIC_ROUTER_BUDGET_S", budget)
            if hasattr(mod, "MUSIC_ROUTER_REFINE_S"):
                monkeypatch.setattr(mod, "MUSIC_ROUTER_REFINE_S", refine)


def _frames(text):
    out = []
    for line in text.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except Exception:
                pass
    return out


def test_slow_router_falls_back_within_the_music_budget(patch_server, monkeypatch):
    """Both local vLLM boxes took 30s+ on 2026-11-03; a music ask must show
    the fallback player after MUSIC_ROUTER_BUDGET_S, and a router that never
    answers inside MUSIC_ROUTER_REFINE_S must not hold the turn open."""
    import asyncio
    import time
    _set_budget(monkeypatch, 0.3, 0.5)

    async def slow_router(message_, context_block):
        await asyncio.sleep(5)
        return {"widgets": [{"type": "music", "query": "never", "modifiers": {}}]}

    _seed()
    patch_server("route_with_llm", slow_router)
    t0 = time.perf_counter()
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "play buckethead music", "current_canvas": EMPTY})
    took = time.perf_counter() - t0
    assert took < 3, f"music ask waited {took:.1f}s for a slow router"
    frames = _frames(res.text)
    assert frames[0].get("fallback") == "router-unavailable"
    assert not any(f.get("path") == "router-refine" for f in frames)
    assert "buckethead" in res.text


def test_late_router_answer_refines_the_player_in_place(patch_server, monkeypatch):
    """The fallback plays at once (kind "" = genre first); when the router
    answers after the budget, the SAME widget is rebuilt as an artist mix."""
    import asyncio
    _set_budget(monkeypatch, 0.2, 5)

    async def late_router(message_, context_block):
        await asyncio.sleep(0.8)
        return {"widgets": [{"type": "music", "query": "Buckethead",
                             "modifiers": {"kind": "artist"}, "target": None}],
                "reason": "music", "checks": {}}

    _seed()
    patch_server("route_with_llm", late_router)
    with patch("httpx.AsyncClient.stream"):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "buckethead instrumental music",
            "current_canvas": EMPTY})
    frames = _frames(res.text)
    comps = [f for f in frames if f.get("type") == "component"]
    refine = [f for f in frames if f.get("path") == "router-refine"]
    assert len(comps) == 2, "fallback frame, then the refined frame"
    assert refine and refine[0]["config"] == {"genre": "Buckethead", "kind": "artist"}

    def cfg_of(c):
        mm = re.search(r'data-hn-config=(?:"([^"]*)"|\'([^\']*)\')', c["content"])
        return json.loads(_html.unescape(mm.group(1) or mm.group(2)))
    assert cfg_of(comps[0])["kind"] == ""
    assert cfg_of(comps[1]) | {} == {**cfg_of(comps[1]), "genre": "Buckethead", "kind": "artist"}
    ids = [re.search(r'id="(music-[0-9a-f]+)"', c["content"]).group(1) for c in comps]
    assert ids[0] == ids[1], "refined in place, not a second player"


def test_router_small_talk_reply_to_a_music_ask_still_plays(patch_server):
    """Live eval 2026-11-03: the router replied to "play music" with a chat
    bubble. A music-shaped ask must still get a player."""
    reply = {"reply": "Sure! What would you like to hear?", "reason": "chat", "checks": {}}
    calls, debug, cfg = _drive("play music", reply, patch_server)
    assert calls == ["play music"]
    assert cfg is not None and cfg["genre"] == ""
    assert debug.get("fallback") == "router-unavailable"


def test_resolve_music_kind_facts():
    assert m.resolve_music_kind("smooth jazz") == "genre"
    assert m.resolve_music_kind("lofi") == "genre"          # MusicBrainz spells it lo-fi
    assert m.resolve_music_kind("jungle house") == ""       # unlisted blend: failover decides
    assert m.resolve_music_kind("buckethead instrumental") == ""  # "instrumental" IS a genre word
    assert m.resolve_music_kind("hip-hop") == "genre"
    assert m.resolve_music_kind("buckethead") == ""          # unknown, not "artist"
    assert m.resolve_music_kind("hindu") == ""
    assert m.resolve_music_kind("") == ""
    assert len(m.MUSICBRAINZ_GENRE_KEYS) > 2000
