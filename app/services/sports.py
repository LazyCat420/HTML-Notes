import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

def resolve_league(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    for key in _SPORTS_KEYS:
        if re.search(rf'\b{re.escape(key)}\b', lowered):
            return SPORTS_LEAGUES[key]
    return None


def _competitor(side: dict) -> dict:
    """A competitor is a team in team sports and an athlete in MMA/boxing — the
    'team' key is simply absent for a UFC fight."""
    team = side.get("team") or {}
    athlete = side.get("athlete") or {}
    return {
        "name": (team.get("displayName") or athlete.get("displayName")
                 or team.get("shortDisplayName") or athlete.get("shortName") or "TBD"),
        "abbrev": team.get("abbreviation") or athlete.get("shortName") or "",
        "logo": team.get("logo") or athlete.get("headshot") or "",
        "score": side.get("score"),
        "record": (side.get("records") or [{}])[0].get("summary", ""),
        "winner": bool(side.get("winner")),
    }


async def sports_scores(league: str) -> dict:
    """Fixtures and scores for a league. Keyless, sub-second.

    Without this, "fifa scores" had no tool at all: it fell to the agent, which
    web-searched (a 20-60s scrape) and tried to squeeze the result into a text
    card. A UFC event nests every fight under one event's `competitions`, so
    those get flattened into individual matchups alongside team-sport games.
    """
    path = resolve_league(league) or SPORTS_LEAGUES.get(league.lower().strip()) or league
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard",
                headers={"User-Agent": _YAHOO_UA})
            payload = resp.json()
    except Exception as e:
        logger.error(f"sports_scores({league}) error: {e}")
        return {"error": str(e), "is_error": True}

    info = (payload.get("leagues") or [{}])[0]
    matchups = []
    for event in payload.get("events", []):
        for comp in event.get("competitions", []):
            sides = comp.get("competitors") or []
            if len(sides) < 2:
                continue
            # ESPN puts home first for team sports; MMA has no home/away at all.
            away, home = ((sides[1], sides[0]) if sides[0].get("homeAway") == "home"
                          else (sides[0], sides[1]))
            status = (comp.get("status") or {}).get("type") or {}
            matchups.append({
                "home": _competitor(home),
                "away": _competitor(away),
                "state": status.get("state"),           # pre | in | post
                "detail": status.get("shortDetail"),    # "Final", "7/18 - 5:00 PM EDT", "62'"
                "completed": bool(status.get("completed")),
                "note": (comp.get("notes") or [{}])[0].get("headline", ""),
            })

    if not matchups:
        return {"error": f"No fixtures found for '{league}'. It may be the off-season.",
                "is_error": True}

    return {
        "league": info.get("abbreviation") or info.get("name") or league,
        "title": info.get("name") or league,
        "season": (payload.get("season") or {}).get("year"),
        "events": matchups,
    }


__all__ = [k for k in globals().keys() if not k.startswith('__')]
