"""Tests for the widget-routing / editing / fallback edge-case fixes."""
import os
os.environ.setdefault("DATABASE_URL", "data/test_notes.db")
import re
import json
import pytest

from app.widgets.factory import render_iframe_app, render_checklist, generate_widget_html


# ── App Window (iframe_app) blank-URL guard ─────────────────────────────────
def test_iframe_app_blank_url_shows_placeholder_not_black_iframe():
    html = render_iframe_app("w1", {})  # no url
    assert "about:blank" not in html
    assert "<iframe" not in html
    assert "No app URL" in html


def test_iframe_app_real_url_renders_iframe():
    html = render_iframe_app("w1", {"url": "https://example.com/x"})
    # A framed-blocked external site renders an iframe (not the placeholder) that
    # points at the same-origin reader proxy, with an "open full app" link.
    assert "<iframe" in html
    assert "/widgets/embed?u=" in html
    assert "open_in_new" in html


# ── Universal empty-widget guard ────────────────────────────────────────────
def test_render_widget_degrades_empty_news_card_to_fallback():
    from app.main import render_widget
    # The exact broken case: a news data_card carrying only a topic, no items.
    html = render_widget("data_card", "news-1", {"news_topic": "stock market"})
    # It must NOT render the raw "NEWS_TOPIC | stock market" key/value dump...
    assert "NEWS_TOPIC" not in html.upper()
    # ...it renders the graceful fallback instead (apostrophe may be smart-quoted).
    assert "came back empty" in html
    assert "Stock Market" in html  # topic surfaced as a readable title


def test_render_widget_keeps_populated_card():
    from app.main import render_widget
    html = render_widget("data_card", "news-1",
                         {"title": "News", "items": [{"title": "A headline"}]})
    assert "A headline" in html
    assert "came back empty" not in html


def test_degenerate_predicate():
    from app.main import _widget_is_degenerate
    assert _widget_is_degenerate("data_card", {"news_topic": "x"}) is True
    assert _widget_is_degenerate("data_card", {"topic": "x", "query": "y"}) is True
    assert _widget_is_degenerate("data_card", {"items": [1]}) is False
    assert _widget_is_degenerate("data_card", {"answer": "hi"}) is False
    assert _widget_is_degenerate("weather", {"topic": "x"}) is False  # not guarded


# ── Routing regexes ─────────────────────────────────────────────────────────
def test_list_edit_vs_create():
    from app.main import LIST_EDIT_RE, LIST_INTENT_RE
    edit = "add a greek salad to the bbq pork ribs grocery list"
    assert LIST_EDIT_RE.search(edit)
    assert LIST_EDIT_RE.search("also add milk")
    assert LIST_EDIT_RE.search("put eggs on the list")
    # "give me a grocery list" is a CREATE, not an edit.
    assert not LIST_EDIT_RE.search("give me a grocery list")
    assert not LIST_EDIT_RE.search("make a shopping list for tacos")


def test_livestream_query_is_cleaned_to_subject():
    from app.main import clean_video_query, LIVE_ASK_RE
    # The live path must search the SUBJECT, not the literal phrase.
    assert LIVE_ASK_RE.search("dunkey livestreams")
    assert clean_video_query("dunkey livestreams") == "dunkey"
    assert clean_video_query("cnn live news") == "cnn news"
    assert clean_video_query("lofi hip hop live stream") == "lofi hip hop"


def test_directions_and_wiki_and_map():
    from app.main import DIRECTIONS_ASK_RE, WIKI_ASK_RE, MAP_ASK_RE
    assert DIRECTIONS_ASK_RE.search("how long will it take to get to the airport")
    assert DIRECTIONS_ASK_RE.search("traffic to downtown")
    assert WIKI_ASK_RE.search("open a random wikipedia page")
    assert MAP_ASK_RE.search("where are the fires in california")


# ── Checklist extract + merge (edit an existing list in place) ──────────────
def test_extract_and_merge_checklist(monkeypatch):
    import app.main as m

    # Render a real checklist, stash it as the session canvas, then extract it.
    checklist_html = generate_widget_html(
        "checklist", "checklist-abc",
        {"title": "BBQ Pork Ribs", "items": ["pork ribs", "bbq sauce"]})
    canvas = f'<div id="dashboard-grid" class="dashboard-grid">{checklist_html}</div>'
    m.set_session_canvas("sess-1", canvas)

    got = m._extract_existing_checklist("sess-1")
    assert got is not None
    wid, title, items = got
    assert wid == "checklist-abc"
    assert title == "BBQ Pork Ribs"
    assert [i["text"] for i in items] == ["pork ribs", "bbq sauce"]


@pytest.mark.asyncio
async def test_build_list_add_merges_without_dupes(patch_server):
    import app.main as m

    async def fake_llm(_instruction, max_tokens=400):
        return {"items": ["feta cheese", "cucumber", "pork ribs"]}  # "pork ribs" dupes

    patch_server("fast_llm_json", fake_llm)
    existing = [{"text": "pork ribs", "done": True}, {"text": "bbq sauce", "done": False}]
    out = await m.build_list_add_config("add a greek salad", "BBQ Pork Ribs", existing)

    texts = [i["text"] for i in out["items"]]
    assert texts[:2] == ["pork ribs", "bbq sauce"]          # existing preserved, in order
    assert out["items"][0]["done"] is True                   # done flag preserved
    assert "feta cheese" in texts and "cucumber" in texts    # new items added
    assert texts.count("pork ribs") == 1                     # dupe collapsed
    assert out["title"] == "BBQ Pork Ribs"                   # keeps the original title


# ── Canvas control: close-all, item-remove, restore ─────────────────────────
def test_clear_all_regex():
    from app.main import CLEAR_ALL_RE, LIST_ITEM_REMOVE_RE
    for phrase in ["close out everything", "clear the whole canvas",
                   "get rid of all the widgets", "wipe it", "start over",
                   "close all my widgets", "remove everything", "nuke it all"]:
        assert CLEAR_ALL_RE.search(phrase), phrase
    # single-widget removals must NOT trigger a full wipe
    for phrase in ["close the clock", "delete the grocery list",
                   "remove the weather widget", "hide the map"]:
        assert not CLEAR_ALL_RE.search(phrase), phrase


def test_list_item_remove_vs_widget_remove():
    from app.main import LIST_ITEM_REMOVE_RE
    assert LIST_ITEM_REMOVE_RE.search("delete the veggies from the grocery list")
    assert LIST_ITEM_REMOVE_RE.search("cross milk off the list")
    assert LIST_ITEM_REMOVE_RE.search("remove eggs from my shopping list")
    # "delete the grocery list" targets the WIDGET, not an item
    assert not LIST_ITEM_REMOVE_RE.search("delete the grocery list")
    assert not LIST_ITEM_REMOVE_RE.search("close the checklist")


def test_list_restore_regex():
    from app.main import LIST_RESTORE_RE, LIST_EDIT_RE
    assert LIST_RESTORE_RE.search("bring back my grocery list")
    assert LIST_RESTORE_RE.search("restore the list")
    assert LIST_RESTORE_RE.search("show me that list again")
    # an add-edit is guarded out of restore in the router
    assert LIST_EDIT_RE.search("add milk to my grocery list again")


def test_widget_state_db_overwrite():
    import app.database as db
    db.init_db()
    db.set_widget_state("list:test-xyz", '{"a": 1}')
    assert db.get_widget_state("list:test-xyz") == '{"a": 1}'
    db.set_widget_state("list:test-xyz", '{"a": 2}')  # OVERWRITE, not ignore
    assert db.get_widget_state("list:test-xyz") == '{"a": 2}'
    keys = [s["key"] for s in db.list_widget_states("list:")]
    assert keys.count("list:test-xyz") == 1  # one row — no bloat


def test_list_slug():
    from app.main import _list_slug
    assert _list_slug("Grocery List") == "grocery-list"
    assert _list_slug("BBQ Pork Ribs!") == "bbq-pork-ribs"
    assert _list_slug("") == "checklist"


def test_persist_and_resolve_list():
    import app.main as m, app.database as db
    db.init_db()
    m._persist_list_state({"title": "Zzq Grocery List",
                           "items": [{"text": "milk", "done": False}]})
    got = m._resolve_restorable_list("bring back my zzq grocery list")
    assert got and got["title"] == "Zzq Grocery List"
    assert got["items"][0]["text"] == "milk"
    # bare "bring my list back" falls back to the most recent (__last__)
    last = m._resolve_restorable_list("bring my list back")
    assert last and last.get("items")


@pytest.mark.asyncio
async def test_build_list_remove(patch_server):
    import app.main as m
    async def fake_llm(_instruction, max_tokens=400):
        return {"remove": ["carrots", "broccoli"]}
    patch_server("fast_llm_json", fake_llm)
    items = [{"text": "carrots", "done": False},
             {"text": "milk", "done": True},
             {"text": "broccoli", "done": False}]
    out = await m.build_list_remove_config("delete the veggies", "Groceries", items)
    texts = [i["text"] for i in out["items"]]
    assert texts == ["milk"]                 # only the two veggies removed
    assert out["items"][0]["done"] is True   # remaining item's state preserved


@pytest.mark.asyncio
async def test_build_list_remove_no_match_keeps_list(patch_server):
    import app.main as m
    async def fake_llm(_instruction, max_tokens=400):
        return {"remove": []}
    patch_server("fast_llm_json", fake_llm)
    items = [{"text": "milk", "done": False}]
    out = await m.build_list_remove_config("delete the veggies", "Groceries", items)
    assert [i["text"] for i in out["items"]] == ["milk"]  # unchanged, not emptied


# ── Google Places map pins ──────────────────────────────────────────────────
def test_poi_map_detection():
    from app.main import POI_MAP_RE, _NON_POI_GEO_RE
    for q in ["coffee shops in seattle", "pharmacies near me", "best sushi nearby",
              "gas stations in austin", "bookstores in portland", "bars near me"]:
        assert POI_MAP_RE.search(q), q
    # hazard / where-is stays on the web+geocode path, NOT Places
    assert _NON_POI_GEO_RE.search("where are the fires in california")
    assert not POI_MAP_RE.search("where are the fires in california")


@pytest.mark.asyncio
async def test_google_places_search_maps_markers(monkeypatch, patch_server):
    import app.main as m
    async def fake_secret(name): return "FAKE_KEY"
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"places": [
                {"displayName": {"text": "Mintish Coffee"},
                 "location": {"latitude": 47.62, "longitude": -122.32},
                 "formattedAddress": "123 Pine St", "rating": 5, "userRatingCount": 88},
                {"displayName": {"text": "No Coords"}, "location": {}},  # dropped: no coords
            ]}
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp()
    patch_server("_fetch_secret", fake_secret)
    monkeypatch.setattr(m.httpx, "AsyncClient", FakeClient)
    out = await m.google_places_search("coffee shops in seattle")
    assert len(out) == 1                        # the coord-less place is dropped
    mk = out[0]
    assert mk["lat"] == 47.62 and mk["lon"] == -122.32
    assert mk["label"] == "Mintish Coffee"
    assert "★ 5" in mk["detail"] and "123 Pine St" in mk["detail"]


@pytest.mark.asyncio
async def test_google_places_search_no_key_returns_empty(patch_server):
    import app.main as m
    async def no_secret(name): return ""
    patch_server("_fetch_secret", no_secret)
    assert await m.google_places_search("coffee shops") == []


@pytest.mark.asyncio
async def test_build_map_config_uses_places_for_poi(patch_server):
    import app.main as m
    async def fake_places(q, limit=12):
        return [{"lat": 1.0, "lon": 2.0, "label": "Shop", "detail": "", "color": "#8b5cf6"}]
    patch_server("google_places_search", fake_places)
    cfg = await m.build_map_config("coffee shops in seattle")
    assert cfg["markers"] and cfg["markers"][0]["label"] == "Shop"
    assert "1 place" in cfg["subtitle"]


# ── Hunger/meal intent → Places map (was falling to the slow blank-US agent) ──
def test_eat_map_detection():
    from app.main import EAT_MAP_RE, POI_MAP_RE, MAP_ASK_RE
    # These are exactly the phrasings that named no POI noun and no geo token, so
    # BOTH pre-existing regexes missed them and they hit the slow agent path.
    for q in ["where can i get food?", "where can i eat", "where to eat",
              "somewhere to eat", "food bank near me", "food pantry",
              "soup kitchen", "grab some lunch", "get some food", "im hungry",
              "good places to eat", "where should i eat dinner"]:
        ql = q.lower()
        assert EAT_MAP_RE.search(ql), q
    # Must NOT hijack informational/other food asks
    for q in ["food recipe", "how to cook food", "food news",
              "why is food expensive", "what is the healthiest food"]:
        assert not EAT_MAP_RE.search(q.lower()), q


def test_anchor_places_query(monkeypatch):
    import app.main as m, app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {"location": "New York"})
    # bare eat/POI ask → anchored to the user's city
    assert m.anchor_places_query("where can i get food") == "where can i get food in New York"
    assert m.anchor_places_query("food bank") == "food bank in New York"
    # "near me" → the city, not a literal Places search for "near me"
    assert m.anchor_places_query("restaurants near me") == "restaurants in New York"
    # an explicit place is left untouched
    assert m.anchor_places_query("tacos in Austin") == "tacos in Austin"
    # no saved city → degrade gracefully (Places falls back to IP-geo)
    monkeypatch.setattr(db, "get_user_facts", lambda: {})
    assert m.anchor_places_query("where can i get food") == "where can i get food"


@pytest.mark.asyncio
async def test_build_map_config_food_anchors_to_user_city(monkeypatch, patch_server):
    """'where can i get food' must reach Google Places with the user's city
    appended — the exact bug where a New York user got a blank whole-US map."""
    import app.main as m, app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {"location": "New York"})
    seen = {}
    async def fake_places(q, limit=12):
        seen["q"] = q
        return [{"lat": 40.7, "lon": -74.0, "label": "Joe's Pizza", "detail": "", "color": "#8b5cf6"}]
    patch_server("google_places_search", fake_places)
    cfg = await m.build_map_config("where can i get food")
    assert seen["q"] == "where can i get food in New York"   # anchored, not bare
    assert cfg["markers"] and cfg["markers"][0]["label"] == "Joe's Pizza"


# ── Require a stated location; never map the server's region ─────────────────
def test_poi_query_has_location(monkeypatch):
    import app.main as m, app.database as db
    # explicit place in the query → always OK, regardless of saved city
    monkeypatch.setattr(db, "get_user_facts", lambda: {})
    assert m.poi_query_has_location("tacos in Austin")
    assert m.poi_query_has_location("food banks near downtown Chicago")
    # no place + no saved city → NOT ok (would fall back to server region)
    assert not m.poi_query_has_location("where can i get food")
    assert not m.poi_query_has_location("restaurants near me")
    # no place but a saved city → OK
    monkeypatch.setattr(db, "get_user_facts", lambda: {"location": "Seattle"})
    assert m.poi_query_has_location("where can i get food")


def _map_branch_fires(m, text_clean, is_data, is_list):
    """Replicates the map/POI routing condition so we can assert the grocery guard."""
    return bool(m.MAP_ASK_RE.search(text_clean)
                or ((m.POI_MAP_RE.search(text_clean) or m.EAT_MAP_RE.search(text_clean))
                    and not is_data and not is_list))


def test_grocery_list_not_hijacked_by_map():
    """'grocery' is a POI noun, so a grocery-LIST ask used to hit the map branch
    (→ the 'which city?' prompt). The is_list guard must keep list asks off the map."""
    import re, app.main as m
    for q in ["can we add potato salad recipe to the grocery list",
              "add potato salad to the grocery list", "grocery list",
              "what's on my shopping list"]:
        is_list = bool(re.search(r'\b(list|checklist|to-?dos?)\b', q)
                       or m.LIST_EDIT_RE.search(q) or m.LIST_ITEM_REMOVE_RE.search(q))
        assert is_list, q
        assert not _map_branch_fires(m, q, is_data=False, is_list=is_list), q
    # a genuine store search (no "list") still maps
    q = "grocery stores near me"
    assert _map_branch_fires(m, q, is_data=False, is_list=False), q


def test_list_edit_matches_conversational_followups():
    from app.main import LIST_EDIT_RE
    for q in ["oh and add steak as well", "and add milk", "add eggs too",
              "also grab some bread", "and throw in some butter",
              "add greek salad to the grocery list"]:
        assert LIST_EDIT_RE.search(q), q


@pytest.mark.asyncio
async def test_traffic_widget_fallbacks_without_tomtom_key(monkeypatch, patch_server):
    """Without a TomTom key a traffic ask no longer pretends to show live flow:
    when the place geocodes it degrades to OUR themed area map (traffic off) with
    an honest 'needs a TomTom key' subtitle, instead of a plain Google embed
    misleadingly titled 'Traffic:'."""
    import app.main as m, app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {"location": "Seattle"})

    async def no_key(name):
        return ""

    async def geo(name):
        return {"lat": 47.6, "lon": -122.33, "resolved": "Seattle"}
    patch_server("_fetch_secret", no_key)
    patch_server("geocode_place", geo)

    wtype, cfg = await m.build_traffic_widget("traffic in seattle")
    assert wtype == "map" and cfg and cfg.get("traffic") is not True
    assert "TomTom key" in cfg.get("subtitle", "") and cfg["markers"]
    assert not cfg["title"].startswith("Traffic")  # honest — not live traffic
    # from A to B → a route embed (its route line is still congestion-coloured);
    # this path returns before any geocode, so it stays an iframe_app.
    wtype, cfg = await m.build_traffic_widget("directions from boston to nyc")
    assert wtype == "iframe_app" and cfg
    assert "saddr=boston" in cfg["url"] and "daddr=nyc" in cfg["url"]
    # "near me" falls back to the saved city, geocoded onto our map
    wtype, cfg = await m.build_traffic_widget("traffic near me")
    assert wtype == "map" and cfg["center"]["lat"] == 47.6
    # no place and no saved city → None so caller uses the travel-time answer card
    monkeypatch.setattr(db, "get_user_facts", lambda: {})
    wtype, cfg = await m.build_traffic_widget("traffic")
    assert cfg is None


@pytest.mark.asyncio
async def test_traffic_widget_renders_leaflet_tomtom_map_with_key(monkeypatch, patch_server):
    import app.main as m, app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {"location": "Seattle"})

    async def key(name):
        return "tomtom-test-key"

    async def geo(name):
        return {"lat": 47.6, "lon": -122.33, "resolved": "Seattle"}
    patch_server("_fetch_secret", key)
    patch_server("geocode_place", geo)
    wtype, cfg = await m.build_traffic_widget("traffic near me")
    assert wtype == "map" and cfg["traffic"] is True
    assert cfg["center"]["lat"] == 47.6 and cfg["markers"]
    assert cfg["title"].startswith("Traffic")


def test_map_document_injects_traffic_tiles_only_with_key():
    """The map iframe gets a TomTom flow overlay only when a tiles URL is supplied.

    The parameter used to be `traffic_key` (a bare API key the renderer pasted
    into a URL template). It is now `traffic_tiles_url` — the caller resolves the
    secret and builds the URL (app/main.py fetches TOMTOM_API_KEY from vault),
    so the renderer never handles the key itself. This test was left on the old
    signature by that refactor and had been failing ever since; the production
    caller was always correct.
    """
    from app.widgets.factory import map_payload, map_document_html
    payload = map_payload({"traffic": True,
                           "center": {"lat": 47.6, "lon": -122.33}, "zoom": 13})
    assert payload["traffic"] is True
    tiles = ("https://api.tomtom.com/traffic/map/4/tile/flow/relative0/"
             "{z}/{x}/{y}.png?key=abc123")
    doc = map_document_html(payload, traffic_tiles_url=tiles)
    assert "api.tomtom.com/traffic/map/4/tile/flow" in doc and "abc123" in doc
    assert "__TRAFFIC_LAYER__" not in doc
    # no tiles URL → no overlay, and the placeholder must not leak into the page
    doc = map_document_html(payload, traffic_tiles_url="")
    assert "tomtom" not in doc.lower() and "__TRAFFIC_LAYER__" not in doc
    # a non-traffic payload never gets the layer even when tiles are available
    plain = map_payload({"center": {"lat": 1, "lon": 2}})
    assert map_document_html(plain, traffic_tiles_url=tiles).lower().count("tomtom") == 0


def test_stock_news_asks_route_to_market_branch():
    """'stock market news' used to be EXCLUDED from the news fast-path and fall to
    the agent, whose stock_news tool rows have no snippets → a wall of links. The
    market branch must catch every finance-news phrasing the news branch rejects."""
    from app.main import NEWS_ASK_RE, STOCK_WORD_RE, MARKET_WORD_RE
    for q in ["stock market news", "crypto news", "market news today",
              "any news on nvidia stock", "nasdaq headlines"]:
        assert NEWS_ASK_RE.search(q), q
        assert STOCK_WORD_RE.search(q) or MARKET_WORD_RE.search(q), q
    # plain news must NOT be hijacked by the market branch
    for q in ["news about ai", "latest headlines", "news today"]:
        assert not (STOCK_WORD_RE.search(q) or MARKET_WORD_RE.search(q)), q


@pytest.mark.asyncio
async def test_build_stock_news_config_writes_summaries(patch_server):
    import app.main as m

    async def _none(*a, **k):
        return []

    async def fake_stock_news(query, limit=8):
        return {"news": [{"title": "Nvidia pops 5%", "publisher": "Reuters",
                          "published": "2026-07-15 10:00 UTC", "url": "https://x/a",
                          "image": "https://img/a.jpg", "related_tickers": ["NVDA"],
                          # provider summary — the card path does NOT scrape pages
                          "og_desc": "Nvidia rose after earnings beat expectations."}],
                "matches": [], "count": 1}

    async def fake_llm(instruction, max_tokens=400):
        return {"overview": "Nvidia rallied 5% after its earnings beat.",
                "items": [{"index": 0, "title": "Nvidia jumps on earnings",
                           "summary": "Nvidia stock rose 5% after a strong report."}]}
    patch_server("news_search", _none)
    patch_server("_finnews_articles", _none)
    patch_server("stock_news", fake_stock_news)
    patch_server("fast_llm_json", fake_llm)
    cfg = await m.build_stock_news_config("nvidia stock news")
    item = cfg["items"][0]
    assert item["description"].startswith("Nvidia stock rose")
    assert item["title"] == "Nvidia jumps on earnings"
    assert item["badge"] == "NVDA" and item["image"] == "https://img/a.jpg"
    assert cfg["answer"] == "Nvidia rallied 5% after its earnings beat."
    assert cfg["subtitle"] != cfg["answer"] and cfg["subtitle"].startswith("1 stories")

    # LLM pass failing must still yield the provider summary, never bare links
    async def dead_llm(instruction, max_tokens=400):
        return None
    patch_server("fast_llm_json", dead_llm)
    cfg = await m.build_stock_news_config("nvidia stock news")
    assert cfg["items"][0]["description"].startswith("Nvidia rose after")


def test_traffic_gate_does_not_hijack_translation():
    """The 'from X to Y' route form lives INSIDE the directions gate, so a
    'translate from english to spanish' can never be read as a map route."""
    from app.main import DIRECTIONS_ASK_RE, EAT_MAP_RE
    q = "translate from english to spanish"
    assert not DIRECTIONS_ASK_RE.search(q)   # never enters the traffic branch


@pytest.mark.asyncio
async def test_build_map_config_prompts_when_no_location(monkeypatch, patch_server):
    """No place named and no saved city → build_map_config must NOT call Places
    (which would return the server region); it returns a location-prompt card."""
    import app.main as m, app.database as db
    monkeypatch.setattr(db, "get_user_facts", lambda: {})
    called = {"places": False}
    async def fake_places(q, limit=12):
        called["places"] = True
        return [{"lat": 1, "lon": 2, "label": "X", "detail": "", "color": "#8b5cf6"}]
    patch_server("google_places_search", fake_places)
    cfg = await m.build_map_config("where can i get food")
    assert cfg.get("prompt_for_location") is True
    assert not cfg.get("markers")
    assert called["places"] is False           # never searched the server region
    assert "where you are" in cfg["answer"]


# ── News: general "what's going on" must fetch top stories, not literal-search ─
@pytest.mark.asyncio
async def test_general_news_uses_top_stories(patch_server):
    import app.main as m
    captured = {}
    async def fake_news_search(topic, limit=6, **kw):
        captured["topic"] = topic
        return []  # short-circuits the rest of build_news_config
    patch_server("news_search", fake_news_search)

    await m.build_news_config("whats going on in the news")
    assert captured["topic"] == "", "general ask must search top stories (empty topic)"

    await m.build_news_config("news about AI")
    assert captured["topic"] == "ai", "a real topic must still be searched"


# ── Time / clock / timer ────────────────────────────────────────────────────
def test_timezone_resolver():
    from app.main import _resolve_timezone
    assert _resolve_timezone("time in tokyo") == "Asia/Tokyo"
    assert _resolve_timezone("what time is it in new york") == "America/New_York"
    assert _resolve_timezone("what time is it") == ""


def test_duration_parser():
    from app.main import _parse_duration_seconds
    assert _parse_duration_seconds("set a timer for 5 minutes") == 300
    assert _parse_duration_seconds("1 hour 30 minutes") == 5400
    assert _parse_duration_seconds("90 seconds") == 90
    assert _parse_duration_seconds("no duration here") == 0


# ── Website embed reader routing ────────────────────────────────────────────
def test_iframe_app_routes_external_through_reader():
    from app.widgets.factory import render_iframe_app
    ext = render_iframe_app("w1", {"url": "https://en.wikipedia.org/wiki/Cat"})
    assert "/widgets/embed?u=" in ext          # framed sites go through the reader
    yt = render_iframe_app("w2", {"url": "https://www.youtube.com/embed/abc"})
    assert 'src="https://www.youtube.com/embed/abc"' in yt  # embed-friendly = direct


# ── Persistent user memory ──────────────────────────────────────────────────
def test_capture_user_facts_and_default_location():
    import app.main as m, app.database as db
    db.init_db(); db.wipe_user_facts()
    got = m.capture_user_facts("hey my name is Alex and im from Seattle")
    assert got.get("name") == "Alex" and got.get("location") == "Seattle"
    assert db.get_user_facts()["location"] == "Seattle"
    # bare weather ask now defaults to the remembered city, not New York
    assert m.extract_location("whats the weather") == "Seattle"
    # a changed city OVERWRITES (upsert), not appends
    m.capture_user_facts("actually i live in Boston now")
    assert db.get_user_facts()["location"] == "Boston"
    # wipe clears everything
    db.wipe_user_facts()
    assert db.get_user_facts() == {}
    assert m.extract_location("whats the weather") == "New York"


def test_user_facts_prompt():
    import app.main as m, app.database as db
    db.init_db(); db.wipe_user_facts()
    assert m._user_facts_prompt() == ""          # nothing known → no injection
    db.set_user_fact("location", "Seattle")
    assert "based in Seattle" in m._user_facts_prompt()
    db.wipe_user_facts()


# ── Agentic router (LLM widget selection + composition) ─────────────────────
@pytest.mark.asyncio
async def test_route_with_llm_validates_and_filters(patch_server):
    """The router must accept only known types, cap at 4, and pass a defer through
    untouched — a malformed model reply degrades to None (→ agent), never a crash."""
    import app.main as m

    async def run(fake):
        async def fake_llm(instruction, max_tokens=400):
            return fake
        patch_server("fast_llm_json", fake_llm)
        # An explicitly BROAD ask: the router now returns ONE widget unless the
        # ask is broad (composition_allowed), so the multi-widget and cap-at-4
        # assertions below need a message that is allowed to compose. The
        # validation behaviour they pin is unchanged.
        return await m.route_with_llm("plan my whole day in Seattle", "")

    # valid multi-widget plan, plus one bogus type that must be dropped
    plan = await run({
        "widgets": [{"type": "weather", "query": "Seattle"},
                    {"type": "map", "query": "things to do in Seattle"},
                    {"type": "bogus", "query": "x"}],
        "reason": "plan the day"})
    assert [w["type"] for w in plan["widgets"]] == ["weather", "map"]
    # explicit deferral is preserved
    assert (await run({"defer": True}))["defer"] is True
    # no valid widgets → None (caller falls back to the agent)
    assert await run({"widgets": [{"type": "nope"}]}) is None
    # model failure (None) → None
    assert await run(None) is None
    # cap at 4
    big = await run({"widgets": [{"type": "answer", "query": str(i)} for i in range(9)]})
    assert len(big["widgets"]) == 4


@pytest.mark.asyncio
async def test_build_router_widget_dispatches_each_type(patch_server):
    """Each router type routes to the matching builder and returns a spawnable
    (widget_type, id_prefix, config) triple — or None when the pull is empty."""
    import app.main as m

    async def ok_weather(loc, units="fahrenheit"):
        return {"location": loc, "current": {}}
    async def bad_weather(loc, units="fahrenheit"):
        return {"is_error": True}
    async def fake_answer(msg, results=None, read_top=2):
        return {"title": "A", "answer": "text"}
    async def fake_map(msg):
        return {"title": "M", "markers": [{"lat": 1, "lon": 2}]}
    patch_server("get_weather", ok_weather)
    patch_server("build_answer_config", fake_answer)
    patch_server("build_map_config", fake_map)

    wt, pfx, cfg = await m.build_router_widget({"type": "weather", "query": "Tokyo"}, "s", "msg")
    # extract_location normalises to lowercase before geocoding
    assert (wt, pfx) == ("weather", "weather") and cfg["location"].lower() == "tokyo"

    wt, pfx, cfg = await m.build_router_widget({"type": "answer", "query": "why is the sky blue"}, "s", "msg")
    assert wt == "data_card" and cfg["answer"] == "text"

    wt, pfx, cfg = await m.build_router_widget({"type": "map", "query": "fires"}, "s", "msg")
    assert wt == "map" and cfg["markers"]

    # a failed weather pull → None (skipped, not an empty widget)
    patch_server("get_weather", bad_weather)
    assert await m.build_router_widget({"type": "weather", "query": "Nowhere"}, "s", "msg") is None

    # unknown type → None
    assert await m.build_router_widget({"type": "mystery", "query": "x"}, "s", "msg") is None


@pytest.mark.asyncio
async def test_build_router_widget_clock_and_traffic(patch_server):
    import app.main as m
    # timer parsing lands a countdown, not a bare clock
    wt, pfx, cfg = await m.build_router_widget(
        {"type": "clock", "query": "set a timer for 5 minutes"}, "s", "msg")
    assert cfg["mode"] == "countdown" and cfg["duration_seconds"] == 300
    # traffic delegates to build_traffic_widget's (type, cfg) contract
    seen = {}
    async def fake_traffic(msg, force_traffic=False):
        seen["force_traffic"] = force_traffic
        return "iframe_app", {"url": "https://maps.google.com/maps?q=x&output=embed"}
    patch_server("build_traffic_widget", fake_traffic)
    wt, pfx, cfg = await m.build_router_widget(
        {"type": "traffic", "query": "traffic in LA", "modifiers": {"traffic": True}}, "s", "msg")
    assert wt == "iframe_app" and "output=embed" in cfg["url"]
    # The router must ASSERT traffic intent rather than let it be re-inferred from
    # the query text. It classifies type='traffic' and passes the place alone
    # ("east bay"), so a re-grep for the word "traffic" finds nothing and silently
    # builds the plain directions embed with no TomTom overlay.
    assert seen["force_traffic"] is True


@pytest.mark.asyncio
async def test_resolve_ticker(patch_server):
    import app.main as m
    assert await m._resolve_ticker("TSLA") == "TSLA"      # bare symbol passes through
    async def fake_news(q, limit=8, **kw):
        return {"news": [], "matches": [{"symbol": "AAPL", "name": "Apple Inc."}]}
    patch_server("stock_news", fake_news)
    assert await m._resolve_ticker("Apple") == "AAPL"     # name resolves via matches
