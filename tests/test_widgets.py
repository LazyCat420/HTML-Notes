import pytest
from app.widgets.factory import generate_widget_html

def test_youtube_player_widget_escapes_quotes():
    config = {
        "video_id": "ey_GaPdC9zk",
        "title": "One man just liberated Fable... and now it's illegal"
    }
    
    html_output = generate_widget_html("youtube_player", "widget-123", config)
    
    # Verify the output doesn't contain raw single quotes wrapping the title in x-data
    # Previously: youtubePlayerWidget('ey_GaPdC9zk', 'One man just liberated Fable... and now it's illegal')
    # Now it should be json_escaped: youtubePlayerWidget(&quot;ey_GaPdC9zk&quot;, &quot;One man just liberated Fable... and now it&#x27;s illegal&quot;)
    
    assert "youtubePlayerWidget(" in html_output
    assert "One man just liberated Fable" in html_output
    # Check that double quotes are escaped to &quot; and single quote is escaped to &#x27;
    assert "&quot;ey_GaPdC9zk&quot;" in html_output
    assert "now it&#x27;s illegal&quot;" in html_output
    
    # Also verify double quotes in title are escaped
    config_with_double_quotes = {
        "video_id": "ey_GaPdC9zk",
        "title": 'He said "Hello"'
    }
    html_output_dq = generate_widget_html("youtube_player", "widget-123", config_with_double_quotes)
    # json.dumps('He said "Hello"') -> "He said \"Hello\""
    # html.escape -> &quot;He said \&quot;Hello\&quot;&quot;
    assert r"&quot;He said \&quot;Hello\&quot;&quot;" in html_output_dq

def test_checklist_widget_escapes_quotes():
    config = {
        "title": "User's checklist",
        "items": ["Task 1", {"text": 'Task "2"', "done": False}]
    }
    html_output = generate_widget_html("checklist", "widget-456", config)
    
    assert "checklistWidget(" in html_output
    assert "&quot;User&#x27;s checklist&quot;" in html_output
    assert r"&quot;text&quot;: &quot;Task \&quot;2\&quot;&quot;" in html_output

# ─── Contract widgets (data_card / image / chart) ────────────────

def test_data_card_renders_items_with_images_and_links():
    config = {
        "title": "Top News",
        "subtitle": "Updated hourly",
        "items": [
            {"title": "Big <Story>", "description": "Something & happened",
             "image": "https://example.com/a.jpg", "url": "https://example.com/story",
             "badge": "World", "meta": "Reuters"},
            {"title": "No image item"},
        ],
    }
    html_output = generate_widget_html("data_card", "news-1", config)
    assert 'id="news-1"' in html_output
    assert "close-widget-btn" in html_output
    # Data is escaped and baked in server-side
    assert "Big &lt;Story&gt;" in html_output
    assert "Something &amp; happened" in html_output
    assert 'src="https://example.com/a.jpg"' in html_output
    assert 'href="https://example.com/story"' in html_output
    assert "Reuters" in html_output
    # Item without image falls back to a monogram tile
    assert "item-thumb" in html_output
    assert ">N</span>" in html_output  # monogram letter of "No image item"

def test_data_card_falls_back_to_content_then_config_dump():
    html_content = generate_widget_html("data_card", "card-2", {"title": "Answer", "content": "Line one\nLine two"})
    assert "Line one" in html_content and "Line two" in html_content

    html_dump = generate_widget_html("data_card", "card-3", {"title": "Odd", "value": "42"})
    assert "42" in html_dump  # raw config rendered, never a blank card

def test_image_widget_renders_urls_and_empty_state():
    config = {"title": "Aurora", "images": [{"url": "https://example.com/x.png", "caption": "Night sky"}]}
    html_output = generate_widget_html("image", "img-1", config)
    assert 'src="https://example.com/x.png"' in html_output
    assert "Night sky" in html_output
    assert "close-widget-btn" in html_output

    empty = generate_widget_html("image", "img-2", {"title": "Nothing"})
    assert "No image available" in empty

def test_chart_widget_bakes_config_and_falls_back():
    html_output = generate_widget_html("chart", "chart-1", {"title": "BTC", "type": "line", "labels": ["Mon", "Tue"], "values": [1, 2]})
    assert "language-chart" in html_output
    assert "Mon" in html_output
    # Unusable data degrades to a data card, not an error
    fallback = generate_widget_html("chart", "chart-2", {"title": "Broken"})
    assert "language-chart" not in fallback
    assert 'id="chart-2"' in fallback

def test_unknown_widget_type_degrades_to_data_card():
    html_output = generate_widget_html("mystery_thing", "w-1", {"content": "hello"})
    assert "hello" in html_output
    assert "Unknown widget type" not in html_output
    assert "Mystery Thing" in html_output

# ─── Heuristic intent guards ─────────────────────────────────────

def test_video_ask_overrides_widget_keywords():
    from app.main import VIDEO_ASK_RE, DATA_ASK_RE, ANSWER_ASK_RE
    # "clock for video" must be treated as a video ask, not a clock spawn
    assert VIDEO_ASK_RE.search("pull up a clock for video")
    assert VIDEO_ASK_RE.search("play a video of a clock")
    assert not VIDEO_ASK_RE.search("add a clock widget")
    # data asks reach the dedicated data paths / agent
    assert DATA_ASK_RE.search("show me the news")
    assert not DATA_ASK_RE.search("add a checklist")
    # recipes are now a synthesised ANSWER card, not a raw data ask
    assert ANSWER_ASK_RE.search("find me a recipe with chicken")
    assert not DATA_ASK_RE.search("find me a recipe with chicken")

def test_youtube_player_bakes_candidates_and_query():
    config = {"video_id": "abc123DEF45", "title": "2Pac - I Get Around",
              "candidates": ["xyz987GHI65", "qrs456JKL21"], "query": "2pac i get around"}
    out = generate_widget_html("youtube_player", "yt-1", config)
    assert "youtubePlayerWidget(" in out
    assert "xyz987GHI65" in out and "qrs456JKL21" in out
    assert "2pac i get around" in out
    assert "Watch on YouTube" in out

# ─── Content signature (client reconcile keys on this to avoid widget stutter) ──

def test_every_widget_carries_a_content_signature():
    # The client reconciler leaves a live widget node (and its playing iframe /
    # audio) untouched when its data-sig is unchanged, instead of diffing rendered
    # HTML — which is unreliable once Alpine mutates the DOM. So every widget must
    # be stamped, on the root element next to its id.
    for wtype in ("data_card", "youtube_player", "mini_music_player", "clock",
                  "checklist", "stock_card", "scoreboard", "weather", "chart",
                  "notes", "image", "iframe_app", "some_unknown_type"):
        out = generate_widget_html(wtype, f"w-{wtype}", {"title": "X"})
        assert 'data-sig="' in out, f"{wtype} is missing data-sig"
        assert f'id="w-{wtype}" data-sig="' in out, f"{wtype} data-sig not on the root"

def test_content_signature_is_stable_and_content_addressed():
    from app.widgets.factory import _content_sig
    # Same config (any key order) → same sig → unchanged widget is left alone.
    a = _content_sig("youtube_player", {"video_id": "abc", "title": "X"})
    b = _content_sig("youtube_player", {"title": "X", "video_id": "abc"})
    assert a == b
    # New content → new sig → the widget correctly re-renders.
    assert a != _content_sig("youtube_player", {"video_id": "DEF", "title": "X"})
    # Same config, different widget type → different sig.
    assert a != _content_sig("chart", {"video_id": "abc", "title": "X"})

def test_singleton_media_sig_changes_when_song_changes():
    # A new song in the (id-reusing) music player must change the sig so the
    # client re-renders it; an unrelated re-render with the same genre must not.
    from app.widgets.factory import _content_sig
    jazz = _content_sig("mini_music_player", {"genre": "jazz"})
    assert jazz == _content_sig("mini_music_player", {"genre": "jazz"})
    assert jazz != _content_sig("mini_music_player", {"genre": "rock"})


def test_products_widget_image_links_to_source():
    """The products grid must show a reference photo per item and make the whole
    card a link to the source, so clicking the picture opens the buy/read page."""
    config = {
        "title": "Outdoor Shoes",
        "items": [
            {"title": "Salomon X Ultra", "description": "Great trail grip",
             "image": "https://ex.com/a.jpg", "url": "https://rei.com/salomon",
             "price": "$150", "meta": "rei.com"},
            {"title": "Merrell Moab", "url": "https://amazon.com/moab"},  # no image
        ],
    }
    html_output = generate_widget_html("products", "products-1", config)
    assert "products-grid" in html_output
    # The image card is wrapped in an anchor to its source.
    assert 'href="https://rei.com/salomon"' in html_output
    assert "https://ex.com/a.jpg" in html_output
    assert "$150" in html_output
    # No-image item still renders a clickable card (monogram fallback).
    assert 'href="https://amazon.com/moab"' in html_output
    # Content-sig is stamped so the client reconciler tracks it.
    assert "data-sig=" in html_output


def test_products_widget_never_blank():
    """Empty items degrade to a friendly empty state, never a broken frame."""
    html_output = generate_widget_html("products", "products-2", {"title": "X", "items": []})
    assert "No recommendations found" in html_output


def test_trip_and_shop_intent_routing():
    """The trip/shopping fast-paths must catch their asks without stealing plain
    map / restaurant / news queries."""
    from app.main import TRIP_ASK_RE, SHOP_ASK_RE, extract_trip_destination
    trip_yes = ["plan me a trip to japan", "3 days in Rome", "kyoto itinerary",
                "things to do in Lisbon"]
    trip_no = ["map of japan", "weather in tokyo", "best budget laptop"]
    for q in trip_yes:
        assert TRIP_ASK_RE.search(q.lower()), q
    for q in trip_no:
        assert not TRIP_ASK_RE.search(q.lower()), q

    shop_yes = ["help me find good outdoor shoes", "best budget laptop",
                "where to buy a tent", "gift for a hiker"]
    shop_no = ["best restaurants in nyc", "stock price of tesla",
               "plan a trip to japan", "news about shoes"]
    for q in shop_yes:
        assert SHOP_ASK_RE.search(q.lower()), q
    for q in shop_no:
        assert not SHOP_ASK_RE.search(q.lower()), q

    assert extract_trip_destination("plan me a trip to japan") == "japan"


def test_strip_citation_markers():
    """Stray [N] source markers are removed from answer prose, but real markdown
    links and numbered-list markers survive."""
    from app.main import _strip_citation_markers
    assert _strip_citation_markers("Visit Kyoto [0, 2, 3] and Osaka [1].") == "Visit Kyoto and Osaka."
    assert _strip_citation_markers("See [guide](http://x.com)") == "See [guide](http://x.com)"


# ── Markdown tables ──────────────────────────────────────────────
# build_answer_config's prompt tells the summariser "a comparison -> a Markdown
# table", but _render_markdown had no table branch, so the rows fell through to
# the paragraph handler — which joins lines with " ". A comparison card rendered
# as one wrapped blob of pipes and dashes. The prompt asked for something the
# renderer could not draw.

def test_markdown_table_renders_as_a_table():
    from app.widgets.factory import _render_markdown
    html = _render_markdown(
        "| Category | Model |\n| :--- | :--- |\n| Value | Apex V2 |\n| Milk | Bambino |")
    assert "<table" in html and "<thead" in html
    assert html.count("<td") == 4, "2 rows x 2 cols"
    assert "|" not in html, "no raw pipe should survive into the output"


def test_table_cells_still_escape_and_take_inline_markdown():
    from app.widgets.factory import _render_markdown
    html = _render_markdown(
        "| A | B |\n| --- | --- |\n| **bold** | <script>x</script> |")
    assert "<strong" in html, "cells run through _md_inline"
    assert "<script>" not in html, "a cell must never emit live markup"


def test_ragged_rows_are_padded_to_the_header_width():
    """A short row must not produce a table with uneven columns."""
    from app.widgets.factory import _render_markdown
    html = _render_markdown("| A | B | C |\n| --- | --- | --- |\n| only-one |")
    assert html.count("<td") == 3


def test_pipes_without_a_separator_are_not_a_table():
    """Prose that merely contains a pipe stays prose."""
    from app.widgets.factory import _render_markdown
    html = _render_markdown("| this is just | text with pipes")
    assert "<table" not in html


def test_music_player_carries_kind_base_and_queue_ui():
    # The widget is a thin client over the music-player service: routing's
    # genre/artist guess (kind) and the service base URL must reach the Alpine
    # component, and the queue UI must be in the server template.
    out = generate_widget_html(
        "mini_music_player", "w-music",
        {"genre": "jungle", "kind": "genre", "autoplay": True})
    assert "musicPlayerWidget({ genre: &quot;jungle&quot;, kind: &quot;genre&quot;," in out
    assert "base: &quot;http" in out, "service base URL must be baked in"
    assert "queue_music" in out, "queue toggle button missing"
    assert 'x-for="item in upcoming"' in out, "queue panel rows missing"
    assert "streamStatus" in out, "SSE progress line missing"
    assert "{{" not in out, "unsubstituted f-string braces leaked into HTML"


def test_music_player_kind_changes_the_signature():
    from app.widgets.factory import _content_sig
    # A genre→artist correction must re-render (new sig); a same-config re-ask
    # must NOT tear down a playing widget (same sig).
    genre_sig = _content_sig("mini_music_player", {"genre": "jungle", "kind": "genre", "autoplay": True})
    artist_sig = _content_sig("mini_music_player", {"genre": "jungle", "kind": "artist", "autoplay": True})
    same_sig = _content_sig("mini_music_player", {"kind": "genre", "genre": "jungle", "autoplay": True})
    assert genre_sig != artist_sig
    assert genre_sig == same_sig
