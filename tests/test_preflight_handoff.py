"""Tier 2's read of the ask must SURVIVE into tier 3.

Live failure 2026-07-31: "145F chicken breast ... how long to get to 165 ... 25
minutes in the oven at 400F" rendered a unit converter. The console proved the
AGENT path ran, which means the tier-2 classifier had already read the ask and
the canvas and its verdict was logged and thrown away — the agent then
re-derived intent from raw text with no hint at all. That is the same
caller-knows-the-answer-and-discards-it seam that made the traffic builder
re-grep for a keyword the router had already consumed.

These pin the handoff: the pre-flight answers reach the prompt, they read as a
PRIOR rather than a mandate (the classifier is exactly what got this ask wrong),
they never name a tool the agent does not have, and — the highest-probability
break in the whole change — the capture variables are bound at function scope so
a removal turn cannot NameError inside the agent proxy.
"""
import ast
import os
import pathlib

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_preflight_handoff.db")

from app import main as m

MAIN_SRC = "\n".join(p.read_text() for p in pathlib.Path(m.__file__).parent.glob("**/*.py"))

SPECS = [{"type": "answer",
          "query": "how long for a 145F chicken breast to reach 165F at 400F",
          "modifiers": {}, "target": None}]
CHECKS = {"is_arithmetic": False, "needs_fresh_data": True, "wants": "answer",
          "subject": "chicken breast internal temperature",
          "unknowns": ["oven recovery time"]}


# ── the block itself ─────────────────────────────────────────────────────────

def test_no_plan_changes_the_prompt_by_zero_bytes():
    """On defer/None (usually the fast-LLM backend being down) the agent must
    behave exactly as it did before this existed — one well-understood mode."""
    assert m._preflight_block([], {}) == ""
    assert m._preflight_block([], CHECKS) == ""


def test_block_names_the_type_the_query_and_the_tool():
    block = m._preflight_block(SPECS, CHECKS)
    assert "answer" in block
    assert "how long for a 145F chicken breast to reach 165F at 400F" in block
    assert "html_notes_web_search" in block
    assert "search_query" in block


def test_block_carries_the_preflight_answers():
    block = m._preflight_block(SPECS, CHECKS)
    assert "Is this ask itself a calculation a converter can finish? NO" in block
    assert "chicken breast internal temperature" in block
    assert "oven recovery time" in block


def test_block_is_a_prior_not_a_mandate():
    """A mandate would turn 'sometimes wrong, agent recovers' into 'sometimes
    wrong, agent locked in' — and a wrong classification is the bug that started
    this. The cleaned query is the part that survives a wrong type, so it gets
    the imperative and the type gets the prior."""
    block = m._preflight_block(SPECS, CHECKS)
    assert "NOT an order" in block
    assert "reuse that query verbatim" in block
    assert "that id wins over anything here" in block


def test_block_never_collides_with_the_followup_directive():
    """test_followup_targeting.py slices the source on these literals; the
    follow-up directive must keep last position and sole ownership of them."""
    block = m._preflight_block(SPECS, CHECKS)
    assert "THIS TURN IS A FOLLOW-UP" not in block
    assert "Update the existing widget #" not in block


def test_block_targets_an_existing_widget_when_the_router_said_so():
    specs = [dict(SPECS[0], target="answer-123")]
    assert "EXISTING widget #answer-123" in m._preflight_block(specs, CHECKS)


def test_recipes_only_name_tools_the_agent_actually_has():
    """A hint naming a phantom tool is worse than no hint — it burns iterations
    failing against something that will always return 'Unknown tool'."""
    start = MAIN_SRC.index("enabled_tools = [")
    enabled = MAIN_SRC[start:MAIN_SRC.index("]", start)]
    for wtype, (tool, _render) in m._PREFLIGHT_RECIPE.items():
        if tool:
            assert tool in enabled, f"{wtype} recipe names a tool the agent lacks: {tool}"


def test_every_research_type_has_a_recipe():
    for wtype in m._AGENT_RESEARCH_TYPES:
        assert wtype in m._PREFLIGHT_RECIPE, f"{wtype} defers to the agent with no recipe"


# ── the checks sanitiser fails open ──────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "nope", 42, [], {"is_arithmetic": "maybe"}])
def test_malformed_checks_degrade_to_empty(raw):
    assert m._clean_preflight_checks(raw) == {}


def test_checks_drop_only_the_bad_fields():
    out = m._clean_preflight_checks(
        {"is_arithmetic": False, "wants": "not-a-real-want",
         "subject": "  chicken  ", "unknowns": "not a list"})
    assert out == {"is_arithmetic": False, "subject": "chicken"}


def test_checks_are_bounded():
    out = m._clean_preflight_checks(
        {"subject": "x" * 500, "unknowns": ["a", "b", "c", "d", "e"]})
    assert len(out["subject"]) <= 160
    assert len(out["unknowns"]) == 3


# ── the capture site ─────────────────────────────────────────────────────────

def _send_message_body():
    tree = ast.parse(MAIN_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_message":
            return node
    raise AssertionError("send_message not found")


def test_router_capture_is_bound_at_function_scope():
    """THE regression risk of this change. The agent proxy closes over these
    names; on a removal turn the classifier never runs, so binding them only
    inside `if not wants_removal:` would NameError at stream time — i.e. on
    every "remove the clock"."""
    fn = _send_message_body()
    # THE router guard specifically — send_message has several `wants_removal`
    # conditions (the crypto intercept among them), so identify it by the
    # route_with_llm call in its body, not by the test expression.
    removal_guard = next(
        node for node in ast.walk(fn)
        if isinstance(node, ast.If)
        and "wants_removal" in ast.dump(node.test)
        and "route_with_llm" in ast.dump(node))
    inside_guard = set(map(id, ast.walk(removal_guard)))

    bound_outside = set()
    for node in ast.walk(fn):
        if id(node) in inside_guard:
            continue
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                bound_outside.add(t.id)

    for name in ("router_plan", "router_specs", "router_checks",
                 "router_status", "router_debug"):
        assert name in bound_outside, (
            f"{name} is bound only inside `if not wants_removal:` — the agent "
            f"proxy closes over it, so a removal turn would NameError")


def test_preflight_block_is_injected_before_the_followup_directive():
    """Context -> verdict -> target. The follow-up directive keeps LAST position:
    it is the more brittle of the two (a weaker, non-last version measurably
    produced prose and zero tool calls), and the two do not compete — this block
    says WHAT to fetch, the directive says WHERE to write it."""
    assert (MAIN_SRC.index("_preflight_block(router_specs, router_checks)")
            < MAIN_SRC.index("THIS TURN IS A FOLLOW-UP"))


def test_deferral_captures_the_plan_instead_of_only_logging_it():
    idx = MAIN_SRC.index("tier3-agent: deferring research")
    window = MAIN_SRC[idx - 900:idx + 300]
    assert "router_specs = widgets" in window, (
        "the whole plan must be carried, not just the research half — a "
        "composite ('weather + answer') defers entirely")


# ── observability ────────────────────────────────────────────────────────────

def test_agent_debug_event_carries_the_classification():
    idx = MAIN_SRC.index('"path": "agent"')
    window = MAIN_SRC[idx:idx + 700]
    assert '"router": router_debug' in window
    assert '"followup_target"' in window


def test_router_debug_event_carries_the_queries():
    """The type alone says 'converter' without saying it was handed a cooking
    question — the query is the field that names a misroute."""
    idx = MAIN_SRC.index('"path": "router"')
    window = MAIN_SRC[idx:idx + 500]
    assert '"queries"' in window


def test_client_renders_a_router_route():
    """Without this branch a tier-2 build printed 'fast-path -> undefined',
    because spawn_router_stream sends `widgets`, not `widget_type`."""
    js = (pathlib.Path(m.__file__).parent / "static" / "index.js").read_text()
    assert "data.path === 'router'" in js
    assert "route: ROUTER" in js
