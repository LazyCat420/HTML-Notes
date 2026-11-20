"""Async and sync SSRF guards validating initial URLs, DNS pinning, and redirect hops."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import urllib.parse
from typing import Optional

import httpx

_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal"})


def _ip_is_public(host: str) -> Optional[bool]:
    """Return False if host is a non-public IP literal, True if public, or None if not an IP."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return not (
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
