"""Every widget type the agent prompt advertises must be in the live
canvas_add_widget enum, or the tool call is rejected by the gateway and the
model apologises instead of rendering. quality_profile and settings were
advertised (message.py ROUTING) but absent from the enum at HEAD cf1a937.

The flat tool_schemas.json is what the gateway loads at boot. It lives in the
sibling lazy-agent-service checkout; set LAZY_AGENT_SERVICE_DIR to point at a
worktree. Skipped (not passed) when no checkout is found.
"""
import json
import os
import pathlib
import re

import pytest

from tests._sources import MESSAGE_SRC


def _flat_schema_path():
    env = os.environ.get("LAZY_AGENT_SERVICE_DIR")
    if env:
        p = pathlib.Path(env) / "tool_schemas.json"
        if p.exists():
            return p
    # Packaged in-repo versioned schema artifact (independent of sibling worktrees)
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    packaged = repo_root / "app" / "schemas" / "tool-contract-v1.json"
    if packaged.exists():
        return packaged
    return None


def _live_enum():
    path = _flat_schema_path()
    if path is None:
        pytest.fail("Tool contract schema not found; app/schemas/tool-contract-v1.json is missing")
    tools = json.loads(path.read_text())
    tool = next(t for t in tools if t["name"] == "canvas_add_widget")
    return set(tool["parameters"]["properties"]["widget_type"]["enum"]), path


def test_every_prompt_advertised_widget_type_is_in_the_live_enum():
    advertised = set(re.findall(r"widget_type='([a-z_]+)'", MESSAGE_SRC))
    assert len(advertised) >= 20, "the prompt scan found too few types — wrong source?"
    enum, path = _live_enum()
    missing = sorted(advertised - enum)
    assert not missing, f"advertised in the prompt but not in {path}: {missing}"
