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
