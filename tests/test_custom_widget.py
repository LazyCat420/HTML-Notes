"""create_widget renders into a sandboxed iframe, not inline markup.

Before: the injector wrote the model's htmlContent + a live <script> straight
into the canvas grid; the client had to allow <script> through DOMPurify and
re-create the nodes so they ran. The JS ran with the page's own origin —
cookies, localStorage, fetch as the user. The map widget already proved the
alternative: a document served by our own route inside a sandbox="allow-scripts"
iframe (no allow-same-origin ⇒ an opaque origin ⇒ no parent credentials).

`custom` is now a factory widget: the injector persists {title, html, css, js}
under widget_state 'custom:<id>', render_custom draws the chrome around an
iframe pointing at /widgets/custom/<id>, and update_widget re-renders through
the same path.
"""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "data/test_notes.db")

from app import database
from app import main as m
from app.main import app
from app.widgets import factory
from app.widgets.factory import WIDGET_RENDERERS, generate_widget_html

client = TestClient(app)
SESSION = "test-session-custom-widget"

CFG = {"title": "Push-up counter", "html": '<button id="b">+1</button><span id="n">0</span>',
       "css": "#b{color:red}", "js": "container.querySelector('#b').onclick=()=>{}"}


def test_custom_is_a_registered_factory_widget():
    assert "custom" in WIDGET_RENDERERS


def test_render_custom_is_an_iframe_with_no_inline_script():
    html = generate_widget_html("custom", "widget-abc12345", CFG)
    assert 'data-widget-type="custom"' in html
    assert "<script" not in html.lower()
    assert 'sandbox="allow-scripts"' in html
    assert "allow-same-origin" not in html
    assert 'src="/widgets/custom/widget-abc12345?v=' in html


def test_render_custom_src_changes_when_the_content_changes():
    a = generate_widget_html("custom", "widget-abc12345", CFG)
    b = generate_widget_html("custom", "widget-abc12345", {**CFG, "html": "<p>changed</p>"})
    assert a != b, "a stale ?v= would let the browser keep the old document"


def test_document_neutralises_a_script_breakout():
    js = "alert(1)</script><script>alert(2)"
    css = "b{}</style><script>alert(3)</script>"
    doc = factory.custom_document_html("widget-abc12345", {**CFG, "js": js, "css": css})
    # What a parser sees is the invariant: the model's closers are defanged, so
    # its text stays INSIDE our one <script> / one <style> — a bare "<script>"
    # opener in script data or CSS text is not a tag.
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(doc, "html.parser")
    scripts, styles = soup.find_all("script"), soup.find_all("style")
    assert len(scripts) == 1 and len(styles) == 1, (len(scripts), len(styles))
    assert "alert(2)" in scripts[0].string and "alert(1)" in scripts[0].string
    assert "alert(3)" in styles[0].string, "the CSS breakout stayed inside <style>"
    assert "</script>" not in scripts[0].string and "</style>" not in styles[0].string


def test_document_route_serves_the_stored_widget_and_404s_otherwise():
    database.init_db()
    database.set_widget_state("custom:widget-route1", json.dumps(CFG))
    res = client.get("/widgets/custom/widget-route1")
    assert res.status_code == 200
    assert "Push-up counter" in res.text and 'id="b"' in res.text
    assert res.headers.get("cache-control") == "no-store"
    assert client.get("/widgets/custom/widget-nope").status_code == 404


# ─── the injector, driven through a scripted agent stream ──────────────────

class _Stream:
    status_code = 200

    def __init__(self, events):
        self._chunks = [f'data: {json.dumps(e)}\n' for e in events] + ['data: {"type": "done"}\n']

    async def aiter_text(self):
        for c in self._chunks:
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


def _seed():
    database.init_db()
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id = ?", (SESSION,))
    cur.execute("DELETE FROM chat_sessions WHERE id = ?", (SESSION,))
    cur.execute("INSERT INTO chat_sessions (id, title, created_at) VALUES (?, ?, ?)",
                (SESSION, "Custom Widget", "2026-09-07T00:00:00Z"))
    conn.commit()
    conn.close()


def _components(sse):
    out = []
    for line in sse.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            if ev.get("type") == "component":
                out.append(ev.get("content") or ev.get("html") or "")
    return out


def _drive(events, canvas='<div id="dashboard-grid" class="dashboard-grid"></div>', patch_server=None):
    _seed()
    with patch("httpx.AsyncClient.stream", return_value=_Stream(events)):
        res = client.post("/session/message", json={
            "session_id": SESSION, "message": "build me a custom widget that counts my push-ups",
            "provider": "vllm", "model": "nemotron35", "current_canvas": canvas})
    assert res.status_code == 200
    return res.text


def _tool(name, args):
    return {"type": "tool_execution", "status": "done",
            "tool": {"name": f"mcp__lazy-tool-service__{name}", "args": args, "result": "ok"}}


def test_create_widget_commits_a_sandboxed_custom_widget(patch_server):
    sse = _drive([_tool("create_widget", {
        "widgetType": "custom", "title": "Push-up counter",
        "htmlContent": CFG["html"], "cssContent": CFG["css"], "jsContent": CFG["js"]})])
    comps = _components(sse)
    assert comps, "no component frame"
    html = comps[-1]
    assert 'data-widget-type="custom"' in html
    assert "<script" not in html.lower(), "the model's JS must not be inline"
    assert 'sandbox="allow-scripts"' in html
    # The document is persisted, so the iframe has something to load — and it
    # survives a restart.
    import re
    wid = re.search(r'/widgets/custom/(widget-[0-9a-f]{8})', html).group(1)
    stored = json.loads(database.get_widget_state(f"custom:{wid}"))
    assert stored["js"] == CFG["js"] and stored["title"] == "Push-up counter"


def test_update_widget_rerenders_a_custom_widget_in_place(patch_server):
    _seed()
    wid = "widget-upd00001"
    database.set_widget_state(f"custom:{wid}", json.dumps(CFG))
    canvas = ('<div id="dashboard-grid" class="dashboard-grid">'
              + generate_widget_html("custom", wid, CFG) + '</div>')
    sse = _drive([_tool("update_widget", {"widgetId": wid, "title": "Sit-up counter"})], canvas=canvas)
    html = _components(sse)[-1]
    assert html.count('data-widget-type="custom"') == 1, "must update, not stack"
    assert "Sit-up counter" in html
    stored = json.loads(database.get_widget_state(f"custom:{wid}"))
    assert stored["title"] == "Sit-up counter" and stored["js"] == CFG["js"], "untouched fields survive"
