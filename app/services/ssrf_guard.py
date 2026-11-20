"""Async and sync SSRF guards validating initial URLs, DNS pinning, and redirect hops."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import urllib.parse
from typing import Optional

import httpx

_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal"})


def _ip_is_public(host: str) -> Optional[bool]:
    """Return False if host is non-public/non-global IP literal, True if global public, or None if not an IP."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return bool(ip.is_global) and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _check_resolved_addrs(infos) -> bool:
    """True when socket.getaddrinfo returned at least one address and all are public."""
    for info in infos:
        if _ip_is_public(info[4][0]) is not True:
            return False
    return bool(infos)


def _extract_candidate_host(url: str) -> Optional[str]:
    """Return normalized hostname for http(s) URLs not on known internal hosts."""
    try:
        parsed = urllib.parse.urlparse(url or "")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname
    lower = host.lower()
    if lower in _BLOCKED_HOSTS or lower.endswith(".local"):
        return None
    return host


def _is_public_http_url(url: str) -> bool:
    """True only for http(s) URLs whose host resolves exclusively to public addresses."""
    host = _extract_candidate_host(url)
    if not host:
        return False
    ip_verdict = _ip_is_public(host)
    if ip_verdict is not None:
        return ip_verdict
    try:
        return _check_resolved_addrs(socket.getaddrinfo(host, None))
    except OSError:
        return False


async def _is_public_http_url_async(url: str) -> bool:
    """Async SSRF check running DNS resolution off the event loop."""
    host = _extract_candidate_host(url)
    if not host:
        return False
    ip_verdict = _ip_is_public(host)
    if ip_verdict is not None:
        return ip_verdict
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        return _check_resolved_addrs(infos)
    except OSError:
        return False


async def _resolve_pinned_public_target(url: str) -> Optional[tuple[str, str, Optional[str]]]:
    """Resolve and pin a public URL to its validated IP to prevent DNS rebinding TOCTOU."""
    host = _extract_candidate_host(url)
    if not host:
        return None
    parsed = urllib.parse.urlparse(url)
    ip_verdict = _ip_is_public(host)
    if ip_verdict is False:
        return None
    if ip_verdict is True:
        return url, parsed.netloc, None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except OSError:
        return None
    if not _check_resolved_addrs(infos):
        return None
    resolved_ip = infos[0][4][0]
    ip_host = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    pinned_netloc = f"{ip_host}:{parsed.port}" if parsed.port else ip_host
    connect_url = urllib.parse.urlunparse(parsed._replace(netloc=pinned_netloc))
    sni_host = host if parsed.scheme == "https" else None
    return connect_url, parsed.netloc, sni_host


def _merge_pinned_request_kwargs(host_header: str, sni_host: Optional[str], base_kwargs: dict) -> dict:
    """Prepare request kwargs preserving Host header, SNI isolation, and TLS sni_hostname for pinned IPs."""
    req_kwargs = dict(base_kwargs)
    headers = dict(req_kwargs.get("headers") or {})
    if not any(k.lower() == "host" for k in headers):
        headers["Host"] = host_header
    if sni_host:
        # Prevent httpcore pooling (keyed by IP, ignoring SNI) from reusing a socket across hosts.
        if not any(k.lower() == "connection" for k in headers):
            headers["Connection"] = "close"
        ext = dict(req_kwargs.get("extensions") or {})
        ext.setdefault("sni_hostname", sni_host)
        req_kwargs["extensions"] = ext
    req_kwargs["headers"] = headers
    return req_kwargs


async def safe_public_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_redirects: int = 3,
    **kwargs,
) -> Optional[httpx.Response]:
    """Perform an HTTP request pinning DNS resolution on the initial URL and every redirect hop."""
    current = url
    req_method = method
    for _ in range(max_redirects + 1):
        target = await _resolve_pinned_public_target(current)
        if target is None:
            return None
        connect_url, host_header, sni_host = target
        req_kwargs = _merge_pinned_request_kwargs(host_header, sni_host, kwargs)
        resp = await client.request(req_method, connect_url, follow_redirects=False, **req_kwargs)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        loc = resp.headers.get("location")
        if not loc:
            return None
        if resp.status_code == 303:
            req_method = "GET"
        current = urllib.parse.urljoin(current, loc)
    return None


_ALLOWED_TEXT_TYPES = frozenset({"", "text/html", "application/xhtml+xml", "text/plain"})
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _is_allowed_text_type(content_type: str) -> tuple[bool, str]:
    """Return (allowed, media_type) allowing only HTML/plain-text responses."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    return ctype in _ALLOWED_TEXT_TYPES, ctype


async def _read_bounded_response(resp: httpx.Response, max_bytes: int) -> Optional[str]:
    """Decode response body up to max_bytes, rejecting declared oversized payloads."""
    raw_len = (resp.headers.get("content-length") or "").strip()
    if raw_len.isdigit() and int(raw_len) > max_bytes:
        return None
    if hasattr(resp, "aiter_bytes"):
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            if not chunk:
                continue
            need = max_bytes - total
            chunks.append(chunk[:need])
            total += len(chunk)
            if total > max_bytes:
                return None
        encoding = getattr(resp, "encoding", None) or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")
    raw_content = getattr(resp, "content", b"")
    if isinstance(raw_content, (bytes, bytearray)) and len(raw_content) > max_bytes:
        return None
    text = resp.text or ""
    if len(text.encode("utf-8", errors="replace")) > max_bytes:
        return None
    return text


async def _fetch_text_once(
    client: httpx.AsyncClient,
    connect_url: str,
    req_kwargs: dict,
    max_bytes: int,
) -> tuple[int, str, str, Optional[str]]:
    """Fetch one pinned hop via streaming when available, returning (status, ctype, text, location)."""
    if hasattr(client, "stream"):
        async with client.stream("GET", connect_url, follow_redirects=False, **req_kwargs) as resp:
            if resp.status_code in _REDIRECT_CODES:
                return resp.status_code, "", "", resp.headers.get("location")
            if resp.status_code >= 400:
                return resp.status_code, "", "", None
            ok_type, ctype = _is_allowed_text_type(resp.headers.get("content-type", ""))
            if not ok_type:
                return 415, ctype, "", None
            body = await _read_bounded_response(resp, max_bytes)
            if body is None:
                return 413, ctype, "", None
            return resp.status_code, ctype, body, None
    resp = await client.request("GET", connect_url, follow_redirects=False, **req_kwargs)
    if resp.status_code in _REDIRECT_CODES:
        return resp.status_code, "", "", resp.headers.get("location")
    if resp.status_code >= 400:
        return resp.status_code, "", "", None
    ok_type, ctype = _is_allowed_text_type(resp.headers.get("content-type", ""))
    if not ok_type:
        return 415, ctype, "", None
    body = await _read_bounded_response(resp, max_bytes)
    if body is None:
        return 413, ctype, "", None
    return resp.status_code, ctype, body, None


async def safe_public_read_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = 2_097_152,
    max_redirects: int = 3,
    headers: Optional[dict] = None,
) -> tuple[Optional[int], str, str]:
    """GET HTML/plain text through DNS pinning and bounded streaming across redirects."""
    current = url
    base_kwargs = {"headers": dict(headers or {})}
    for _ in range(max_redirects + 1):
        target = await _resolve_pinned_public_target(current)
        if target is None:
            return None, "", ""
        connect_url, host_header, sni_host = target
        req_kwargs = _merge_pinned_request_kwargs(host_header, sni_host, base_kwargs)
        code, ctype, body, loc = await _fetch_text_once(client, connect_url, req_kwargs, max_bytes)
        if code not in _REDIRECT_CODES:
            return code, ctype, body
        if not loc:
            return None, "", ""
        current = urllib.parse.urljoin(current, loc)
    return None, "", ""


_CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary", "search", "menubar", "dialog", "alertdialog"})
_CHROME_NAME_RE = re.compile(r"(?:cookie|consent|gdpr|navbar|nav-bar|menubar|breadcrumb|sidebar|masthead|modal|newsletter)", re.I)


def strip_non_article_chrome(soup) -> None:
    """Decompose navigation, header, footer, aside, form, script, and banner containers in place."""
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe", "canvas", "nav", "header", "footer", "aside", "form", "button", "dialog"]):
        tag.decompose()
    for el in list(soup.find_all(attrs={"role": True})):
        if str(el.get("role") or "").strip().lower() in _CHROME_ROLES:
            el.decompose()
    for el in list(soup.find_all(True)):
        if getattr(el, "attrs", None) is None or el.name in ("html", "body", "article", "main") or el.find(["article", "main"]):
            continue
        tokens = " ".join([str(el.get("id") or ""), *(el.get("class") or [])])
        if tokens and _CHROME_NAME_RE.search(tokens):
            el.decompose()


def extract_article_text(body: str, content_type: str) -> str:
    """Extract main readable article text from bounded HTML or plain-text bodies."""
    raw = body or ""
    if "html" not in (content_type or "").lower():
        return " ".join(raw.split())
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    strip_non_article_chrome(soup)
    primary = [n for n in soup.select("article, main, [role='main']") if len(" ".join(n.get_text(" ", strip=True).split())) >= 120]
    if primary:
        best = max(primary, key=lambda n: len(" ".join(n.get_text(" ", strip=True).split())))
        return " ".join(best.get_text(" ", strip=True).split())
    scored = [(len(ptxt), blk) for blk in soup.find_all(["section", "div"]) if (ptxt := " ".join(p.get_text(" ", strip=True) for p in blk.find_all("p"))) and len(ptxt) >= 120]
    if scored:
        return " ".join(max(scored, key=lambda item: item[0])[1].get_text(" ", strip=True).split())
    return " ".join((soup.body or soup).get_text(" ", strip=True).split())
