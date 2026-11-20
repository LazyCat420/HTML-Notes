"""Async and sync SSRF guards validating initial URLs and every redirect hop."""

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


async def safe_public_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_redirects: int = 3,
    **kwargs,
) -> Optional[httpx.Response]:
    """Perform an HTTP request validating the initial URL and every redirect hop."""
    current = url
    for _ in range(max_redirects + 1):
        if not await _is_public_http_url_async(current):
            return None
        resp = await client.request(method, current, follow_redirects=False, **kwargs)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        loc = resp.headers.get("location")
        if not loc:
            return None
        current = urllib.parse.urljoin(current, loc)
    return None
