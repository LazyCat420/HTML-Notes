"""Playwright layout, mobile command-bar, masonry, vendored libs & toolbar checks."""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.main import app

STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "static"
client = TestClient(app)


def _find_chromium_executable():
    cache = pathlib.Path.home() / ".cache" / "ms-playwright"
    for pat in (
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium-*/chrome-linux64/chrome",
    ):
        matches = sorted(cache.glob(pat), reverse=True)
        if matches:
            return str(matches[0])
    return None


def _attach_app_routes(page, external_urls: list[str], initial_widgets_html: str = ""):
    """Route http://hn.test/* through FastAPI TestClient without touching the network."""

    def handle(route):
        url = route.request.url
        if not url.startswith("http://hn.test"):
            external_urls.append(url)
            route.abort()
            return

        path = url.replace("http://hn.test", "", 1).split("?", 1)[0] or "/"
        if path == "/models":
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"models":[{"provider":"vllm","model":"m1","label":"Qwen3.8-Flash-Next"},{"provider":"vllm-2","model":"m2","label":"Qwen3.8-Flash-Next"}]}',
            )
            return
        if path == "/health/model":
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return
        if path == "/api/services":
            route.fulfill(status=200, content_type="application/json", body='{"apps":[]}')
            return
        if path.startswith("/session/") and path.endswith("/history"):
            import json
            payload = {
                "messages": (
                    [{"role": "assistant", "content": "ok", "canvas_html": initial_widgets_html}]
                    if initial_widgets_html
                    else []
                ),
                "canvas_version": 1,
                "closed_widgets": [],
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
            return
        if path.endswith("/canvas/dismiss"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"ok":true,"removed":true,"version":2,"closed":[{"id":"w-clock","title":"World Clock","widget_type":"clock"}]}',
            )
            return
        if path.endswith("/canvas/restore"):
            import json
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "restored_id": "w-clock",
                    "content": initial_widgets_html,
                    "version": 3,
                    "closed": [],
                }),
            )
            return
        if path.startswith("/session/") and path.endswith("/events"):
            route.fulfill(status=200, content_type="text/event-stream", body=": connected\n\n")
            return

        resp = client.get(path)
        ct = resp.headers.get("content-type", "application/octet-stream")
        route.fulfill(status=resp.status_code, content_type=ct, body=resp.content)

    page.route("**/*", handle)


def test_mobile_400px_desktop_1024_1440_and_masonry_and_toolbar():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    widgets_html = (
        '<div id="dashboard-grid" class="dashboard-grid">'
        '<div id="w-clock" class="widget-container" data-widget-type="clock" '
        'data-widget-title="World Clock" style="height:220px">'
        '<h3>World Clock</h3><div class="text-4xl">12:00:00</div></div>'
        '<div id="nba-player-stats" class="widget-container" data-widget-type="table" '
        'data-widget-title="NBA Player Stats" style="height:260px">'
        '<h3>NBA Player Stats</h3><p>Points per game table</p></div>'
        '</div>'
    )

    exe = _find_chromium_executable()
    launch_kwargs = {"headless": True}
    if exe:
        launch_kwargs["executable_path"] = exe

    external_urls: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page(viewport={"width": 400, "height": 844})
            _attach_app_routes(page, external_urls, widgets_html)
            page.goto("http://hn.test/", wait_until="domcontentloaded")
            page.wait_for_selector("#nba-player-stats")

            # Wait briefly for assets + masonry + model dropdown
            page.wait_for_function("() => !!window.marked && !!window.Chart && !!window.Alpine")
            page.wait_for_function(
                "() => document.querySelectorAll('#model-select option').length === 2"
            )

            # TCK-1036: no external cdn.jsdelivr.net fetches
            assert not any("cdn.jsdelivr.net" in u for u in external_urls), external_urls

            # TCK-1031: duplicate model labels disambiguated
            labels = page.eval_on_selector_all(
                "#model-select option", "opts => opts.map(o => o.textContent)"
            )
            assert len(set(labels)) == 2, labels

            # TCK-1021: nba-player-stats stays a table widget (NOT music player!)
            assert page.get_attribute("#nba-player-stats", "data-widget-type") == "table"
            assert "musicPlayerWidget" not in (page.get_attribute("#nba-player-stats", "x-data") or "")

            # TCK-1019: 400px mobile viewport clears 52px app-rail and gives #chat-input >= 200px
            mobile = page.evaluate("""() => {
                const rail = document.getElementById('app-rail').getBoundingClientRect();
                const pal = document.querySelector('.command-palette').getBoundingClientRect();
                const inp = document.getElementById('chat-input').getBoundingClientRect();
                return { railRight: rail.right, palLeft: pal.left, palRight: pal.right, inpWidth: inp.width };
            }""")
            assert mobile["palLeft"] >= 52, mobile
            assert mobile["palRight"] <= 400.5, mobile
            assert mobile["inpWidth"] >= 200, mobile

            # TCK-1020: masonry measures offsetHeight cleanly even during scaleY(0.02) transform
            masonry = page.evaluate("""() => {
                const card = document.getElementById('nba-player-stats');
                card.style.transform = 'scaleY(0.02)';
                window.__masonryLayout();
                return {
                    span: card.style.gridRowEnd,
                    rectHeight: card.getBoundingClientRect().height,
                    offsetHeight: card.offsetHeight
                };
            }""")
            span_num = int(masonry["span"].replace("span", "").strip())
            assert masonry["rectHeight"] < 20, masonry
            assert span_num >= masonry["offsetHeight"] >= 180, masonry

            # TCK-1026: user-resized height persists across re-renders via WidgetResizer
            resized = page.evaluate("""() => {
                const card = document.getElementById('nba-player-stats');
                card.style.transform = '';
                window.WidgetResizer.saveSize('nba-player-stats', { cols: 1, height: 320 });
                window.WidgetResizer.apply(card);
                window.__masonryLayout();
                return { offsetHeight: card.offsetHeight, span: card.style.gridRowEnd };
            }""")
            assert resized["offsetHeight"] >= 315, resized
            assert int(resized["span"].replace("span", "").strip()) >= 335, resized

            # Check 1024px and 1440px viewports + I2 toolbar + I1 dismiss/undo
            for vw, vh in ((1024, 768), (1440, 900)):
                page.set_viewport_size({"width": vw, "height": vh})
                desktop = page.evaluate("""() => ({
                    palLeft: document.querySelector('.command-palette').getBoundingClientRect().left,
                    inpWidth: document.getElementById('chat-input').getBoundingClientRect().width
                })""")
                assert desktop["palLeft"] >= 52 and desktop["inpWidth"] >= 300, (vw, desktop)

            # I2 search filtering
            page.fill("#canvas-search-input", "nba")
            assert page.evaluate(
                "() => document.getElementById('w-clock').classList.contains('is-filtered-out')"
            )
            assert not page.evaluate(
                "() => document.getElementById('nba-player-stats').classList.contains('is-filtered-out')"
            )
            page.click("#canvas-search-clear")
            assert not page.evaluate(
                "() => document.getElementById('w-clock').classList.contains('is-filtered-out')"
            )

            # I1 dismiss shows undo toast & Closed (1), Undo restores
            page.evaluate("() => window.WidgetManager.dismiss(document.getElementById('w-clock'))")
            page.wait_for_selector("#undo-dismiss-toast:not([hidden])")
            assert "Closed (1)" in page.text_content("#canvas-closed-btn")
            page.click(".undo-dismiss-btn")
            page.wait_for_selector("#w-clock")
        finally:
            browser.close()
