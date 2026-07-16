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
    assert '<iframe src="https://example.com/x"' in html
    assert "open_in_new" in html  # the "open full app" link is present


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
async def test_build_list_add_merges_without_dupes(monkeypatch):
    import app.main as m

    async def fake_llm(_instruction, max_tokens=400):
        return {"items": ["feta cheese", "cucumber", "pork ribs"]}  # "pork ribs" dupes

    monkeypatch.setattr(m, "fast_llm_json", fake_llm)
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
async def test_build_list_remove(monkeypatch):
    import app.main as m
    async def fake_llm(_instruction, max_tokens=400):
        return {"remove": ["carrots", "broccoli"]}
    monkeypatch.setattr(m, "fast_llm_json", fake_llm)
    items = [{"text": "carrots", "done": False},
             {"text": "milk", "done": True},
             {"text": "broccoli", "done": False}]
    out = await m.build_list_remove_config("delete the veggies", "Groceries", items)
    texts = [i["text"] for i in out["items"]]
    assert texts == ["milk"]                 # only the two veggies removed
    assert out["items"][0]["done"] is True   # remaining item's state preserved


@pytest.mark.asyncio
async def test_build_list_remove_no_match_keeps_list(monkeypatch):
    import app.main as m
    async def fake_llm(_instruction, max_tokens=400):
        return {"remove": []}
    monkeypatch.setattr(m, "fast_llm_json", fake_llm)
    items = [{"text": "milk", "done": False}]
    out = await m.build_list_remove_config("delete the veggies", "Groceries", items)
    assert [i["text"] for i in out["items"]] == ["milk"]  # unchanged, not emptied
