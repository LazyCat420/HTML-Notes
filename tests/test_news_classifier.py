"""classify_news_ask — the deterministic pre-router for every news-shaped ask.

One decision replaces five cascade regexes. Each row below is a message the
user actually typed, or a near-miss chosen to prove the classifier does NOT
over-claim. The pure classifier is asserted exactly; it never calls a model.
"""
import ast

import pytest

from app import main as m

# (message, expected)  expected = None | dict(finance, general, depth, kind, id_prefix)
ROWS = [
    # ── the user's asks ─────────────────────────────────────────────────────
    ("hello", None),
    ("thanks", None),
    ("stock market news",
     dict(finance=True, general=True, depth="card", kind="news", id_prefix="stock-news")),
    ("market news",
     dict(finance=True, general=True, depth="card", kind="news", id_prefix="stock-news")),
    ("stock market for the day please",
     dict(finance=True, general=True, depth="card", kind="news", id_prefix="stock-news")),
    ("stock market news for the day please.",
     dict(finance=True, general=True, depth="card", kind="news", id_prefix="stock-news")),
    ("whats going on in the news",
     dict(finance=False, general=True, depth="card", kind="news", id_prefix="news")),
    ("news about nvidia earnings",
     dict(finance=True, general=False, depth="card", kind="news", id_prefix="stock-news")),
    ("latest news on the israel hamas ceasefire",
     dict(finance=False, general=False, depth="card", kind="news", id_prefix="news")),
    # ── other words own these ───────────────────────────────────────────────
    ("bloomberg live news", None),          # LIVE_ASK_RE
    ("cnn live news", None),
    ("5 minute timer", None),
    ("weather news", None),                 # WEATHER_ASK_RE
    ("wikipedia news", None),               # WIKI_ASK_RE
    ("whats going on with my timer", None), # residual "timer" -> not general
    ("top gainers today", None),            # TRENDING_STOCK_RE -> router's stock_trending
    ("tesla stock price", None),            # price shape, no news word -> stock card path
    # ── depth and kind ──────────────────────────────────────────────────────
    ("nvda deep dive",
     dict(finance=True, general=False, depth="brief", kind="stock_report", id_prefix="stock-report")),
    ("deep dive on the stock market",
     dict(finance=True, general=True, depth="brief", kind="news", id_prefix="stock-news")),
    ("summarize todays news",
     dict(finance=False, general=True, depth="brief", kind="news", id_prefix="news")),
    ("tell me about the stock market news",
     dict(finance=True, general=True, depth="card", kind="news", id_prefix="stock-news")),
    # ── capitalisation must not change the route ────────────────────────────
    ("STOCK MARKET NEWS",
     dict(finance=True, general=True, depth="card", kind="news", id_prefix="stock-news")),
    ("Deep dive on the US market",
     dict(finance=True, general=True, depth="brief", kind="news", id_prefix="stock-news")),
    # ── subjects of two letters are subjects ────────────────────────────────
    ("news about AI",
     dict(finance=False, general=False, depth="card", kind="news", id_prefix="news")),
    ("bitcoin news",
     dict(finance=True, general=False, depth="card", kind="news", id_prefix="stock-news")),
]


@pytest.mark.parametrize("message,expected", ROWS, ids=[r[0] for r in ROWS])
def test_classify_news_ask(message, expected):
    got = m.classify_news_ask(message)
    if expected is None:
        assert got is None, f"{message!r} was claimed as news: {got}"
        return
    assert got is not None, f"{message!r} was NOT claimed as news"
    actual = dict(finance=got.finance, general=got.general, depth=got.depth,
                  kind=got.kind, id_prefix=got.id_prefix)
    assert actual == expected


def test_exclusion_flags_win():
    """The caller's already-computed flags must veto, whatever the words say."""
    assert m.classify_news_ask("close the news", wants_removal=True) is None
    assert m.classify_news_ask("news video", is_video_ask=True) is None
    assert m.classify_news_ask("news music", wants_music=True) is None
    assert m.classify_news_ask("nba news scores", league="nba") is None
    # ...but a league without a score word is a news ask about that league
    assert m.classify_news_ask("nba news", league="nba") is not None


def test_the_five_regex_branches_are_gone():
    """No module BINDS the retired regexes. Checked on the AST: the comment in
    app/main.py explaining why they were retired names them, and a substring
    scan would fail on the very prose that records the fix."""
    from tests._sources import trees, MESSAGE_SRC
    bound = set()
    for _name, tree in trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
    assert "MARKET_RESEARCH_RE" not in bound
    assert "_NEWS_SYNTH_RE" not in bound
    assert "classify_news_ask(" in MESSAGE_SRC


def test_news_dispatch_precedes_the_llm_router_and_the_cascade():
    """Structural: the pre-router runs before route_with_llm AND before the
    mode-gated cascade, so no news ask can reach either."""
    from tests._sources import MESSAGE_SRC
    tree = ast.parse(MESSAGE_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "send_message")
    order = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in ("classify_news_ask", "route_with_llm"):
                order.append((node.lineno, name))
    order.sort()
    names = [n for _, n in order]
    assert "classify_news_ask" in names and "route_with_llm" in names
    assert names.index("classify_news_ask") < names.index("route_with_llm")
    cascade_line = next(n.lineno for n in ast.walk(fn)
                        if isinstance(n, ast.If) and "use_lazy_agent" in ast.dump(n.test))
    assert order[names.index("classify_news_ask")][0] < cascade_line
