"""News relevance: the query that leaves the process, and the articles that come back.

Two independent defects produced "the articles are random / not about what I
asked / basically ads":

1. The news topic was built by deleting stopwords from the user's message, using
   a list assembled for the MUSIC widget (`TOPIC_STOPWORDS = MUSIC_FILLER_WORDS |
   {...}`). Measured against the real code on 2026-09-05:

       news about track and field world championships -> "field world championships"
       latest news on the player transfer window      -> "transfer window"
       latest news on us china trade talks            -> "china trade talks"
       show me news about the search for the missing submarine -> "missing submarine"
       what is the latest news on the israel hamas ceasefire
                                              -> "what is israel hamas ceasefire"

   "track", "player", "us", "search" are all deleted; "what is" survives and gets
   keyword-matched. Live, that last one returned ONE article for one of the
   largest running news stories.

2. Nothing downstream checked whether a returned article was about the question.
   news_search takes the first non-empty provider tier of five, the shared
   provider takes the first non-empty of five APIs, and neither dedups, scores
   relevance, or filters ads. The LLM pass was told "write one entry per distinct
   story", so it had no authority to drop anything — it just wrote a confident
   summary for whatever arrived, which is exactly why the junk read as
   intentional.
"""
import pytest

from app import main as m
from app import config_builders as cb


NEWS_ASKS = [
    ("news about track and field world championships", "track"),
    ("latest news on the player transfer window", "player"),
    ("latest news on us china trade talks", "us"),
    ("show me news about the search for the missing submarine", "search"),
]


@pytest.mark.parametrize("ask,must_keep", NEWS_ASKS)
@pytest.mark.asyncio
async def test_news_query_keeps_the_words_that_carry_the_subject(ask, must_keep, patch_server):
    """A word is not filler because the music widget said so."""
    async def fake_ground(message):
        return {"subject": message, "intent": "informational",
                "retrieval_query": message, "hyde": "", "negatives": [],
                "freshness": "", "ambiguous": False, "clarify": ""}

    seen = {}

    async def fake_news(topic, limit=6):
        seen["topic"] = topic
        return []

    patch_server("ground_query", fake_ground)
    patch_server("news_search", fake_news)
    await cb.build_news_config(ask)
    assert must_keep in (seen.get("topic") or "").lower(), (
        f"{ask!r} searched {seen.get('topic')!r} — the subject word "
        f"{must_keep!r} was deleted before the query left the process")


@pytest.mark.asyncio
async def test_news_query_drops_the_question_scaffolding(patch_server):
    """The mirror image: 'what is' must NOT survive into a keyword query."""
    async def fake_ground(message):
        return {"subject": "Israel-Hamas ceasefire", "intent": "informational",
                "retrieval_query": "Israel Hamas ceasefire", "hyde": "",
                "negatives": [], "freshness": "", "ambiguous": False, "clarify": ""}

    seen = {}

    async def fake_news(topic, limit=6):
        seen["topic"] = topic
        return []

    patch_server("ground_query", fake_ground)
    patch_server("news_search", fake_news)
    await cb.build_news_config("what is the latest news on the israel hamas ceasefire")
    topic = (seen.get("topic") or "").lower()
    assert "what is" not in topic, f"question scaffolding leaked into the query: {topic!r}"
    assert "ceasefire" in topic


@pytest.mark.asyncio
async def test_a_general_ask_still_means_top_stories(patch_server):
    """'what's going on in the news' has no subject — that is not a bug, and the
    grounding rewrite must not invent one."""
    async def fake_ground(message):
        # No invented keys: ground_query never returns an "is_general_news"
        # flag, and a fixture that supplies one only proves the test's own
        # premise. The general verdict has to come from the MESSAGE.
        return {"subject": "current events", "intent": "informational",
                "retrieval_query": "today's top news stories", "hyde": "",
                "negatives": [], "freshness": "today", "ambiguous": False,
                "clarify": ""}

    seen = {}

    async def fake_news(topic, limit=6):
        seen["topic"] = topic
        return []

    patch_server("ground_query", fake_ground)
    patch_server("news_search", fake_news)
    await cb.build_news_config("whats going on in the news today")
    assert (seen.get("topic") or "").strip() == "", (
        f"a general ask must fetch TOP stories, not search {seen.get('topic')!r}")


# ── the drop-gate ────────────────────────────────────────────────────────────

ITEMS = [
    {"title": "Ceasefire holds for a third day", "url": "https://reuters.com/a", "snippet": "..."},
    {"title": "5 Stocks To Buy Now, According To Analysts", "url": "https://x.com/b", "snippet": "..."},
    {"title": "Aid convoys enter through Rafah", "url": "https://apnews.com/c", "snippet": "..."},
]


@pytest.mark.asyncio
async def test_relevance_gate_drops_the_off_subject_item(patch_server):
    async def fake_llm(instruction, max_tokens=400):
        return {"keep": [0, 2]}
    patch_server("fast_llm_json", fake_llm)
    kept = await m.filter_items_by_relevance("Israel-Hamas ceasefire", [], list(ITEMS))
    assert [i["title"] for i in kept] == [ITEMS[0]["title"], ITEMS[2]["title"]]


@pytest.mark.asyncio
async def test_relevance_gate_fails_open_on_model_failure(patch_server):
    """A grading outage must never empty a card — the same contract the vision
    gate already keeps."""
    async def dead(instruction, max_tokens=400):
        return None
    patch_server("fast_llm_json", dead)
    kept = await m.filter_items_by_relevance("anything", [], list(ITEMS))
    assert kept == ITEMS


@pytest.mark.asyncio
async def test_relevance_gate_min_keep_zero_returns_empty_on_all_rejected(patch_server):
    """min_keep=0 hands the all-rejected verdict BACK to the caller as [].

    This is the contract build_news_config's escalation depends on, and it was
    silently broken: the gate read `max(min_keep, 1)`, clamping 0 to 1, so an
    all-rejected set was always reinstated and the escalation branch never ran.
    The sibling test below uses min_keep=1 and therefore could not see it."""
    async def reject_all(instruction, max_tokens=400):
        return {"keep": []}
    patch_server("fast_llm_json", reject_all)
    kept = await m.filter_items_by_relevance("x", [], list(ITEMS), min_keep=0)
    assert kept == [], f"min_keep=0 must return [] on all-rejected, got {len(kept)} items"


@pytest.mark.asyncio
async def test_relevance_gate_never_empties_the_card(patch_server):
    """If the model rejects everything, show the unfiltered list rather than an
    empty widget — a blank card is a worse answer than a loose one."""
    async def reject_all(instruction, max_tokens=400):
        return {"keep": []}
    patch_server("fast_llm_json", reject_all)
    kept = await m.filter_items_by_relevance("x", [], list(ITEMS), min_keep=1)
    assert kept == ITEMS


@pytest.mark.asyncio
async def test_relevance_gate_is_wired_into_the_news_card(patch_server):
    """The gate existing is not the gate running."""
    called = {}

    async def fake_ground(message):
        return {"subject": "ceasefire", "intent": "informational",
                "retrieval_query": "ceasefire", "hyde": "",
                "negatives": ["stock promotion"], "freshness": "",
                "ambiguous": False, "clarify": ""}

    async def fake_news(topic, limit=6):
        return [dict(i, image="", meta="x", date="") for i in ITEMS]

    async def fake_gate(subject, negatives, items, **kw):
        called["subject"] = subject
        called["negatives"] = negatives
        return [items[0]]

    async def fake_llm(instruction, max_tokens=400):
        return None

    patch_server("ground_query", fake_ground)
    patch_server("news_search", fake_news)
    patch_server("filter_items_by_relevance", fake_gate)
    patch_server("fast_llm_json", fake_llm)
    cfg = await cb.build_news_config("news on the ceasefire")
    assert called.get("subject"), "build_news_config never called the relevance gate"
    assert called.get("negatives") == ["stock promotion"], (
        "ground_query's negatives must reach the gate — they are what let it "
        "reject the OTHER meaning of an ambiguous word")
    assert len(cfg["items"]) == 1


# ── ad / PR filtering ────────────────────────────────────────────────────────

def test_pr_spam_filter_does_not_reinstate_what_it_caught():
    """`filtered or raw` means a list that is ALL ads comes back in full — the
    filter defeats itself in exactly the case it exists for.

    Checked via the AST, not a substring scan: the comment documenting this very
    removal contains the old expression, and prose recording a fix must pass
    while a live `_drop_pr_spam(...) or <anything>` must fail.
    """
    import ast
    from tests._sources import BUILDERS_SRC

    offenders = []
    for node in ast.walk(ast.parse(BUILDERS_SRC)):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        for value in node.values[:-1]:
            # The defect is a CALL to the filter on the left of an `or`. Not the
            # repo's own `getattr(main, "_drop_pr_spam", None) or _drop_pr_spam`
            # lookup idiom, where the name is an ARGUMENT to getattr rather than
            # the function being invoked — matching on the whole node cannot
            # tell those two apart.
            if isinstance(value, ast.Call) and "_drop_pr_spam" in ast.dump(value.func):
                offenders.append(ast.unparse(node)[:90])
    assert not offenders, (
        f"the PR-spam filter falls back to the unfiltered list: {offenders}")


def test_pr_spam_is_applied_to_every_tier_not_just_the_yahoo_fallback():
    from tests._sources import BUILDERS_SRC
    assert "_drop_pr_spam" in BUILDERS_SRC, (
        "PR-spam filtering must be a shared helper applied to whichever tier "
        "served the stories, not inlined in the Yahoo fallback only")


def test_pr_spam_catches_the_known_offenders():
    rows = [{"title": "Ceasefire holds", "meta": "Reuters"},
            {"title": "5 Stocks To Buy Now", "meta": "Motley Fool"},
            {"title": "Acme Corp Announces Q3", "meta": "GlobeNewswire"}]
    kept = m._drop_pr_spam(rows)
    assert [r["title"] for r in kept] == ["Ceasefire holds"]


def test_pr_spam_returns_empty_rather_than_reinstating():
    rows = [{"title": "5 Stocks To Buy Now", "meta": "Motley Fool"}]
    assert m._drop_pr_spam(rows) == []


# ── a general ask is not a search for the words "top stories" ───────────────

@pytest.mark.asyncio
async def test_general_ask_passes_an_empty_topic_through(patch_server):
    """The shared tool treats "" as "use your top-headlines endpoint". Turning it
    into the literal query "top stories" keyword-matches roundup pages that
    contain that phrase — measured live, it returned "...and other top stories
    highlighted by Us for September 4", where the phrase was the match."""
    sent = {}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **kw):
            sent.update(json or {})
            class _R:
                status_code = 200
                @staticmethod
                def json(): return {"items": []}
            return _R()

    import app.services.search as S
    patch_server("httpx", type("_hx", (), {"AsyncClient": _Client})())
    await S._shared_news_search("", 6)
    assert sent.get("topic") == "", (
        f"a general ask must send an empty topic, sent {sent.get('topic')!r}")


def test_news_brief_sources_are_ad_filtered():
    """The synthesized brief reaches the card without going through the news
    builder, so it was the one route where a press release could still be cited
    as a 'Source'."""
    from tests._sources import BUILDERS_SRC
    start = BUILDERS_SRC.index("async def build_news_brief_config")
    body = BUILDERS_SRC[start:BUILDERS_SRC.index("async def build_stock_report_config", start)]
    assert "_drop_pr_spam" in body, "the brief's sources bypass the ad filter"
