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
