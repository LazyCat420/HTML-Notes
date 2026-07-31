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

@pytest.mark.parametrize("wtype", ["answer", "image", "wikipedia",
                                   "news", "stock_news"])
def test_research_types_go_to_the_agent(wtype):
    """These need search -> read pages -> synthesis, which is what an agent is for.

    news/stock_news are here deliberately: the value in news is corroborating
    across outlets and naming what they DISAGREE on, which a per-story summariser
    structurally cannot do.

    products is deliberately NOT here (2026-07-21 audit): deferring it left
    render_products unreachable in prism mode — the agent's prompt renders a
    data_card, so the same shopping ask produced a photo grid in lazy-agent
    mode and a text card in prism mode. The router's local products builder
    runs in both modes now."""
    assert wtype in m._AGENT_RESEARCH_TYPES


def test_products_stays_local():
    assert "products" not in m._AGENT_RESEARCH_TYPES
    assert "products" in m.ROUTER_WIDGETS


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


# ── video/live must never be stolen by news research ─────────────────────────

def test_video_override_runs_before_the_classifier():
    """A watch request is deterministic. Moving news to tier 3 let the LLM router
    read 'cnn live news' as news -> research -> a card of links, and did it
    non-deterministically (the same intent behaved differently run to run)."""
    src = MAIN_SRC
    override = src.find("DETERMINISTIC VIDEO/LIVE OVERRIDE")
    classifier = src.find("TIER 2 — the classifier backstop")
    assert override != -1, "the deterministic video override is gone"
    assert classifier != -1, "the tier-2 classifier block is gone"
    assert override < classifier, (
        "the video override must run BEFORE the classifier, or the router can "
        "still classify a watch request as news")


def test_video_is_not_a_research_type():
    """video must stay tier 2 — an agent adds latency and nothing else."""
    assert "video" not in m._AGENT_RESEARCH_TYPES
    assert "video" in m.ROUTER_WIDGETS


@pytest.mark.parametrize("msg", ["cnn live news", "cnn news live video",
                                 "watch a video about bees", "nba highlights"])
def test_watch_asks_are_detected(msg):
    assert (m.VIDEO_ASK_RE.search(msg) or m.LIVE_ASK_RE.search(msg)), \
        f"{msg!r} must be recognised as a watch request"


@pytest.mark.parametrize("msg", ["live nba scores", "live traffic on the 405"])
def test_live_sports_and_traffic_keep_their_own_widgets(msg):
    """Sports and traffic own the word 'live' for their own widgets, so the video
    override must not swallow them."""
    assert m.LIVE_ASK_RE.search(msg), "precondition: these do contain 'live'"
    stolen = not (m.resolve_league(msg) or m.TRAFFIC_MAP_RE.search(msg))
    assert not stolen, f"{msg!r} would be stolen by the video override"


# ---------------------------------------------------------------------------
# Prompt/implementation seam guards (2026-07-19 audit wave)
#
# Each of these pins a place where an agent-facing DOCUMENT (the SYSTEM_PROMPT
# or the MCP tool schema) promised something the SERVER did not implement. That
# class of bug is invisible to unit tests of either side alone: both halves work,
# they just disagree, and the user gets a quietly degraded widget.
# ---------------------------------------------------------------------------

def _main_src() -> str:
    return pathlib.Path(m.__file__).read_text()


def test_canvas_modify_dom_implements_every_action_it_advertises():
    """The committing path handled append/replace/remove while the tool schema
    advertised six actions. prepend/insert_before/insert_after fell off the end
    of the if-chain and returned None, and commit_canvas only aborts on an
    explicit False — so the server committed an UNCHANGED canvas and told the
    model {"success": true}. "Put a header above the chart" did nothing, forever,
    with no error anywhere."""
    src = _main_src()
    start = src.index("def _modify(soup):")
    # Slice to the end of the nested function rather than a byte count, so the
    # guard cannot be defeated by the comment block growing.
    body = src[start:src.index("event = await emit(_modify)", start)]
    for action in ("append", "prepend", "insert_before", "insert_after",
                   "replace", "remove"):
        assert f'"{action}"' in body, f"canvas_modify_dom does not implement {action!r}"
    # An unrecognised action must abort rather than report a phantom success.
    assert "return False" in body.split("else:")[-1], (
        "unknown modify actions must return False, not silently commit a no-op")


def test_data_card_treats_content_as_an_alias_for_answer():
    """The SYSTEM_PROMPT says to pass 'answer'; the MCP tool schema documented
    'content'. The renderer's chain is `if answer / elif items / elif content`,
    so a model that followed the schema AND supplied sources had its prose
    dropped on the floor — research done, brief written, card showed headlines
    only."""
    from app.widgets.factory import render_data_card
    html = render_data_card("w1", {
        "title": "T",
        "content": "THE_BRIEF_TEXT",
        "items": [{"title": "S", "description": "d", "url": "https://e.com"}],
    })
    assert "THE_BRIEF_TEXT" in html, "content was dropped when items were present"
    assert "Sources" in html, "content should render in the answer+sources layout"


def test_quality_floor_sees_content_as_prose():
    """_data_card_quality_gap only read `answer`, so a content-bearing card
    looked prose-less and the floor tried to 'repair' a card that was fine."""
    gap = m._data_card_quality_gap({
        "content": "a real brief",
        "items": [{"title": "S", "url": "https://e.com", "description": "d"}],
    })
    assert gap == "", f"content-bearing card should not be flagged, got {gap!r}"


def test_image_widget_has_an_agent_reachable_build_path():
    """'image' is in _AGENT_RESEARCH_TYPES so every picture ask is DEFERRED to
    the agent — but the agent's 21-tool scope has no image-search tool and the
    injector had no branch for it, so the model could only invent a URL. The
    injector must build the widget server-side via build_image_config (which
    carries the og:image extraction and the vision relevance gate)."""
    assert "image" in m._AGENT_RESEARCH_TYPES
    src = _main_src()
    assert 'widget_type == "image"' in src, "no injector branch for the image widget"
    # Slice to the end of the branch, not a byte count — a growing comment
    # block must not be able to silently shrink what this guard inspects.
    _img_start = src.index('elif widget_type == "image"')
    injector = src[_img_start:src.index('elif (widget_type == "map"', _img_start)]
    assert "build_image_config" in injector, (
        "image branch must call build_image_config, not trust a model-supplied URL")
    assert "image_query" in injector
    # The branch must NOT stand down when the model supplied a url — that
    # inverted the guard, skipping the builder in exactly the case it exists
    # for. Live: "a picture of a red panda" produced a plausible Wikimedia
    # thumb path that 400s, and the user got a broken-image frame.
    condition = src[src.index('elif widget_type == "image"'):][:120]
    assert 'config.get("url")' not in condition, (
        "image branch must run even when the model supplied a url — it is invented")
    assert "_image_url_loads" in injector, (
        "a model-supplied image URL must be verified to load before being kept")


def test_news_topic_falls_back_to_a_builder_like_its_siblings():
    """news_topic was the only injector key with no builder fallback: on a cache
    miss it returned the config untouched, while stock_news_query and
    search_query call their builders unconditionally. A one-character drift
    between the topic passed to html_notes_news and the topic passed to
    canvas_add_widget cost the user the photo stories entirely."""
    src = _main_src()
    start = src.index("async def _resolve_news_topic_config")
    branch = src[start:src.index("\nasync def ", start + 10)]
    assert "build_news_config" in branch, (
        "news_topic cache miss must rebuild via build_news_config")
    assert "build_answer_config" in branch, (
        "a news_topic card that only web-searched must degrade to research")


def test_generic_google_news_thumb_is_not_treated_as_a_photo():
    """Google News redirect pages serve a CONSTANT lh3.googleusercontent.com
    og:image for every story, so a six-story card rendered six identical 300x300
    tiles and called them article photos. Caught in a real browser — the DOM
    check passed (six <img>, all loading) because they were all the same valid
    decorative image."""
    assert m._is_generic_news_thumb(
        "https://lh3.googleusercontent.com/J6_coFbogxhRI9iM864NL_liGXvsQp2Aups=")
    # A real publisher photo must survive.
    assert not m._is_generic_news_thumb("https://www.reuters.com/img/story.jpg")
    assert not m._is_generic_news_thumb("")


def test_news_prefers_the_shared_provider_tool():
    """News must try lazy-tool-service's shared news_search BEFORE Google News
    RSS. Google News links are redirect stubs: they don't resolve to the
    publisher and every one serves Google's own logo as og:image, so a card
    built from them cites news.google.com and shows N identical pictures. The
    shared tool returns real publisher URLs and real per-story photos."""
    src = _main_src()
    body = src[src.index("async def news_search("):]
    body = body[:body.index("\nasync def ", 10)]
    shared = body.index("_shared_news_search")
    google = body.index("_google_news_rss")
    gdelt = body.index("_gdelt_news")
    assert shared < google < gdelt, (
        "source order must be shared -> google-news -> gdelt; "
        f"got shared@{shared} google@{google} gdelt@{gdelt}")


def test_shared_news_search_degrades_instead_of_raising():
    """lazy-tool-service being down must not take news down with it — the
    remaining chain still works, so this returns [] rather than propagating."""
    import asyncio
    assert asyncio.run(m._shared_news_search("", 6)) == []


# ── a numeric QUESTION must not classify as a conversion (2026-07-31) ────────

CHICKEN = ("145F chicken breast with the carcass how long to get to 165 its been "
           "cooking for about 25 minutes in the oven at 400F. I think 10 minutes "
           "should work?")


def test_router_converter_spec_excludes_numeric_questions():
    spec = m.ROUTER_WIDGETS["converter"][1]
    assert "the ask IS the arithmetic" in spec
    assert "NEVER a question that merely contains numbers" in spec


def test_router_answer_spec_claims_numeric_judgement_questions():
    assert "wants a JUDGEMENT" in m.ROUTER_WIDGETS["answer"][1]


def test_router_prompt_asks_the_preflight_questions():
    """The classifier already has the message and the canvas; asking it the
    basic self-checks costs no extra call and no extra latency, and its output
    on the deferral path was otherwise thrown away entirely."""
    assert '"checks"' in MAIN_SRC
    assert '"is_arithmetic"' in MAIN_SRC
    assert '"needs_fresh_data"' in MAIN_SRC


@pytest.mark.asyncio
async def test_converter_pick_is_demoted_when_checks_say_not_arithmetic(monkeypatch):
    async def fake(_instruction, max_tokens=400):
        return {"widgets": [{"type": "converter", "query": CHICKEN}],
                "reason": "unit conversion",
                "checks": {"is_arithmetic": False, "needs_fresh_data": True,
                           "wants": "answer", "subject": "chicken doneness"}}
    monkeypatch.setattr(m, "fast_llm_json", fake)
    plan = await m.route_with_llm(CHICKEN, "")
    assert [w["type"] for w in plan["widgets"]] == ["answer"]
    assert plan["widgets"][0]["query"]


@pytest.mark.asyncio
async def test_a_real_conversion_survives_the_demotion(monkeypatch):
    async def fake(_instruction, max_tokens=400):
        return {"widgets": [{"type": "converter", "query": "20 usd to eur"}],
                "reason": "currency", "checks": {"is_arithmetic": True}}
    monkeypatch.setattr(m, "fast_llm_json", fake)
    plan = await m.route_with_llm("20 usd to eur", "")
    assert [w["type"] for w in plan["widgets"]] == ["converter"]


@pytest.mark.asyncio
async def test_missing_checks_leave_the_plan_alone(monkeypatch):
    """Fail open: a model that omits `checks` must not change routing."""
    async def fake(_instruction, max_tokens=400):
        return {"widgets": [{"type": "converter", "query": "20 usd to eur"}],
                "reason": "currency"}
    monkeypatch.setattr(m, "fast_llm_json", fake)
    plan = await m.route_with_llm("20 usd to eur", "")
    assert [w["type"] for w in plan["widgets"]] == ["converter"]
    assert plan["checks"] == {}
