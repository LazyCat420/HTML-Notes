"""Security regression tests for TCK-1013..TCK-1018."""
import json
import pathlib
import re
import secrets
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import canvas_manager, database
from app.main import app
from app.routes.internal import internal_tool_execute
from app.main import InternalToolRequest
from app.services.search import _is_public_http_url_async, safe_public_request
from app.widgets.factory import _safe_dom_id, generate_widget_html

STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "static"
client = TestClient(app)

CFG = {
    "title": "Security probe",
    "html": "<p>ok</p>",
    "css": "",
    "js": "container.textContent = 'ran';",
}


def test_internal_execute_fails_closed_503_when_no_token_configured(monkeypatch, patch_server):
    monkeypatch.delenv("INTERNAL_EXECUTE_TOKEN", raising=False)
    monkeypatch.delenv("RUNTIME_AUTH_SECRET", raising=False)
    patch_server("_fetch_secret", AsyncMock(return_value=""))

    res = client.post(
        "/internal/execute",
        json={"tool": "html_notes_search_notes", "args": {"query": "test"}},
    )
    assert res.status_code == 503


def test_internal_execute_requires_matching_token_when_configured(monkeypatch, patch_server):
    token = f"tok_{secrets.token_hex(16)}"
    monkeypatch.delenv("RUNTIME_AUTH_SECRET", raising=False)
    patch_server("_fetch_secret", AsyncMock(return_value=token))

    missing = client.post(
        "/internal/execute",
        json={"tool": "html_notes_search_notes", "args": {"query": "test"}},
    )
    assert missing.status_code == 401

    wrong = client.post(
        "/internal/execute",
        headers={"x-internal-token": f"wrong_{secrets.token_hex(8)}"},
        json={"tool": "html_notes_search_notes", "args": {"query": "test"}},
    )
    assert wrong.status_code == 401

    valid = client.post(
        "/internal/execute",
        headers={"x-internal-token": token},
        json={"tool": "html_notes_search_notes", "args": {"query": "test"}},
    )
    assert valid.status_code == 200


@pytest.mark.asyncio
async def test_internal_execute_direct_python_call_without_http_request(monkeypatch, patch_server):
    monkeypatch.delenv("INTERNAL_EXECUTE_TOKEN", raising=False)
    monkeypatch.delenv("RUNTIME_AUTH_SECRET", raising=False)
    patch_server("_fetch_secret", AsyncMock(return_value=""))

    out = await internal_tool_execute(
        InternalToolRequest(tool="html_notes_search_notes", args={"query": "hello"}),
        request=None,
    )
    assert isinstance(out, dict) and "results" in out


def test_custom_widget_route_sends_sandbox_csp_and_nosniff():
    database.set_widget_state("custom:widget-sec00001", json.dumps(CFG))
    res = client.get("/widgets/custom/widget-sec00001")
    assert res.status_code == 200
    assert res.headers.get("content-security-policy") == "sandbox allow-scripts"
    assert res.headers.get("x-content-type-options") == "nosniff"


def test_user_memory_rejects_missing_header_and_null_origin():
    assert client.get("/user/memory").status_code == 403
    assert client.delete("/user/memory").status_code == 403

    res_null = client.get(
        "/user/memory",
        headers={"X-HN-Client": "1", "Origin": "null"},
    )
    assert res_null.status_code == 403

    del_null = client.delete(
        "/user/memory",
        headers={"X-HN-Client": "1", "Origin": "null"},
    )
    assert del_null.status_code == 403

    ok_get = client.get(
        "/user/memory",
        headers={"X-HN-Client": "1", "Origin": "http://10.0.0.16:8035"},
    )
    assert ok_get.status_code == 200

    ok_del = client.delete(
        "/user/memory",
        headers={"X-HN-Client": "1", "Origin": "http://10.0.0.16:8035"},
    )
    assert ok_del.status_code == 200
    assert ok_del.json()["ok"] is True


def test_widget_id_sanitization_blocks_attribute_injection():
    evil_id = 'bad" onclick="alert(1)'
    cleaned_cm = canvas_manager.sanitize_widget_id(evil_id)
    cleaned_fac = _safe_dom_id(evil_id)
    for cid in (cleaned_cm, cleaned_fac):
        assert re.fullmatch(r"[A-Za-z0-9_-]+", cid), cid
        assert '"' not in cid and " " not in cid

    rendered = generate_widget_html("clock", evil_id, {"title": "Clock"})
    assert 'onclick=' not in rendered
    assert 'x-init="alert' not in rendered


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_private_hosts_and_unsafe_redirects():
    blocked = [
        "http://127.0.0.1:8035/",
        "http://10.0.0.16:8888/",
        "http://100.64.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://user:pass@93.184.216.34/",
        "file:///etc/passwd",
    ]
    for url in blocked:
        assert await _is_public_http_url_async(url) is False, url

    seen_urls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append((request.method, str(request.url)))
        if request.url.host == "93.184.216.34" and request.url.path == "/redirect-loopback":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8035/secret"})
        if request.url.host == "93.184.216.34" and request.url.path == "/redirect-303":
            return httpx.Response(303, headers={"Location": "/public"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as hc:
        assert await safe_public_request(hc, "GET", "https://user:pass@93.184.216.34/public") is None
        res_redirect = await safe_public_request(hc, "GET", "https://93.184.216.34/redirect-loopback")
        assert res_redirect is None
        assert all("127.0.0.1" not in u for _, u in seen_urls)

        res_303 = await safe_public_request(hc, "POST", "https://93.184.216.34/redirect-303")
        assert res_303 is not None and res_303.status_code == 200
        assert ("GET", "https://93.184.216.34/public") in seen_urls

        res_ok = await safe_public_request(hc, "GET", "https://93.184.216.34/public")
        assert res_ok is not None and res_ok.status_code == 200

    import socket
    from unittest.mock import patch
    pinned_calls: list[tuple[str, str, str | None, str | None]] = []

    def hostname_handler(request: httpx.Request) -> httpx.Response:
        pinned_calls.append((
            str(request.url),
            request.headers.get("host", ""),
            (request.extensions or {}).get("sni_hostname"),
            request.headers.get("connection"),
        ))
        return httpx.Response(200, text="pinned-ok")

    with patch("app.services.ssrf_guard.socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]):
        async with httpx.AsyncClient(transport=httpx.MockTransport(hostname_handler), follow_redirects=False) as hc:
            res_pinned = await safe_public_request(hc, "GET", "https://alpha.example/chart.png")
            assert res_pinned is not None and res_pinned.status_code == 200
            res_pinned2 = await safe_public_request(hc, "GET", "https://beta.example/chart.png")
            assert res_pinned2 is not None and res_pinned2.status_code == 200
    assert pinned_calls == [
        ("https://93.184.216.34/chart.png", "alpha.example", "alpha.example", "close"),
        ("https://93.184.216.34/chart.png", "beta.example", "beta.example", "close"),
    ]


@pytest.mark.asyncio
async def test_read_web_page_uses_pinned_safe_request_never_scraper_service(patch_server):
    import socket
    from contextlib import asynccontextmanager
    from unittest.mock import patch
    from app.services.search import read_web_page

    called_scraper = False

    async def fail_if_scrape(*a, **kw):
        nonlocal called_scraper
        called_scraper = True
        return ""

    patch_server("_scrape", fail_if_scrape)
    seen: list[tuple[str, str, str | None]] = []
    article_html = (
        "<html><head><script>bad()</script></head>"
        "<body><nav>Home Menu Cookie Banner Subscribe Now</nav>"
        "<article><p>" + ("Federal Reserve holds rates steady in article text. " * 6) + "</p></article>"
        "<footer>Copyright 2026 All Rights Reserved</footer></body></html>"
    )

    class _StreamResp:
        def __init__(self, status_code, headers, chunks):
            self.status_code = status_code
            self.headers = headers
            self._chunks = chunks
            self.encoding = "utf-8"

        async def aiter_bytes(self):
            for c in self._chunks:
                yield c

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        @asynccontextmanager
        async def stream(self, method, target_url, follow_redirects=False, headers=None, extensions=None, **kw):
            seen.append((str(target_url), (headers or {}).get("Host", ""), (extensions or {}).get("sni_hostname")))
            host = (headers or {}).get("Host", "")
            if "evil" in host:
                yield _StreamResp(302, {"location": "http://127.0.0.1:8035/secret"}, [])
            elif "pdf" in host:
                yield _StreamResp(200, {"content-type": "application/pdf"}, [b"%PDF-1.7"])
            elif "huge" in host:
                yield _StreamResp(200, {"content-type": "text/html"}, [b"x" * 1_500_000, b"y" * 1_500_000])
            else:
                yield _StreamResp(200, {"content-type": "text/html"}, [article_html.encode("utf-8")])

    patch_server("httpx", type("_H", (), {"AsyncClient": _FakeClient})())
    with patch("app.services.ssrf_guard.socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]):
        res = await read_web_page("https://news.example/story", max_chars=250)
        blocked = await read_web_page("https://evil.example/bounce")
        pdf_res = await read_web_page("https://pdf.example/report.pdf")
        huge_res = await read_web_page("https://huge.example/stream")

    assert called_scraper is False
    assert res.get("is_error") is not True
    assert res["content"].startswith("Federal Reserve holds rates steady")
    assert "Cookie Banner" not in res["content"]
    assert "Copyright 2026" not in res["content"]
    assert "bad()" not in res["content"]
    assert seen[0] == ("https://93.184.216.34/story", "news.example", "news.example")
    assert blocked["is_error"] is True
    assert pdf_res["is_error"] is True
    assert huge_res["is_error"] is True


def test_canvas_domain_service_upsert_sanitizes_returned_id():
    from app.domain.canvas.service import CanvasDomainService

    svc = CanvasDomainService()
    res = svc.upsert_widget("stock_card", "stock-brk.b", {"symbol": "BRK.B", "price": 450}, session_id="s-dom")
    assert res["success"] is True
    assert res["widget_id"] == "stock-brk-b"
    assert 'id="stock-brk-b"' in res["html"]

    empty_res = svc.upsert_widget("clock", "", {"title": "Clock"}, session_id="s-dom")
    assert empty_res["success"] is True
    assert empty_res["widget_id"] and empty_res["widget_id"] != "widget"
    assert f'id="{empty_res["widget_id"]}"' in empty_res["html"]


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


def test_browser_dompurify_scopes_alpine_attrs_to_canvas_only():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    index_js = (STATIC_DIR / "index.js").read_text(encoding="utf-8")
    cfg_match = re.search(
        r"const CANVAS_DOMPURIFY_CONFIG = (\{[\s\S]*?\n    \});",
        index_js,
    )
    assert cfg_match, "CANVAS_DOMPURIFY_CONFIG missing"
    canvas_fn = re.search(
        r"(function sanitizeCanvasHtml\(dirty\) \{[\s\S]*?\n    \})",
        index_js,
    )
    chat_fn = re.search(
        r"(function sanitizeChatHtml\(dirty\) \{[\s\S]*?\n    \})",
        index_js,
    )
    assert canvas_fn and chat_fn

    script_src = f"""
    const CANVAS_DOMPURIFY_CONFIG = {cfg_match.group(1)};
    {canvas_fn.group(1)}
    {chat_fn.group(1)}
    window.__testSanitizeCanvas = sanitizeCanvasHtml;
    window.__testSanitizeChat = sanitizeChatHtml;
    """

    exe = _find_chromium_executable()
    launch_kwargs = {"headless": True}
    if exe:
        launch_kwargs["executable_path"] = exe

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page()
            page.set_content("<!doctype html><html><body></body></html>")
            page.add_script_tag(path=str(STATIC_DIR / "lib" / "dompurify.min.js"))
            page.add_script_tag(content=script_src)

            res = page.evaluate("""() => {
                const payload = '<div x-data="{}" x-init="window.__pwn=1" @click="alert(1)">hi</div><img src=x onerror="window.__img=1">';
                const chatFirst = window.__testSanitizeChat(payload);
                const canvasOut = window.__testSanitizeCanvas('<div x-data="clockWidget()" @click="toggle()">ok</div>');
                const chatAfter = window.__testSanitizeChat('<span x-init="window.__pwn=2" :class="x">chat</span>');
                return { chatFirst, canvasOut, chatAfter, pwn: window.__pwn || 0, img: window.__img || 0 };
            }""")
        finally:
            browser.close()

    for attr in ("x-data", "x-init", "@click", "onerror"):
        assert attr not in res["chatFirst"], res["chatFirst"]
        assert attr not in res["chatAfter"], res["chatAfter"]
    assert ":class" not in res["chatAfter"], res["chatAfter"]
    assert 'x-data="clockWidget()"' in res["canvasOut"]
    assert '@click="toggle()"' in res["canvasOut"]
    assert res["pwn"] == 0 and res["img"] == 0
