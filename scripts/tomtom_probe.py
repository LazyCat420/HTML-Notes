#!/usr/bin/env python3
"""Definitively answer "is the TomTom traffic key working?" from inside the box.

Resolves TOMTOM_API_KEY the same way the app does (env first, then the vault
service), then fetches a real traffic-flow tile and prints TomTom's HTTP status.

    docker compose exec html-notes python scripts/tomtom_probe.py
    # or locally:  TOMTOM_API_KEY=xxxx python scripts/tomtom_probe.py

Reading of the result:
  200            → key is good; traffic tiles will render.
  403            → key invalid, product not enabled, or IP/referrer-locked. Note
                   our proxy sends NO referrer, so a referrer-restricted key fails
                   here — either unrestrict it or restrict by our server's IP.
  no key found   → it isn't in env or the vault; add it (developer.tomtom.com).
"""
import asyncio
import os
import sys

import httpx

VAULT_URL = os.getenv("VAULT_SERVICE_URL", "http://10.0.0.16:5599")
VAULT_TOKEN = os.getenv("VAULT_SERVICE_TOKEN", "")
# A Seattle-area tile at z13 — any valid key returns a PNG here.
TILE = ("https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/"
        "13/1310/2851.png?key={key}&tileSize=256")


async def resolve_key() -> str:
    key = os.getenv("TOMTOM_API_KEY", "") or ""
    if key:
        print(f"[key] found in env (len={len(key)})")
        return key
    if not VAULT_TOKEN:
        print("[key] not in env and no VAULT_SERVICE_TOKEN to ask the vault")
        return ""
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{VAULT_URL}/secrets", params={"keys": "TOMTOM_API_KEY"},
                            headers={"Authorization": f"Bearer {VAULT_TOKEN}"})
        if r.status_code == 200:
            key = str((r.json() or {}).get("TOMTOM_API_KEY", "") or "")
            print(f"[key] vault returned {'a value (len=%d)' % len(key) if key else 'EMPTY'}")
            return key
        print(f"[key] vault responded {r.status_code}: {r.text[:120]!r}")
    except Exception as e:
        print(f"[key] vault fetch failed: {e}")
    return ""


async def main() -> int:
    key = await resolve_key()
    if not key:
        print("\nRESULT: no key — add TOMTOM_API_KEY to the vault/env.")
        return 2
    url = TILE.format(key=key)
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url)
    except Exception as e:
        print(f"\nRESULT: request failed: {e}")
        return 3
    ctype = r.headers.get("content-type", "")
    print(f"[tile] HTTP {r.status_code}  content-type={ctype!r}  bytes={len(r.content)}")
    if r.status_code == 200 and ctype.startswith("image"):
        print("\nRESULT: ✅ key works — traffic tiles will render.")
        return 0
    print(f"[tile] body: {r.text[:200]!r}")
    print("\nRESULT: ❌ key not usable — see the status guide at the top of this file.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
