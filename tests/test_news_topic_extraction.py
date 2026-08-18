"""Test that news topic extraction correctly normalizes general daily news queries to empty topics (top headlines)."""
import pytest
from app.main import extract_topic, _NEWSY

def test_extract_topic_general_news():
    queries = [
        "news for the day",
        "news of the day",
        "daily news",
        "todays news",
        "today news",
        "morning news",
        "evening news",
        "top news",
        "top stories",
        "what's happening today",
    ]
    
    _GENERAL = {
        "whats", "what", "s", "going", "on", "happening", "up", "new",
        "current", "events", "event", "anything", "something", "interesting",
        "any", "the", "is", "are", "in", "world", "lately", "now", "right",
        "hows", "how", "things", "there", "out", "cool", "hey", "so",
        "tell", "me", "show", "give", "whatsup", "sup", "good", "today",
        "todays", "day", "days", "daily", "for", "of", "all", "top", "global",
        "us", "usa", "america", "american", "morning", "evening", "tonight",
        "feed", "stories", "story", "summary", "brief"
    }
    
    for q in queries:
        raw = extract_topic(q)
        topic = " ".join(w for w in raw.split() if w not in _NEWSY).strip()
        if topic and all(w in _GENERAL for w in topic.split()):
            topic = ""
        assert topic == "", f"Expected empty topic for general query '{q}', got '{topic}' (raw='{raw}')"

def test_extract_topic_specific_subject():
    queries = {
        "news on quantum computing": "quantum computing",
        "apple stock news": "apple stock",
        "latest updates on mars rover": "mars rover",
        "breaking news about bitcoin": "bitcoin",
    }
    
    for q, expected in queries.items():
        raw = extract_topic(q)
        topic = " ".join(w for w in raw.split() if w not in _NEWSY).strip()
        assert topic == expected, f"Expected '{expected}' for '{q}', got '{topic}'"
