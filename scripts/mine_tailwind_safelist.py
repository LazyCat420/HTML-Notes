#!/usr/bin/env python3
"""Mine the Tailwind classes that SAVED canvases still use.

Tailwind used to run in the browser (cdn.tailwindcss.com), so any class in any
canvas the server ever stored got styled on the fly. The compiled stylesheet
(app/static/tailwind.css, built from tailwind.config.js) only contains classes
found in the source tree, and saved canvases carry ~300 classes the current
templates no longer emit (older widget templates, agent-written cards). This
script reads a notes.db, collects every class token from stored canvas HTML,
and writes tailwind.safelist.json, which tailwind.config.js feeds to the build
so old canvases keep their look.

    .venv/bin/python scripts/mine_tailwind_safelist.py path/to/notes.db

Read-only on the DB (opened with mode=ro). Re-run against a fresh copy of the
live DB when you want to pick up newer saved canvases.
"""
import json
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tailwind.safelist.json"
# A class token: utilities with variants/arbitrary values, e.g.
# "group-hover/row:opacity-100", "h-[456px]", "shadow-[0_0_10px_rgba(1,2,3,0.5)]".
TOKEN = re.compile(r"^!?-?[a-z0-9@][a-z0-9:/\[\]().,_%#\-]*$")


def mine(db_path: str) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    found = set()
    for (content,) in conn.execute(
            "SELECT content FROM chat_messages WHERE content LIKE '%class=%'"):
        for m in re.finditer(r'class="([^"]*)"', content or ""):
            for tok in m.group(1).split():
                if TOKEN.match(tok) and not tok.endswith(":"):
                    found.add(tok)
    conn.close()
    return sorted(found)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    classes = mine(sys.argv[1])
    OUT.write_text(json.dumps(classes, indent=0) + "\n")
    print(f"wrote {len(classes)} classes to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
