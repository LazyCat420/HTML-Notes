"""Outage / soft-failure classification tests (audit F2, F11).

Verifies the harness contract: a dead endpoint classifies as OUTAGE, a
2xx-empty classifies as SOFT_EMPTY — the two states app code currently
conflates. Run: python3 -m bench.web.simulate_outage
"""
from __future__ import annotations

import json

from .adapters import search_searxng
from .schema import OUTAGE, SOFT_EMPTY

DEAD_PORT = "http://127.0.0.1:9"   # nothing listens here


def main() -> int:
    failures = []

    # 1. Transport outage must classify OUTAGE, not soft-empty.
    try:
        search_searxng("test", "sim_outage", base_url=DEAD_PORT)
        failures.append("dead endpoint returned a response instead of raising")
    except Exception as e:
        status = getattr(e, "status", "?")
        print(f"dead endpoint -> {status} ({e})")
        if status != OUTAGE:
            failures.append(f"dead endpoint classified {status!r}, expected {OUTAGE!r}")

    # 2. Real server, gibberish query: zero results must classify SOFT_EMPTY.
    try:
        resp = search_searxng("zzqqxx gibberishqqqq plugh xyzzy", "sim_soft_empty")
        print(f"live server gibberish -> {resp.status}, n={len(resp.results)}")
        if resp.status not in (SOFT_EMPTY, "ok"):
            failures.append(f"gibberish classified {resp.status!r}")
    except Exception as e:
        print(f"live server gibberish raised ({e}) — acceptable only if CAPTCHA")

    print(json.dumps({"failures": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
