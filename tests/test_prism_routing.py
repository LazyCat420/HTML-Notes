"""Regression guards for the prism-mode routing wave (2026-07-19).

Every bug locked down here shipped silently and was only caught by driving the
real system, so each test pins the *contract* rather than an implementation
detail:

  - canvas-control ("close everything") must never be gated behind the
    prism-mode guard — the agent removes one widget per iteration and cannot
    clear a canvas, so gating it made the ask fail with no error.
  - assistant history must never contain an imitable prose placeholder — the
    model copied "[Visual Component Rendered]" verbatim and called zero tools.
  - the tier split decides what pays for an agent turn; drift here silently
    doubles latency (tier 2 leaking to tier 3) or guts quality (the reverse).
  - terse refinements must be recognised as follow-ups, or they get answered in
    prose and the canvas never changes.
"""
import ast
import os
import pathlib

import pytest

os.environ.setdefault("DATABASE_URL", "data/test_prism_routing.db")

from app import main as m

MAIN_SRC = pathlib.Path(m.__file__).read_text()


# ── canvas control must bypass the prism-mode guard ──────────────────────────

def _guarded_by_use_lazy_agent(tree):
    """Every node living inside an `if req.use_lazy_agent:` body."""
    guarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.dump(node.test)
        # the guard is `req.use_lazy_agent` (possibly ANDed with other terms)
        if "use_lazy_agent" not in test_src:
            continue
        # `not req.use_lazy_agent` / `req.use_lazy_agent or ...` are not the guard
        if isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
            continue
        for child in node.body:
            guarded.extend(ast.walk(child))
    return guarded


def _calls_named(nodes, name):
    return [n for n in nodes
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name]


def test_clear_canvas_is_not_gated_behind_prism_mode():
    """'close everything' regressed to a no-op when the whole fast-path cascade
    was wrapped in the guard. The agent CANNOT clear a full canvas."""
    tree = ast.parse(MAIN_SRC)
    guarded = _guarded_by_use_lazy_agent(tree)
    assert not _calls_named(guarded, "_stream_clear_canvas"), (
        "_stream_clear_canvas() is inside an `if req.use_lazy_agent:` block — "
        "in prism mode 'close everything' would silently do nothing.")


def test_clear_canvas_still_exists_and_is_reachable():
    tree = ast.parse(MAIN_SRC)
    all_calls = _calls_named(list(ast.walk(tree)), "_stream_clear_canvas")
    assert all_calls, "the CLEAR ALL intercept disappeared entirely"


# ── history must not teach the model to fake a render ────────────────────────

def _live_string_literals(tree):
    """String constants that are actually USED as values — docstrings excluded.

    Checked via the AST rather than a substring scan of the file so that the
    comments and docstrings documenting this very bug don't trip the guard.
    Comments never reach the AST at all."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_history_never_uses_an_imitable_prose_placeholder():
    """The literal '[Visual Component Rendered]' read as a valid assistant reply,
    so the model emitted it as text and called no tools."""
    lits = _live_string_literals(ast.parse(MAIN_SRC))
    offenders = [s for s in lits if "Visual Component Rendered" in s]
    assert not offenders, (
        f"the imitable placeholder is back as a live string: {offenders!r}")


def test_canvas_history_summary_names_widget_ids():
    html = ('<!--CANVAS_HTML_START-->'
            '<div id="dashboard-grid">'
            '<div class="widget-container" id="best-sandals">'
            '<h2>Best Sandals of 2026</h2></div>'
            '</div>'
            '<!--CANVAS_HTML_END-->')
    out = m._summarize_canvas_for_history(html)
    assert "best-sandals" in out, "summary must name the id so a follow-up can reuse it"
    assert "tool call" in out.lower(), "summary must read as a tool result, not prose"


def test_canvas_history_summary_degrades_safely():
    for junk in ("", "<div>no widgets here</div>", None):
        out = m._summarize_canvas_for_history(junk)
        assert isinstance(out, str) and out, "must always return a non-empty marker"


# ── the tier split ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("wtype", ["products", "answer", "image", "wikipedia",
                                   "news", "stock_news"])
def test_research_types_go_to_the_agent(wtype):
    """These need search -> read pages -> synthesis, which is what an agent is for.

    news/stock_news are here deliberately: the value in news is corroborating
    across outlets and naming what they DISAGREE on, which a per-story summariser
    structurally cannot do."""
    assert wtype in m._AGENT_RESEARCH_TYPES


@pytest.mark.parametrize("wtype", ["weather", "stock", "sports", "clock",
                                   "music", "map", "traffic"])
def test_deterministic_types_stay_local(wtype):
    """One right answer from one API — an agent adds latency and a hallucination
    surface and buys nothing."""
    assert wtype not in m._AGENT_RESEARCH_TYPES
    assert wtype in m.ROUTER_WIDGETS, "tier-2 type must still be router-buildable"


# ── follow-up detection ──────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "what about cheaper ones",
    "only show waterproof ones",
    "just the waterproof ones",
    "tell me more",
    "and the away team?",
    "without the expensive ones",
])
def test_refinements_are_detected(msg):
    assert m._is_refining_followup(msg), f"{msg!r} must be treated as a follow-up"


@pytest.mark.parametrize("msg", [
    "best sandals",
    "weather in tokyo",
    "show me a video of cats",
    "set a 5 minute timer",
])
def test_new_subjects_are_not_followups(msg):
    """Over-matching would hijack a genuinely new ask into rewriting the open
    widget."""
    assert not m._is_refining_followup(msg)


# ── prism payload contract ───────────────────────────────────────────────────

def test_prism_gets_a_persona_id():
    """Without a persona the run is unscoped: prism hands the model ~79 tools
    (execute_python, ...) and it wanders off without mutating the canvas."""
    assert m.PRISM_AGENT_ID, "prism persona id must be set"
    assert m.FORK_AGENT_ID, "fork persona id must be set"


def test_agent_attribution_is_configured():
    """prism attributes by x-project/x-username HEADERS; unset means the turn
    lands in the unattributable 'default' project."""
    assert m.AGENT_PROJECT
    assert m.AGENT_USERNAME
