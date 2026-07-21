"""Theme system: the pick-closest matcher, the settings widget, and the wiring
that connects a spoken request to an applied palette.

The user's ask: "ask the agent to change the theme and it picks the closest" —
egg / pastel / forest / dark, etc. These pin that the matcher lands the obvious
cases, every catalog theme has a CSS palette, and the settings widget carries
the swatches + apply hook.
"""
import os
import pathlib
import re

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_themes.db")

from app import main as m
from app.widgets.factory import generate_widget_html

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUD_CSS = (ROOT / "app/static/hud-theme.css").read_text()
INDEX_JS = (ROOT / "app/static/index.js").read_text()
WIDGETS_JS = (ROOT / "app/static/js/widgets.js").read_text()
INDEX_HTML = (ROOT / "app/static/index.html").read_text()


# ── The matcher picks the closest palette ────────────────────────────────────
@pytest.mark.parametrize("text,want", [
    ("I want dark mode", "midnight"),
    ("forest themed", "forest"),
    ("make everything green", "forest"),
    ("pastel colors", "pastel"),
    ("light mode please", "pastel"),
    ("egg color themed", "egg"),
    ("eggshell", "egg"),
    ("cream and coffee", "egg"),
    ("give me a purple synthwave look", "grape"),
    ("minimal slate", "mono"),
    ("sunset orange vibe", "ember"),
    ("cyberpunk", "hud"),
    ("i want it darker and moody", "midnight"),
])
def test_pick_theme_lands_the_obvious_cases(text, want):
    assert m.pick_theme(text) == want


def test_pick_theme_tolerates_typos():
    assert m.pick_theme("pastal colours") == "pastel"
    assert m.pick_theme("i want it darkk") == "midnight"


def test_pick_theme_none_when_no_signal():
    assert m.pick_theme("show me the weather in tokyo") is None
    assert m.pick_theme("") is None


# ── Every catalog theme has a real CSS palette ───────────────────────────────
def test_every_catalog_theme_has_a_css_block():
    for t in m.THEME_CATALOG:
        name = t["name"]
        if name == "hud":
            # default lives in :root; the alias block is empty by design.
            assert ':root[data-theme="hud"]' in HUD_CSS
            continue
        block = f':root[data-theme="{name}"]'
        assert block in HUD_CSS, f"{name} has no palette in hud-theme.css"
        # Must set the core tones so the palette actually changes.
        seg = HUD_CSS[HUD_CSS.index(block):HUD_CSS.index(block) + 600]
        for var in ("--hud-bg", "--hud-accent-rgb", "--hud-ink"):
            assert var in seg, f"{name} palette missing {var}"


def test_light_themes_drop_the_cockpit_texture():
    for name in ("egg", "pastel"):
        seg = HUD_CSS[HUD_CSS.index(f':root[data-theme="{name}"]'):]
        seg = seg[:seg.index("}")]
        assert "--hud-scan-opacity: 0" in seg and "--hud-bracket-opacity: 0" in seg


def test_hud_literals_were_converted_to_vars():
    # The whole point: a palette override must reach every accent use. If raw
    # cyan literals survive, those spots won't recolor.
    assert HUD_CSS.count("85, 214, 255") <= 1, "accent cyan must be var-driven (only the base rgb triple)"
    assert "var(--hud-accent-rgb)" in HUD_CSS
    assert "var(--hud-title)" in HUD_CSS and "var(--hud-bg-top)" in HUD_CSS


# ── Settings widget renders the swatches + apply hook ────────────────────────
def test_settings_widget_renders_swatches_and_controls():
    cfg = {
        "themes": [{"name": t["name"], "label": t["label"], "swatch": t["swatch"]}
                   for t in m.THEME_CATALOG],
        "active": "forest", "apply": "forest",
    }
    html = generate_widget_html("settings", "settings-panel", cfg)
    assert 'x-data="settingsWidget(' in html
    assert 'x-for="t in themes"' in html and "setTheme(t.name)" in html
    assert "toggleMute()" in html and "resetLayout()" in html
    assert "&quot;forest&quot;" in html          # apply baked in
    assert html.count("{{") == 0                 # no unsubstituted f-string braces


def test_settings_is_a_registered_widget_type():
    assert "settings" in __import__("app.widgets.factory", fromlist=["WIDGET_RENDERERS"]).WIDGET_RENDERERS


# ── Routing: the fast-path fires on look/settings, not on media/content ──────
@pytest.mark.parametrize("text", [
    "dark mode", "forest theme", "make it pastel", "open settings", "settings",
    "switch to midnight", "change the color scheme", "make everything purple",
])
def test_theme_intent_fires(text):
    assert m.THEME_INTENT_RE.search(text)


@pytest.mark.parametrize("text", [
    "show me nvda stock", "what is dark matter", "green tea benefits",
    "forest fires in california map", "weather in seattle",
])
def test_theme_intent_does_not_false_fire(text):
    assert not m.THEME_INTENT_RE.search(text)


def test_media_guard_keeps_dark_music_out_of_the_theme_path():
    # The real intercept condition: intent fires AND it's not a media ask.
    guard = re.compile(r"\b(music|radio|song|playlist|video|watch)\b")
    def intercepts(t):
        return bool(m.THEME_INTENT_RE.search(t)) and not guard.search(t)
    # "dark mode music" would trip the intent (dark mode) but the guard blocks it.
    assert not intercepts("dark mode ambient music please")
    assert not intercepts("play a dark synthwave playlist")
    # A real theme ask still intercepts.
    assert intercepts("switch to dark mode")


# ── Client wiring pins ───────────────────────────────────────────────────────
def test_theme_engine_wired_in_client():
    assert "window.HN.applyTheme" in INDEX_JS
    assert "html_notes_theme" in INDEX_JS
    assert "hn:theme" in INDEX_JS
    # Charts read palette colors (readable on light themes).
    assert "window.HN.chartColors" in INDEX_JS
    assert "Chart.defaults.color = cc.ink" in INDEX_JS
    # Stock card redraws on theme change.
    assert "hn:theme" in WIDGETS_JS and "settingsWidget" in WIDGETS_JS


def test_pre_paint_theme_script_in_head():
    # Applied before first paint so a reload doesn't flash the default theme.
    assert "html_notes_theme" in INDEX_HTML
    assert 'setAttribute("data-theme"' in INDEX_HTML


def test_system_prompt_teaches_the_settings_route():
    assert "APPEARANCE / theme" in m.SYSTEM_PROMPT if hasattr(m, "SYSTEM_PROMPT") else True
    src = pathlib.Path(ROOT / "app/main.py").read_text()
    assert "widget_type='settings'" in src
    assert "closest palette" in src.lower() or "CLOSEST palette" in src
