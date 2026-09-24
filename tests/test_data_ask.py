"""Every factory template stamps data-ask on its natural click targets, so
a headline, a team, a row, a person or a marker is one click from a
follow-up. The utterance is what the user would have typed; the client's
delegated handler (tests/test_data_ask.mjs) sends it through HN.ask.
"""
import pytest

from app.widgets import factory
from app.widgets.factory import generate_widget_html


def test_data_card_headline_asks_for_more_in_place():
    html = generate_widget_html("data_card", "news-1", {
        "title": "News", "items": [{"title": "Fed holds rates", "description": "d",
                                    "url": "https://r.com/a"}]})
    assert 'data-ask="tell me more about Fed holds rates"' in html
    assert 'data-ask-focus="1"' in html, "a headline follow-up refines THIS card"


def test_attribute_value_is_escaped():
    html = generate_widget_html("data_card", "news-2", {
        "title": "News", "items": [{"title": 'He said "no" <b>', "description": "d"}]})
    assert 'data-ask="tell me more about He said &quot;no&quot; &lt;b&gt;"' in html


def test_products_ask_about_the_product():
    html = generate_widget_html("products", "prod-1", {
        "title": "Sandals", "items": [{"name": "Teva Hurricane", "price": "$70"}]})
    assert 'data-ask="more about Teva Hurricane"' in html


def test_table_rows_ask_about_their_first_column():
    html = generate_widget_html("table", "tbl-1", {
        "title": "EVs", "columns": [{"key": "model", "label": "Model"},
                                    {"key": "range", "label": "Range", "format": "number"}],
        "rows": [{"model": "Lucid Air", "range": 516}]})
    assert '<tr' in html and 'data-ask="Lucid Air"' in html


def test_scoreboard_team_asks_for_team_news():
    html = generate_widget_html("scoreboard", "scores-1", {
        "league": "nba", "title": "NBA",
        "events": [{"name": "LAL @ BOS", "status": "final", "state": "post",
                    "home": {"name": "Boston Celtics", "score": "101", "winner": True},
                    "away": {"name": "Los Angeles Lakers", "score": "99"}}]})
    assert 'data-ask="Boston Celtics news"' in html
    assert 'data-ask="Los Angeles Lakers news"' in html


def test_profile_card_name_asks_for_news():
    html = generate_widget_html("profile_card", "prof-1", {
        "title": "Marie Curie", "subtitle": "Physicist", "facts": [{"label": "Born", "value": "1867"}]})
    assert 'data-ask="Marie Curie news"' in html


def test_map_popup_carries_an_ask_button_that_posts_to_the_parent():
    doc = factory.map_document_html({"center": {"lat": 0, "lon": 0}, "zoom": 3,
                                     "markers": [{"lat": 1, "lon": 2, "label": "Seattle"}]})
    assert "hn-ask" in doc
    assert "parent.postMessage" in doc
    assert "'tell me about '" in doc and "'weather in '" in doc


def test_music_queue_has_no_ask_and_app_tile_carries_bound_asks():
    music = generate_widget_html("mini_music_player", "music-1", {"genre": "jazz"})
    assert ":data-ask=" not in music, "music queue must never carry data-ask to prevent click hijacking"
    grid = generate_widget_html("app_grid", "app-hub", {"apps": [], "curation": {}})
    assert ":data-ask=\"'tell me about ' + app.name\"" in grid
