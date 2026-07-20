"""What gets READ ALOUD must describe the answer, not the filing.

The agent is told (SYSTEM_PROMPT rule 5) to write one substantive sentence, but
the FAST LOOP closes its stream the moment the canvas commits — deliberately, to
skip a slow closing turn — so that sentence usually never arrives. The fallback
was a canned "Added it to your canvas.", spoken aloud, which told a user who
wasn't looking at the screen nothing at all. Twice in a row, in the reported
session.

So the sentence is derived server-side from the config that was just rendered.
These guards pin the two properties that make it useful: it says what the widget
SHOWS, and it survives the TTS endpoint's character filter.
"""
import re

import pytest

from app.main import _spoken_summary

# The TTS endpoint's sanitiser (app/main.py, /tts/synthesize). Anything it strips
# is silently lost from the spoken line, so summaries must not depend on it.
TTS_ALLOWED = re.compile(r'[^\w\s\.,!\?\-\'":;À-ÿ]')


class TestSaysTheFindingNotTheFiling:
    @pytest.mark.parametrize("wtype,config,expected", [
        ("map", {"title": "Traffic: San Jose"}, "Live traffic for San Jose."),
        ("weather", {"location": "Tokyo"}, "Here's the forecast for Tokyo."),
        ("stock_card", {"symbol": "AAPL"}, "Here's AAPL, with its chart and fundamentals."),
        ("scoreboard", {"league": "nba"}, "Here are the latest NBA scores."),
        ("mini_music_player", {"genre": "lo-fi"}, "Playing lo-fi."),
    ])
    def test_deterministic_widgets_describe_their_subject(self, wtype, config, expected):
        assert _spoken_summary(wtype, config) == expected

    def test_a_content_card_speaks_its_answer(self):
        cfg = {"title": "Hats", "answer": "The Seattle Rain Hat is the most waterproof, "
                                          "using Gore-Tex. It costs about 60 dollars."}
        # The FIRST sentence — the finding — not the whole essay.
        assert _spoken_summary("data_card", cfg) == (
            "The Seattle Rain Hat is the most waterproof, using Gore-Tex.")

    def test_content_alias_is_spoken_too(self):
        """`content` is the documented alias for `answer`; a card using it must
        not fall through to the title."""
        assert _spoken_summary("data_card", {"title": "T", "content": "Rosin is cheapest at Sunset."}) == (
            "Rosin is cheapest at Sunset.")

    def test_a_list_card_names_what_it_found(self):
        cfg = {"title": "News", "items": [{"title": "US and Iran tensions escalate"},
                                          {"title": "Tropical depression forms"}]}
        assert _spoken_summary("data_card", cfg) == (
            "Found 2, starting with US and Iran tensions escalate.")

    def test_a_single_item_is_not_counted_at_the_user(self):
        cfg = {"items": [{"title": "Fever The Ghost - SOURCE"}]}
        assert _spoken_summary("data_card", cfg) == "Found Fever The Ghost - SOURCE."

    @pytest.mark.parametrize("wtype,config", [
        ("map", {"title": "Traffic: San Jose"}),
        ("data_card", {"answer": "The hat is waterproof."}),
        ("weather", {"location": "Tokyo"}),
        ("iframe_app", {"title": "San Ramon → San Jose"}),
    ])
    def test_never_mentions_the_machinery(self, wtype, config):
        """'Added a card to the canvas' is the failure mode being fixed."""
        spoken = _spoken_summary(wtype, config).lower()
        for banned in ("canvas", "widget", "card to", "i added", "added it"):
            assert banned not in spoken


class TestSurvivesTheTTSFilter:
    def test_arrow_becomes_a_spoken_word(self):
        """A directions title is "A → B". The TTS endpoint strips → entirely, so
        without translation it is read as two place names and no relationship."""
        spoken = _spoken_summary("iframe_app", {"title": "San Ramon → San Jose"})
        assert spoken == "Showing San Ramon to San Jose."
        assert "→" not in spoken

    def test_ampersand_becomes_a_spoken_word(self):
        assert _spoken_summary("data_card", {"title": "Rosin & Wax"}) == "Rosin and Wax."

    @pytest.mark.parametrize("wtype,config", [
        ("iframe_app", {"title": "San Ramon → San Jose"}),
        ("data_card", {"title": "Rosin & Wax"}),
        ("data_card", {"answer": "**Bold** and `code` and [link](http://x.com) removed."}),
        ("map", {"title": "Traffic: San Jose"}),
    ])
    def test_nothing_survives_that_the_tts_filter_would_strip(self, wtype, config):
        """Any character the endpoint strips is silently lost from the audio."""
        spoken = _spoken_summary(wtype, config)
        assert TTS_ALLOWED.sub("", spoken) == spoken, (
            f"{spoken!r} contains characters the TTS endpoint strips")

    def test_markdown_is_removed_not_read_out(self):
        spoken = _spoken_summary("data_card", {"answer": "**The Seattle Rain Hat** wins."})
        assert "*" not in spoken and spoken == "The Seattle Rain Hat wins."


class TestFallsBackSafely:
    def test_a_bare_title_is_punctuated_as_a_sentence(self):
        """Otherwise TTS runs it into whatever is spoken next."""
        assert _spoken_summary("data_card", {"title": "Top stories"}) == "Top stories."

    @pytest.mark.parametrize("config", [{}, {"title": ""}, None, "not a dict"])
    def test_nothing_to_say_returns_empty_so_the_caller_can_fall_back(self, config):
        assert _spoken_summary("data_card", config) == ""

    def test_a_long_answer_is_clipped_not_dumped(self):
        cfg = {"answer": "word " * 200}
        spoken = _spoken_summary("data_card", cfg)
        assert len(spoken) <= 221
