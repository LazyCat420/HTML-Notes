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
