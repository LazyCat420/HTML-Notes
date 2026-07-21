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


@pytest.mark.parametrize("q", [
    "40% of 1250", "5 miles in km", "20 usd to eur", "(3+4)*2", "convert 10 kg to lb",
])
def test_fastpath_fires_on_conversions(q):
    fires = bool(m.CONVERT_INTENT_RE.search(q)) and not re.search(
        r"\b[A-Za-z]{1,5}\s+vs\.?\s+[A-Za-z]{1,5}\b", q)
    assert fires


@pytest.mark.parametrize("q", ["NVDA vs SPY", "tell me about dogs", "weather in tokyo"])
def test_fastpath_skips_non_conversions(q):
    fires = bool(m.CONVERT_INTENT_RE.search(q)) and not re.search(
        r"\b[A-Za-z]{1,5}\s+vs\.?\s+[A-Za-z]{1,5}\b", q)
    assert not fires


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
    src = __import__("pathlib").Path(m.__file__).read_text()
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
