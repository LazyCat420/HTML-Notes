import sys
import app.main as main
sys.modules[__name__].__dict__.update(main.__dict__)

def _wmo(code) -> tuple:
    try:
        return _WMO_CODES.get(int(code), ("Unknown", "help", "❓"))
    except (TypeError, ValueError):
        return ("Unknown", "help", "❓")


async def geocode_location(name: str) -> Optional[dict]:
    """City name → {name, label, latitude, longitude} via Open-Meteo's keyless
    geocoding API. Returns None if nothing matches."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": name, "count": 1, "language": "en", "format": "json"},
            )
            results = resp.json().get("results") or []
    except Exception as e:
        logger.warning(f"geocode({name!r}) failed: {e}")
        results = []
    if not results:
        # Open-Meteo only knows cities/towns; it misses landmarks, neighborhoods,
        # small towns and misspellings ("Lake Tahoe", "Silicon Valley"), which
        # then rendered "Couldn't find a place called X." Fall through to the same
        # Nominatim geocoder the map path uses — it resolves those.
        alt = await geocode_nominatim(name)
        if alt and alt.get("lat") is not None:
            return {
                "name": alt.get("resolved") or name,
                "label": alt.get("resolved") or name,
                "latitude": alt["lat"],
                "longitude": alt["lon"],
            }
        return None
    r = results[0]
    label = r.get("name", name)
    parts = [p for p in (label, r.get("admin1"), r.get("country_code")) if p]
    # dict.fromkeys dedupes "Singapore, Singapore" → "Singapore".
    return {
        "name": label,
        "label": ", ".join(dict.fromkeys(parts)),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
    }


async def get_weather(location: str, units: str = "fahrenheit") -> dict:
    """Current conditions + 5-day forecast for a place, via Open-Meteo (keyless).

    Geocodes the name, then one forecast call. The returned dict is exactly the
    weather widget's config; {is_error: True} on any failure so the caller can
    fall through to the agent instead of rendering a dead card.
    """
    place = await geocode_location(location)
    if not place or place.get("latitude") is None:
        return {"error": f"Couldn't find a place called '{location}'.", "is_error": True}

    fahrenheit = units != "celsius"
    unit_sym = "°F" if fahrenheit else "°C"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
                "wind_speed_unit": "mph" if fahrenheit else "kmh",
                "timezone": "auto",
                "forecast_days": 5,
            })
            data = resp.json()
    except Exception as e:
        logger.warning(f"weather({location!r}) failed: {e}")
        return {"error": f"Couldn't fetch weather for {place['label']}.", "is_error": True}

    def _round(v):
        return round(v) if isinstance(v, (int, float)) else None

    cur = data.get("current") or {}
    cur_label, cur_icon, cur_emoji = _wmo(cur.get("weather_code"))
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    days = []
    for i, d in enumerate(dates[:5]):
        label, icon, emoji = _wmo(codes[i] if i < len(codes) else None)
        try:
            y, m, dd = (int(x) for x in d.split("-"))
            day_name = "Today" if i == 0 else datetime.date(y, m, dd).strftime("%a")
        except Exception:
            day_name = "Today" if i == 0 else d[5:]
        days.append({
            "day": day_name,
            "hi": _round(highs[i]) if i < len(highs) else None,
            "lo": _round(lows[i]) if i < len(lows) else None,
            "condition": label, "icon": icon, "emoji": emoji,
        })

    return {
        "location": place["label"],
        "unit": unit_sym,
        "current": {
            "temp": _round(cur.get("temperature_2m")),
            "feels_like": _round(cur.get("apparent_temperature")),
            "humidity": cur.get("relative_humidity_2m"),
            "wind": _round(cur.get("wind_speed_10m")),
            "wind_unit": "mph" if fahrenheit else "km/h",
            "condition": cur_label, "icon": cur_icon, "emoji": cur_emoji,
        },
        "daily": days,
    }


def extract_location(message: str) -> str:
    """Pull a place name out of a weather ask. 'weather in San Francisco' → 'San
    Francisco'; 'tokyo weather' → 'tokyo'; bare 'weather' → the user's remembered
    city, else 'New York'."""
    m = (message or "").strip()
    match = re.search(r'\b(?:in|for|at|near)\s+([A-Za-zÀ-ɏ .,\'-]+)', m, re.IGNORECASE)
    if match:
        loc = match.group(1).strip(" .,")
        if loc:
            return loc
    cleaned = re.sub(r'[^\w\s]', ' ', m.lower())
    words = [w for w in cleaned.split() if w not in _LOCATION_STOPWORDS]
    remaining = " ".join(words).strip()
    if remaining:
        return remaining
    return database.get_user_facts().get("location") or "New York"


def extract_trip_destination(message: str) -> str:
    """Pull the destination out of a trip ask. 'plan me a trip to japan' → 'japan';
    'kyoto 5 day itinerary' → 'kyoto'. Prefers an explicit 'to/in/for X' clause,
    else falls back to the residual words after stripping trip-planning filler."""
    m = (message or "").strip()
    match = re.search(r'\b(?:to|in|for|around|through|across)\s+([A-Za-zÀ-ɏ .,\'-]+)',
                      m, re.IGNORECASE)
    if match:
        loc = match.group(1).strip(" .,")
        # Drop a trailing duration clause: "japan for 5 days" already handled by the
        # 'to' capture, but "japan in spring" shouldn't keep "in spring".
        loc = re.split(r'\bfor\s+\d|\b\d+\s*(?:day|week)', loc, flags=re.IGNORECASE)[0].strip(" .,")
        if loc and loc.lower() not in _TRIP_STOPWORDS:
            return loc
    cleaned = re.sub(r'[^\w\s]', ' ', m.lower())
    words = [w for w in cleaned.split() if w not in _TRIP_STOPWORDS and not w.isdigit()]
    return " ".join(words).strip()


async def geocode_place(name: str) -> Optional[dict]:
    """Place name -> {lat, lon, resolved} via Open-Meteo's keyless geocoder.
    Fast and clean but CITY/TOWN-level only — it misses counties, landmarks and
    event-ish strings. Returns None on a miss; build_map_config retries those
    through Nominatim (geocode_place_flex)."""
    name = (name or "").strip()
    if len(name) < 2:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": name, "count": 1, "language": "en", "format": "json"})
            hits = (r.json() or {}).get("results") or []
            if hits:
                g = hits[0]
                return {"lat": g["latitude"], "lon": g["longitude"], "resolved": g.get("name", name)}
    except Exception as e:
        logger.warning(f"geocode(open-meteo) failed for {name!r}: {e}")
    return None


async def geocode_nominatim(query: str) -> Optional[dict]:
    """Fallback geocoder (OSM Nominatim) for what Open-Meteo can't resolve —
    counties, landmarks, 'Butte County California'. Keyless but needs a real
    User-Agent and is rate-limited to ~1 req/s, so build_map_config calls it
    SEQUENTIALLY and only for the Open-Meteo misses."""
    query = (query or "").strip()
    if len(query) < 2:
        return None
    try:
        async with httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": "html-notes-map/1.0 (dashboard widget)"}) as c:
            r = await c.get("https://nominatim.openstreetmap.org/search",
                            params={"q": query, "format": "json", "limit": 1})
            arr = r.json() if r.status_code == 200 else []
            if arr:
                return {"lat": float(arr[0]["lat"]), "lon": float(arr[0]["lon"]),
                        "resolved": (arr[0].get("display_name", query) or query).split(",")[0][:40]}
    except Exception as e:
        logger.warning(f"geocode(nominatim) failed for {query!r}: {e}")
    return None


def poi_query_has_location(query: str) -> bool:
    """True when we can search Places somewhere the user actually meant: the query
    names a place ("food in Brooklyn") OR the user has a saved city. False means the
    only anchor left would be the SERVER's IP region — the caller must ask the user
    where they are instead of quietly mapping the datacenter's neighborhood."""
    if _EXPLICIT_PLACE_RE.search((query or "").lower()):
        return True
    return bool((database.get_user_facts().get("location") or "").strip())


def anchor_places_query(query: str) -> str:
    """Give Google Places a location to search. A bare POI/eat ask ("where can I
    get food", "food bank") or a "near me" ask is anchored to the user's remembered
    city so a New York user doesn't get the server region's results; a query that
    already names a place ("tacos in Austin") is left untouched. Only called once
    poi_query_has_location() is True, so a saved city always exists here when the
    query itself names no place."""
    q = query or ""
    city = (database.get_user_facts().get("location") or "").strip()
    if _NEAR_ME_RE.search(q.lower()):
        q = _NEAR_ME_RE.sub(f"in {city}" if city else "nearby", q)
    elif city and not _EXPLICIT_PLACE_RE.search(q.lower()):
        q = f"{q} in {city}"
    return q


def is_deictic_place(text: str) -> bool:
    """True when `text` points AT the user rather than naming a place."""
    return bool(_DEICTIC_PLACE_RE.match((text or "").strip()))


def _extract_directions_place(message: str) -> str:
    cleaned = re.sub(r'[^\w\s]', ' ', message or '')
    cleaned = _DIR_STRIP_RE.sub(' ', cleaned)
    cleaned = ' '.join(cleaned.split()).strip()
    if is_deictic_place(cleaned):
        # Not a place — fall through to the stored user location, or to the
        # "which city?" prompt. Anything but a geocode of the literal word.
        return ''
    return cleaned[:60] if len(cleaned) >= 2 else ''


def _emoji_for_place_type(primary_type: str, types: list) -> str:
    """Best category emoji for a Places result, from its primaryType then its
    type list. Falls back to a generic pin so every marker still gets an icon."""
    candidates = [primary_type or ""] + list(types or [])
    for t in candidates:
        t = (t or "").lower()
        if t in _PLACE_TYPE_EMOJI:
            return _PLACE_TYPE_EMOJI[t]
    # Substring pass — "italian_restaurant", "book_store" etc.
    for t in candidates:
        t = (t or "").lower()
        for key, emo in _PLACE_TYPE_EMOJI.items():
            if key in t:
                return emo
    return "📍"


async def google_places_search(query: str, limit: int = 12) -> list:
    """Real business/POI pins from Google Places API (New) searchText. Returns a
    markers list in render_map's shape ([{lat, lon, label, detail, color, emoji}]),
    or [] when the key is missing or the search fails (caller falls back)."""
    key = await _fetch_secret("GOOGLE_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": ("places.displayName,places.location,"
                                         "places.formattedAddress,places.rating,"
                                         "places.userRatingCount,places.primaryType,"
                                         "places.types"),
                },
                json={"textQuery": query, "maxResultCount": min(max(limit, 1), 20)})
        r.raise_for_status()
        out = []
        for p in (r.json().get("places") or []):
            loc = p.get("location") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if lat is None or lon is None:
                continue
            name = (p.get("displayName") or {}).get("text") or "Place"
            addr = p.get("formattedAddress") or ""
            rating = p.get("rating")
            reviews = p.get("userRatingCount")
            if rating:
                detail = f"★ {rating}" + (f" ({reviews})" if reviews else "")
                detail += f" · {addr}" if addr else ""
            else:
                detail = addr
            out.append({"lat": lat, "lon": lon, "label": name[:90],
                        "detail": detail[:180], "color": "#8b5cf6",
                        "emoji": _emoji_for_place_type(p.get("primaryType"), p.get("types"))})
        return out
    except Exception as e:
        logger.warning(f"[PLACES] search failed for {query!r}: {e}")
        return []


__all__ = [k for k in globals().keys() if not k.startswith('__')]
