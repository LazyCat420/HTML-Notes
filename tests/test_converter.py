"""Calculator/unit/currency converter widget: server-side seeding + routing.
The math/unit/currency computation itself is client-side (Alpine) and covered
by the live E2E; these pin the tab classification and the wiring."""
import os
import re

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_converter.db")

from app import main as m
from app.widgets.factory import generate_widget_html


@pytest.mark.parametrize("q,tab", [
    ("40% of 1250", "calc"),
    ("what is 15*23", "calc"),
    ("(3+4)*2", "calc"),
    ("5 miles in km", "units"),
    ("convert 10 kg to lb", "units"),
    ("350 f to c", "units"),
    ("20 usd to eur", "currency"),
    ("$50 to gbp", "currency"),
    ("100 jpy in usd", "currency"),
])
def test_seed_picks_the_right_tab(q, tab):
    assert m.build_converter_config(q)["tab"] == tab


def test_seed_is_carried_and_capped():
    cfg = m.build_converter_config("  40% of 1250  ")
    assert cfg["seed"] == "40% of 1250"
    assert len(m.build_converter_config("x" * 500)["seed"]) <= 120


# The ask that started this: a cooking-time QUESTION, dense with numbers and
# units, rendered a unit/currency calculator on 2026-07-31.
CHICKEN = ("145F chicken breast with the carcass how long to get to 165 its been "
           "cooking for about 25 minutes in the oven at 400F. I think 10 minutes "
           "should work?")

CONVERSION_TABLE = [
    # the ask IS the arithmetic -> converter
    ("20 usd to eur", True),
    ("5 miles in km", True),
    ("350 f to c", True),
    ("40% of 1250", True),
    ("(3+4)*2", True),
    ("convert 5 miles to km", True),
    ("convert 10 kg to lb", True),
    ("what is 15*23", True),
    ("calculate 12% of 340", True),
    # a QUESTION that merely CONTAINS numbers and units -> research
    (CHICKEN, False),
    ("how long should I cook 5 lb in the oven", False),
    ("how long to drive to SF", False),
    ("is 145 f safe for chicken", False),
    ("what temp should I pull a brisket at", False),
    ("should I give it 10 more minutes", False),
    # not conversion-shaped at all
    ("NVDA vs SPY", False),
    ("tell me about dogs", False),
    ("weather in tokyo", False),
    ("set a 5 minute timer", False),
    ("remind me in 20 minutes", False),
]


@pytest.mark.parametrize("q,expect", CONVERSION_TABLE)
def test_is_conversion_ask(q, expect):
    assert m.is_conversion_ask(q) is expect


def test_the_chicken_question_is_not_a_conversion():
    """The live 2026-07-31 misroute, asserted on its own so a regression names
    the actual bug rather than one row of a table."""
    assert not m.is_conversion_ask(CHICKEN), (
        "a cooking-time question routed to the unit converter — the whole point "
        "of is_conversion_ask")


def test_explicit_convert_verb_beats_the_question_veto():
    """CALC_IMPERATIVE_RE short-circuits: 'recipe', 'cups' and 'baking' are all
    NUMERIC_QUESTION_RE tokens, but the user literally said convert."""
    assert m.is_conversion_ask("convert my recipe from cups to grams for baking")


def test_bare_expression_asks_reach_the_fast_path():
    """"what is 15*23" matches nothing in CONVERT_INTENT_RE (its arithmetic arm
    is anchored ^...$), so before CALC_IMPERATIVE_RE it paid a full agent turn."""
    assert not m.CONVERT_INTENT_RE.search("what is 15*23")
    assert m.is_conversion_ask("what is 15*23")


@pytest.mark.parametrize("q,expect", CONVERSION_TABLE)
def test_fastpath_uses_the_shared_predicate(q, expect):
    """These used to re-implement the predicate inline, so they would have gone
    on passing while production diverged from them."""
    assert m.is_conversion_ask(q) is expect


def test_fastpath_calls_the_shared_predicate_in_source():
    from tests._sources import SERVER_SRC as src
    assert "if is_conversion_ask(text_clean):" in src
    assert "CONVERT_INTENT_RE.search(text_clean)" not in src, (
        "the always-on fast path must go through is_conversion_ask so all three "
        "converter entry points share one definition")


def test_converter_is_registered_and_renders():
    from app.widgets.factory import WIDGET_RENDERERS
    assert "converter" in WIDGET_RENDERERS
    html = generate_widget_html("converter", "conv-1", {"seed": "20 usd to eur", "tab": "currency"})
    assert "converterWidget(" in html
    assert "&quot;20 usd to eur&quot;" in html
    assert "Calc" in html and "Units" in html and "Currency" in html
    assert html.count("{{") == 0


@pytest.mark.asyncio
async def test_fx_rejects_bad_base():
    # Non 3-letter codes are rejected without a network call (deterministic).
    assert await m.fetch_fx_rates("nonsense") == {}
    assert await m.fetch_fx_rates("12") == {}


def test_router_and_prompt_know_converter():
    assert "converter" in m.ROUTER_WIDGETS
    from tests._sources import SERVER_SRC as src
    assert "widget_type='converter'" in src


# ── Reminder widget ──────────────────────────────────────────────────────────
import pytest as _pt

@_pt.mark.parametrize("q,label,off,at", [
    ("remind me in 20 minutes", "Reminder", 1200, ""),
    ("remind me to take out the trash in 2 hours", "take out the trash", 7200, ""),
    ("remind me at 3pm to call mom", "call mom", 0, "15:00"),
    ("set an alarm for 7am", "Reminder", 0, "07:00"),
    ("alarm for 6:30am", "Reminder", 0, "06:30"),
    ("remind me at noon to eat lunch", "eat lunch", 0, "12:00"),
])
def test_reminder_parse(q, label, off, at):
    c = m.build_reminder_config(q)
    assert c["label"].lower() == label.lower()
    assert c["offset_seconds"] == off
    assert c["at_time"] == at


def test_reminder_tomorrow_flag():
    assert m.build_reminder_config("remind me tomorrow at 9am")["tomorrow"] is True
    assert m.build_reminder_config("remind me at 9am")["tomorrow"] is False


@_pt.mark.parametrize("q,fires", [
    ("remind me in 20 minutes", True), ("set an alarm for 7am", True),
    ("reminder to water plants", True),
    ("set a 5 minute timer", False), ("stopwatch", False), ("what time is it", False),
])
def test_reminder_fastpath(q, fires):
    assert bool(m.REMINDER_INTENT_RE.search(q)) == fires


def test_reminder_registered_and_renders():
    from app.widgets.factory import WIDGET_RENDERERS
    assert "reminder" in WIDGET_RENDERERS
    html = generate_widget_html("reminder", "rem-1",
                                {"label": "call mom", "offset_seconds": 0, "at_time": "15:00"})
    assert "reminderWidget(" in html and "&quot;call mom&quot;" in html
    assert "at_time: &quot;15:00&quot;" in html
    assert html.count("{{") == 0


def test_new_widgets_classified_on_canvas():
    for xdata, wt in [("converterWidget", "converter"), ("reminderWidget", "reminder"),
                      ("settingsWidget", "settings")]:
        assert m._CANVAS_XDATA_TYPE[xdata] == wt
