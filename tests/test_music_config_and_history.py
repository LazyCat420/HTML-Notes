"""Music widget: the server's config survives the client, and play history is
accepted by the music-player service.

Two live bugs from 2026-11-03 ("buckethead instrumentals" played jazz; every
track change POSTed /api/music/history → 422). The suites that should have
caught them only grepped source text (test_ghost_audio_and_history.mjs checks
that the string "/api/music/history" exists), so they stayed green. These tests
run the REAL code on both sides:

- the server template from app/widgets/factory.py,
- the self-heal helpers sliced out of app/static/index.js (run in node),
- the musicPlayerWidget factory from app/static/js/widgets.js (run in node via
  tests/_music_widget_harness.mjs),
- the PlayEvent pydantic model from the music-player repo (read-only), compiled
  and used to validate the payload the widget builds.
"""
import ast
import glob
import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.widgets.factory import render_mini_music_player

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INDEX_JS = ROOT / "app" / "static" / "index.js"


def _node() -> str:
    found = shutil.which("node") or next(iter(sorted(
        glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")), reverse=True)), None)
    if not found:
        pytest.skip("node is not installed")
    return found


def _run_node(script: str) -> dict:
    res = subprocess.run([_node(), "--input-type=module", "-e", script],
                         cwd=str(HERE), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


# ── self-heal ───────────────────────────────────────────────────────────────

class _RootAttrs(HTMLParser):
    """Attributes of the first element, plus its direct child-element count
    and whether an <iframe> exists anywhere inside it."""

    def __init__(self):
        super().__init__()
        self.attrs, self.depth, self.children, self.iframe = None, 0, 0, False

    def handle_starttag(self, tag, attrs):
        if self.attrs is None:
            self.attrs = dict(attrs)
            self.depth = 1
            return
        if self.depth == 1:
            self.children += 1
        if tag == "iframe":
            self.iframe = True
        self.depth += 1

    def handle_endtag(self, tag):
        if self.attrs is not None:
            self.depth -= 1


def _selfheal(attrs: dict, children: int, has_iframe: bool) -> dict:
    """Run musicWidgetNeedsRebuild + recoverMusicWidgetConfig from index.js."""
    script = f"""
import {{ readFileSync }} from "node:fs";
const src = readFileSync({json.dumps(str(INDEX_JS))}, "utf8");
const a = src.indexOf("function musicWidgetNeedsRebuild");
const b = src.indexOf("// ─── END MUSIC SELF-HEAL HELPERS");
if (a < 0 || b < 0) throw new Error("self-heal helpers not found in index.js");
const helpers = new Function(src.slice(a, b) + "; return {{ musicWidgetNeedsRebuild, recoverMusicWidgetConfig }};")();
const attrs = {json.dumps(attrs)};
const el = {{
  getAttribute: (k) => (k in attrs ? attrs[k] : null),
  querySelector: (sel) => (sel === "iframe" && {json.dumps(has_iframe)}) ? {{}} : null,
  children: {{ length: {children} }},
}};
console.log(JSON.stringify({{
  needs: helpers.musicWidgetNeedsRebuild(el),
  cfg: helpers.recoverMusicWidgetConfig(el),
}}));
"""
    return _run_node(script)


def test_server_rendered_music_widget_is_not_rebuilt_and_keeps_the_ask():
    html = render_mini_music_player("music-buckethead", {
        "genre": "buckethead instrumentals", "kind": "artist", "autoplay": True})
    p = _RootAttrs()
    p.feed(html)
    assert p.attrs and "data-hn-config" in p.attrs, "server must stamp data-hn-config"
    out = _selfheal(p.attrs, p.children, p.iframe)
    assert out["needs"] is False, "a fresh server node must never be rebuilt"
    assert out["cfg"]["genre"] == "buckethead instrumentals"
    assert out["cfg"]["kind"] == "artist"
    assert out["cfg"]["autoplay"] is True


def test_legacy_positional_node_is_rebuilt_with_its_own_genre():
    out = _selfheal({"id": "music-old", "x-data": "musicPlayerWidget('smooth jazz', true)"},
                    children=1, has_iframe=False)
    assert out["needs"] is True
    assert out["cfg"]["genre"] == "smooth jazz"


def test_options_node_without_data_config_recovers_genre_from_x_data():
    out = _selfheal({"x-data": 'musicPlayerWidget({ genre: "primus", kind: "artist", autoplay: true })'},
                    children=0, has_iframe=False)
    assert out["needs"] is True
    assert out["cfg"]["genre"] == "primus" and out["cfg"]["kind"] == "artist"


def test_nothing_recoverable_never_invents_a_genre():
    out = _selfheal({"id": "music-bare"}, children=0, has_iframe=False)
    assert out["needs"] is True
    assert out["cfg"]["genre"] == "", "no hardcoded fallback genre"


def test_self_heal_twin_mounts_the_same_walkman_iframe_as_the_server():
    """index.js keeps a structural twin of the server template for nodes that
    lost their markup. Config no longer lives in it (data-hn-config does), but
    the iframe that hosts the card must stay identical or a healed node is a
    different widget."""
    import re
    html = render_mini_music_player("music-x", {"genre": "jazz"})
    server_iframe = re.search(r"<iframe[^>]*></iframe>", html).group(0)
    src = INDEX_JS.read_text()
    block = src[src.index("if (musicWidgetNeedsRebuild(widget))"):]
    twin_iframe = re.search(r"<iframe[^>]*></iframe>", block).group(0)
    assert twin_iframe == server_iframe


# ── play history contract ───────────────────────────────────────────────────

def _music_player_history_py() -> Path:
    for parent in HERE.parents:
        cand = parent / "media" / "music-player" / "apps" / "api" / "app" / "routers" / "history.py"
        if cand.exists():
            return cand
    pytest.skip("music-player repo not checked out next to html-notes")


def _play_event_model():
    """Compile the music-player's real PlayEvent class (read-only source)."""
    src = _music_player_history_py().read_text()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PlayEvent")
    ns: dict = {}
    exec(compile(ast.Module(body=[
        ast.parse("from pydantic import BaseModel").body[0], cls], type_ignores=[]),
        "music-player/history.py", "exec"), ns)
    return ns["PlayEvent"]


def _widget(js_expr: str) -> dict:
    script = f"""
import {{ loadFactory }} from "./_music_widget_harness.mjs";
const {{ factory }} = loadFactory();
const w = factory({{ genre: "jazz", autoplay: false, base: "http://svc" }});
w.duration = 213.4;
console.log(JSON.stringify({js_expr}));
"""
    return _run_node(script)


def test_play_event_from_widget_validates_against_music_player_model():
    PlayEvent = _play_event_model()
    body = _widget('w.buildPlayEvent({ id: "cb2w2m1JmCY", isYoutube: true, '
                   'title: "Take the A Train", artist: "Duke Ellington" })')
    ev = PlayEvent(**body)               # raises ValidationError on the old body → 422
    assert ev.track_id == "youtube://cb2w2m1JmCY"
    assert ev.duration == 213
    assert ev.context == "html-notes"


def test_old_history_body_is_what_the_service_rejected():
    """Red witness: the pre-fix body really fails the real model."""
    from pydantic import ValidationError
    PlayEvent = _play_event_model()
    with pytest.raises(ValidationError):
        PlayEvent(track_path="cb2w2m1JmCY", title="t", artist="a", is_youtube=True,
                  youtube_id="cb2w2m1JmCY", duration=0, source="html-notes")


def test_recent_history_rows_map_into_the_widget():
    # Columns of music-player database.get_recently_played's SELECT.
    rows = [{"track_id": "youtube://Cv9NSR-2DwM", "artist": "Art Blakey", "title": "Moanin'",
             "album": "", "source": "youtube", "last_played": "2026-11-03T19:00:00Z"},
            {"track_id": "/music/local.flac", "artist": "X", "title": "Y",
             "album": "", "source": "local", "last_played": "2026-11-03T18:00:00Z"}]
    out = _widget(f"w.mapServerHistory({json.dumps(rows)})")
    assert out[0]["id"] == "Cv9NSR-2DwM" and out[0]["isYoutube"] is True
    assert out[1]["id"] == "/music/local.flac" and out[1]["isYoutube"] is False
