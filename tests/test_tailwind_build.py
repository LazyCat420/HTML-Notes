"""Tailwind is compiled at build time now; nothing styles classes in the browser.

Until 2026-11-03 html-notes loaded cdn.tailwindcss.com, a JIT that styled ANY
class that appeared in the DOM. The compiled app/static/tailwind.css only holds
classes Tailwind's extractor found in the files tailwind.config.js scans (plus
the saved-canvas safelist). A class the server BUILDS at runtime — an f-string
like f"text-{tone}-400" in app/widgets/factory.py — is invisible to that scan
and would silently lose its style.

This test renders every widget type through the real render_widget, then asks
Tailwind itself which of the emitted classes are Tailwind utilities (a second
build with exactly those classes safelisted and no content), and requires every
one of them to be in the production build. It also pins the shell: no CDN
script, and tailwind.css linked last (where the CDN injected its <style>).
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.canvas_manager import render_widget
from app.widgets.factory import WIDGET_RENDERERS

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "app" / "static" / "index.html"
TW_BIN = ROOT / "node_modules" / ".bin" / "tailwindcss"

# Configs that reach the conditional branches (colour by sign, images, lists).
SAMPLES = {
    "data_card": [{"title": "T", "answer": "A [0].", "subtitle": "s",
                   "items": [{"title": "i", "description": "d", "url": "https://x.y/a",
                              "image": "https://x.y/i.jpg", "meta": "m", "badge": "News"}]}],
    "stock_card": [{"symbol": "NVDA", "price": 100, "change": 2.5, "change_percent": 2.5},
                   {"symbol": "NVDA", "price": 100, "change": -2.5, "change_percent": -2.5}],
    "crypto_card": [{"symbol": "BTC", "price": 1, "change_percent": 3},
                    {"symbol": "BTC", "price": 1, "change_percent": -3}],
    "weather": [{"location": "SF", "temperature": 60, "condition": "Rain",
                 "forecast": [{"day": "Mon", "high": 60, "low": 50, "condition": "Sun"}]}],
    "scoreboard": [{"league": "nba", "games": [{"home": "A", "away": "B", "home_score": 1,
                                                "away_score": 2, "status": "Final"}]}],
    "checklist": [{"title": "L", "items": [{"text": "a", "done": True}, {"text": "b", "done": False}]}],
    "image": [{"title": "I", "images": [{"url": "https://x.y/i.jpg", "title": "t"}]}],
    "products": [{"title": "P", "items": [{"title": "p", "url": "https://x.y", "image": "https://x.y/i.jpg",
                                           "price": "$1"}]}],
    "table": [{"title": "T", "columns": ["a", "b"], "rows": [["1", "2"]]}],
    "kpi_row": [{"title": "K", "items": [{"label": "a", "value": "1", "delta": "+2%"},
                                         {"label": "b", "value": "2", "delta": "-2%"}]}],
    "timeline": [{"title": "T", "events": [{"date": "2026", "title": "e", "description": "d"}]}],
    "versus_card": [{"title": "V", "left": {"name": "a"}, "right": {"name": "b"},
                     "rows": [{"label": "x", "left": "1", "right": "2"}]}],
    "progress": [{"title": "P", "items": [{"label": "a", "value": 30, "max": 100}]}],
    "mini_music_player": [{"genre": "jazz", "kind": "genre", "autoplay": True}],
    "youtube_player": [{"video_id": "dQw4w9WgXcQ", "title": "t"}],
}


def _rendered_classes() -> set[str]:
    classes = set()
    for wtype in WIDGET_RENDERERS:
        for i, cfg in enumerate(SAMPLES.get(wtype, []) + [{}]):
            try:
                html = render_widget(wtype, f"{wtype}-{i}", dict(cfg))
            except Exception:
                continue
            for m in re.finditer(r'\sclass="([^"]*)"', html):
                classes.update(m.group(1).split())
    return classes


def _build(tmp: Path, name: str, config_js: str) -> str:
    cfg = tmp / f"{name}.config.js"
    out = tmp / f"{name}.css"
    cfg.write_text(config_js)
    res = subprocess.run([str(TW_BIN), "-c", str(cfg), "-i", str(ROOT / "tailwind.input.css"),
                          "-o", str(out)], cwd=str(ROOT), capture_output=True, text=True,
                         timeout=120)
    assert res.returncode == 0, res.stderr
    return out.read_text()


def _selectors(css: str) -> set[str]:
    # Class selectors as Tailwind escapes them: ".h-\[456px\]", ".hover\:bg-x:hover"
    return set(re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", css))


@pytest.mark.skipif(not TW_BIN.exists() or not shutil.which("node") and not any(
        Path(os.path.expanduser("~/.nvm/versions/node")).glob("*/bin/node")),
    reason="tailwind CLI not installed (npm ci)")
def test_every_runtime_rendered_utility_is_in_the_compiled_css(tmp_path):
    env_path = os.environ.get("PATH", "")
    nvm = sorted(Path(os.path.expanduser("~/.nvm/versions/node")).glob("*/bin"))
    if nvm and not shutil.which("node"):
        os.environ["PATH"] = f"{nvm[-1]}:{env_path}"
    rendered = _rendered_classes()
    assert len(rendered) > 200, "render sweep found too few classes"
    prod = _selectors(_build(tmp_path, "prod", (ROOT / "tailwind.config.js").read_text()
                             .replace('require("./tailwind.safelist.json")',
                                      f'require({json.dumps(str(ROOT / "tailwind.safelist.json"))})')
                             .replace('"./app/', f'"{ROOT}/app/').replace('"!./app/', f'"!{ROOT}/app/')))
    probe_cfg = ("module.exports = { content: [{ raw: '' }], safelist: "
                 + json.dumps(sorted(rendered)) + " };")
    utilities = _selectors(_build(tmp_path, "probe", probe_cfg))
    missing = sorted(u for u in utilities if u not in prod)
    assert not missing, f"utilities rendered at runtime but not compiled: {missing[:40]}"


def test_shell_loads_no_tailwind_runtime_and_links_the_build_last():
    html = INDEX_HTML.read_text()
    assert not re.search(r'<script[^>]+src="[^"]*tailwindcss', html), "no Tailwind runtime script"
    head = html[:html.index("</head>")]
    sheets = re.findall(r'<link rel="stylesheet" href="([^"?]+)', head)
    assert sheets[-1] == "tailwind.css", f"tailwind.css must be the last stylesheet: {sheets}"
