"""Watches — standing asks that push widgets without a message.

    "tell me when NVDA drops 3%"        price_alert  (stock_snapshot)
    "watch the lakers game"             game         (sports_scores)
    "top stories every morning at 8"    briefing     (news card + weather)
    "keep this updated"                 refresh      (refresh_widget)

A watch is a sqlite row (app/database.py `watches`) with a CLOSED kind, a
spec and a condition. `_watch_scheduler` (started in main's lifespan) polls
due rows every WATCH_TICK_S and calls `fire_watch`, which re-pulls with an
EXISTING builder — never the agent, never an app action — evaluates the
condition, commits the widget through the normal canvas path (versioned,
locked) and pushes the `component` frame plus a `notify` line over the
session's event stream (GET /session/{id}/events).

Guards, in one place: WATCH_KINDS is the whole registry; per-session and
global caps; a per-kind minimum interval; a mandatory expiry; a fire
timeout. Parsing is regex-only and returns None on any ambiguity.

Standalone at import (no app.main import); everything from the app is
looked up lazily inside functions so this module can never join the
main ↔ canvas_manager import cycle.
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("app.main")

WATCH_TICK_S = 15
MAX_WATCHES_PER_SESSION = 6
MAX_WATCHES_TOTAL = 40

# kind → cadence limits and lifetime. min_interval is a floor the parser and
# create_watch clamp to; ttl is how long a watch lives unless cancelled.
WATCH_KINDS = {
    "price_alert": {"min_interval": 60, "default_interval": 300, "ttl": 24 * 3600, "timeout": 30},
    "game":        {"min_interval": 30, "default_interval": 60, "ttl": 6 * 3600, "timeout": 30},
    "briefing":    {"min_interval": 3600, "default_interval": 24 * 3600, "ttl": 7 * 24 * 3600, "timeout": 120},
    "refresh":     {"min_interval": 60, "default_interval": 300, "ttl": 12 * 3600, "timeout": 30},
}

WATCH_INTENT_RE = re.compile(
    r"\b(?:tell|let|alert|notify|ping|warn)\s+me\s+(?:know\s+)?(?:when|if|once|whenever)\b"
    r"|\bwatch\s+(?:the|this|that|my|tonight'?s|today'?s)\b.*\b(?:game|match|scores?)\b"
    r"|\bwatch\s+(?:the\s+)?[a-z ]{0,30}\b(?:game|match)\b"
    r"|\bevery\s+(?:morning|evening|night|day|hour|\d+\s*(?:min(?:ute)?s?|hours?))\b"
    r"|\b(?:keep|auto)[- ]?(?:refresh|updat)(?:e|ing|ed)?\s+(?:this|that|the|it)\b"
    r"|\bkeep\s+(?:this|that|it)\s+(?:updated|fresh|live|current)\b", re.I)

_PCT_DOWN_RE = re.compile(r"\b(?:drops?|falls?|dips?|goes?\s+down|sinks?|loses?|declines?)\b(?:\s+by)?\s*(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
_PCT_UP_RE = re.compile(r"\b(?:rises?|goes?\s+up|gains?|climbs?|jumps?|is\s+up|rallies)\b(?:\s+by)?\s*(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
_PRICE_DOWN_RE = re.compile(r"\b(?:below|under|falls?\s+(?:below|under|to)|drops?\s+(?:below|under|to)|dips?\s+(?:below|under|to))\s*\$?\s*(\d+(?:\.\d+)?)\b", re.I)
_PRICE_UP_RE = re.compile(r"\b(?:above|over|hits?|reaches?|crosses|goes?\s+(?:above|over)|rises?\s+(?:above|over|to)|climbs?\s+(?:above|over|to))\s*\$?\s*(\d+(?:\.\d+)?)\b", re.I)
_CASHTAG_RE = re.compile(r"\$?\b([A-Z]{1,5})\b")
_GAME_RE = re.compile(r"\bwatch\s+(?:the|this|that|my|tonight'?s|today'?s)?\s*(.*?)\s*\b(?:game|match|scores?)\b", re.I)
_BRIEFING_WORDS_RE = re.compile(r"\b(news|headlines|top stories|briefing|brief|weather|forecast|digest|rundown)\b", re.I)
_EVERY_RE = re.compile(r"\bevery\s+(morning|evening|night|day)\b", re.I)
_AT_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_REFRESH_RE = re.compile(r"\b(?:keep|auto)[- ]?(?:refresh|updat)(?:e|ing|ed)?\s+(?:this|that|the|it)\b|\bkeep\s+(?:this|that|it)\s+(?:updated|fresh|live|current)\b", re.I)
_PRICE_WORDS = {"tell", "me", "know", "let", "alert", "notify", "ping", "warn", "when", "if", "once", "whenever",
                "the", "a", "an", "stock", "shares", "share", "price", "of", "by", "to", "at", "goes", "go",
                "drops", "drop", "falls", "fall", "dips", "dip", "rises", "rise", "gains", "gain", "climbs",
                "climb", "jumps", "jump", "hits", "hit", "reaches", "reach", "above", "below", "over", "under",
                "percent", "down", "up", "is", "it", "its", "and", "or", "please", "than", "more", "less"}


def _kinds_of(text: str) -> str:
    return (text or "").lower()


async def parse_watch(message: str, defaults: dict, focus_widget_id: str = "") -> Optional[dict]:
    """{kind, spec, label, interval_s} for a watch ask, or None on ambiguity.
    Regex-only; `defaults` is the context bus (canvas_defaults)."""
    text = (message or "").strip()
    low = text.lower()
    if not text or not WATCH_INTENT_RE.search(low):
        return None
    defaults = defaults or {}

    # refresh — needs a focus widget, otherwise "this" means nothing
    if _REFRESH_RE.search(low):
        if not focus_widget_id:
            return None
        return {"kind": "refresh", "spec": {"widget_id": focus_widget_id},
                "label": "keep it updated", "interval_s": WATCH_KINDS["refresh"]["default_interval"]}

    # briefing — a cadence AND a briefing noun (else it is a reminder)
    m_every = _EVERY_RE.search(low)
    if m_every and _BRIEFING_WORDS_RE.search(low):
        when = m_every.group(1)
        hour, minute = {"morning": 8, "day": 8, "evening": 18, "night": 21}[when], 0
        m_at = _AT_TIME_RE.search(low)
        if m_at:
            hour = int(m_at.group(1)); minute = int(m_at.group(2) or 0)
            ampm = (m_at.group(3) or "").lower()
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if not ampm and when in ("evening", "night") and hour < 12:
                hour += 12
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        return {"kind": "briefing", "spec": {"hour": hour, "minute": minute, "place": defaults.get("place", "")},
                "label": f"briefing every {when} at {hour:02d}:{minute:02d}",
                "interval_s": WATCH_KINDS["briefing"]["default_interval"]}

    # game — a league from the text or the canvas; a team is optional
    m_game = _GAME_RE.search(low)
    if m_game:
        from app.services.sports import resolve_league
        subject = m_game.group(1).strip()
        league_path = resolve_league(low)
        league = (league_path.split("/")[-1] if league_path else "") or str(defaults.get("league") or "").lower()
        if not league:
            return None
        team_tokens = [t for t in re.findall(r"[a-z]+", subject)
                       if t not in {"the", "this", "that", "my", "a", "an", "tonight", "tonights", "today", "todays"}
                       and t != league and not resolve_league(t)]
        team = " ".join(team_tokens)
        return {"kind": "game", "spec": {"league": league, "team": team},
                "label": f"{(team or league).title()} game",
                "interval_s": WATCH_KINDS["game"]["default_interval"]}

    # price alert — a direction + threshold + a ticker we can resolve
    cond = None
    for rx, field, op, sign in ((_PCT_DOWN_RE, "change_pct", "<=", -1), (_PCT_UP_RE, "change_pct", ">=", 1),
                                (_PRICE_DOWN_RE, "price", "<=", 1), (_PRICE_UP_RE, "price", ">=", 1)):
        mm = rx.search(low)
        if mm:
            cond = {"field": field, "op": op, "value": sign * float(mm.group(1))}
            break
    if cond is None:
        return None
    symbol = ""
    for tok in _CASHTAG_RE.findall(text):
        if tok.lower() not in _PRICE_WORDS and len(tok) >= 2:
            symbol = tok
            break
    if not symbol:
        words = [w for w in re.findall(r"[a-z][a-z.&'-]*", low) if w not in _PRICE_WORDS]
        name = " ".join(words[:3]).strip()
        if name:
            from app.services.finance import _resolve_ticker
            try:
                symbol = (await _resolve_ticker(name) or "").strip().upper()
            except Exception:
                symbol = ""
    if not symbol:
        return None
    what = (f"{'drops' if cond['value'] < 0 else 'rises'} {abs(cond['value']):g}%" if cond["field"] == "change_pct"
            else f"{'falls below' if cond['op'] == '<=' else 'hits'} ${cond['value']:g}")
    return {"kind": "price_alert", "spec": {"symbol": symbol, "condition": cond},
            "label": f"{symbol} {what}", "interval_s": WATCH_KINDS["price_alert"]["default_interval"]}


# ── store ───────────────────────────────────────────────────────────────────

def create_watch(session_id: str, kind: str, spec: dict, label: str = "",
                 interval_s: Optional[int] = None, next_run: Optional[float] = None) -> Optional[dict]:
    """Insert a watch, or None when a guard refuses it (unknown kind, caps)."""
    from app import database
    rules = WATCH_KINDS.get(kind)
    if not rules or not session_id:
        return None
    if len(database.list_watches(session_id)) >= MAX_WATCHES_PER_SESSION:
        logger.info(f"[WATCH] refused: session {session_id[:8]} at the cap")
        return None
    if database.count_watches() >= MAX_WATCHES_TOTAL:
        logger.info("[WATCH] refused: global cap")
        return None
    interval = max(int(interval_s or rules["default_interval"]), rules["min_interval"])
    now = time.time()
    if next_run is None:
        next_run = _next_run_for(kind, spec, now)
    row = {"id": f"w{uuid.uuid4().hex[:10]}", "session_id": session_id, "kind": kind,
           "spec": spec, "label": (label or kind)[:120], "interval_s": interval,
           "next_run": float(next_run), "expires": now + rules["ttl"]}
    database.create_watch(row)
    return database.get_watch(row["id"])


def _next_run_for(kind: str, spec: dict, now: float) -> float:
    if kind != "briefing":
        return now  # first look right away
    hour, minute = int(spec.get("hour", 8)), int(spec.get("minute", 0))
    target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += timedelta(days=1)
    return target.timestamp()


def build_watch_list_config(session_id: str) -> dict:
    from app import database
    return {"title": "Watches", "watches": database.list_watches(session_id)}


# ── firing ──────────────────────────────────────────────────────────────────

def _cmp(op: str, a: float, b: float) -> bool:
    return a <= b if op == "<=" else a >= b


def _fp(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)[:400]


async def _commit_watch_widgets(session_id: str, widgets: list, label: str, notify: dict) -> bool:
    """Render [(widget_type, widget_id, config)] into the session canvas
    (replacing an earlier fire's node by id), record the turn, push the
    component frame and a notify line. Returns True when something was
    committed."""
    from app import canvas_manager as cm
    from app import database
    from bs4 import BeautifulSoup

    def _place(soup):
        target = soup.select_one("#dashboard-grid")
        if target is None:
            soup.append(BeautifulSoup('<div id="dashboard-grid" class="dashboard-grid"></div>', "html.parser"))
            target = soup.select_one("#dashboard-grid")
        touched = False
        for wtype, wid, cfg in widgets:
            node = BeautifulSoup(cm.render_widget(wtype, wid, cfg), "html.parser")
            existing = soup.find(id=wid)
            if existing is not None:
                existing.replace_with(node)
            else:
                target.insert(0, node)
            touched = True
        return None if touched else False

    event = await cm.commit_canvas(session_id, _place)
    if not event:
        return False
    for wtype, wid, cfg in widgets:
        cm._remember_widget_config(session_id, wid, cfg)
        cm.remember_widget_recipe(session_id, wid, wtype, cfg)
        cm.remember_widget_subject(session_id, wid, wtype, cfg)
    try:
        cm.record_turn(session_id, f"[watch] {label}", "watch",
                       [(wid, wtype, (cfg.get("title") or label), cm._widget_detail(cfg)) for wtype, wid, cfg in widgets])
        database.save_chat_message(
            message_id=f"msg_{uuid.uuid4().hex[:8]}", session_id=session_id, role="assistant",
            content=f"\n\n<!--CANVAS_HTML_START-->\n{cm.get_session_canvas(session_id)}\n<!--CANVAS_HTML_END-->")
    except Exception as e:
        logger.warning(f"[WATCH] bookkeeping failed: {e}")
    cm.push_session_event(session_id, event)
    cm.push_session_event(session_id, "data: " + json.dumps({"type": "notify", **notify}) + "\n\n")
    return True


async def fire_watch(row: dict) -> bool:
    """Evaluate one watch now. Returns True when it fired (committed + pushed).
    Never raises past its own logging; the scheduler bounds it with a timeout."""
    from app import database
    kind, spec = row["kind"], row["spec"] if isinstance(row.get("spec"), dict) else json.loads(row.get("spec") or "{}")
    sid, wid_base, label = row["session_id"], f"watch-{row['id']}", row.get("label") or kind
    fired, fp = False, row.get("last_fp")
    try:
        if kind == "price_alert":
            from app.services.finance import stock_snapshot
            snap = await stock_snapshot(spec["symbol"], "1d")
            if not snap or snap.get("is_error"):
                return False
            cond = spec["condition"]
            value = float(snap.get(cond["field"]) or 0)
            fp = _fp({"v": round(value, 2)})
            if _cmp(cond["op"], value, float(cond["value"])) and fp != row.get("last_fp"):
                sym = snap.get("symbol") or spec["symbol"]
                fired = await _commit_watch_widgets(
                    sid, [("stock_card", wid_base, snap)], label,
                    {"title": f"{sym} alert", "body": f"{sym} is {snap.get('change_pct'):+.1f}% today at ${snap.get('price')}"})
        elif kind == "game":
            from app.services.sports import sports_scores
            board = await sports_scores(spec["league"])
            if not board or board.get("is_error"):
                return False
            team = (spec.get("team") or "").lower()
            events = [e for e in (board.get("events") or []) if isinstance(e, dict)]
            if team:
                events = [e for e in events if team in json.dumps([e.get("home", {}).get("name"), e.get("away", {}).get("name")]).lower()] or events
            watched = events[:1]
            if not watched:
                return False
            ev = watched[0]
            fp = _fp({"state": ev.get("state"), "status": ev.get("status"),
                      "h": (ev.get("home") or {}).get("score"), "a": (ev.get("away") or {}).get("score")})
            if fp != row.get("last_fp"):
                cfg = {**board, "events": watched}
                h, a = ev.get("home") or {}, ev.get("away") or {}
                fired = await _commit_watch_widgets(
                    sid, [("scoreboard", wid_base, cfg)], label,
                    {"title": f"{a.get('name', '?')} @ {h.get('name', '?')}",
                     "body": f"{a.get('score', '-')} – {h.get('score', '-')} · {ev.get('status') or ev.get('state') or ''}"})
        elif kind == "briefing":
            from app import main as _main
            widgets = []
            try:
                news = await _main.build_news_card("top stories", general=True)
                if news and (news.get("items") or news.get("answer")):
                    widgets.append(("data_card", f"{wid_base}-news", news))
            except Exception as e:
                logger.warning(f"[WATCH] briefing news failed: {e}")
            place = spec.get("place") or ""
            if place:
                try:
                    w = await _main.get_weather(place)
                    if w and not w.get("is_error"):
                        widgets.append(("weather", f"{wid_base}-weather", w))
                except Exception as e:
                    logger.warning(f"[WATCH] briefing weather failed: {e}")
            if widgets:
                fp = _fp({"t": int(time.time() // 3600)})
                fired = await _commit_watch_widgets(sid, widgets, label,
                                                    {"title": "Your briefing", "body": f"{len(widgets)} fresh cards on the canvas"})
        elif kind == "refresh":
            from app import canvas_manager as cm
            try:
                out = await cm.refresh_widget(sid, spec["widget_id"])
            except (KeyError, RuntimeError):
                return False
            if out.get("changed"):
                cm.push_session_event(sid, "data: " + json.dumps({"type": "component", "content": out["content"], "version": out["version"]}) + "\n\n")
                fired = True
    except Exception as e:
        logger.warning(f"[WATCH] {kind} {row.get('id')} failed: {e}")
        return False
    finally:
        interval = int(row.get("interval_s") or WATCH_KINDS.get(kind, {}).get("default_interval", 300))
        nxt = time.time() + interval if kind != "briefing" else _next_run_for(kind, spec, time.time() + 60)
        try:
            database.mark_watch_run(row["id"], next_run=nxt, last_fp=fp, fired=fired)
        except Exception as e:
            logger.warning(f"[WATCH] mark_watch_run failed: {e}")
    return fired


async def _watch_scheduler() -> None:
    """Poll due watches; one bounded fire per row per tick. Same shape as
    _mcp_watchdog: never hot-loops, logs what it did."""
    from app import database
    while True:
        try:
            await asyncio.sleep(WATCH_TICK_S)
            now = time.time()
            database.expire_watches(now)
            for row in database.due_watches(now):
                timeout = WATCH_KINDS.get(row["kind"], {}).get("timeout", 30)
                try:
                    fired = await asyncio.wait_for(fire_watch(row), timeout)
                    if fired:
                        logger.info(f"[WATCH] fired {row['kind']} {row['id']} ({row.get('label')})")
                except asyncio.TimeoutError:
                    logger.warning(f"[WATCH] {row['kind']} {row['id']} timed out after {timeout}s")
                    database.mark_watch_run(row["id"], next_run=now + int(row.get("interval_s") or 300),
                                            last_fp=row.get("last_fp"), fired=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[WATCH] scheduler tick failed: {e}")
