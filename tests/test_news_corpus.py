"""Corpus-first news: html-notes reads trading-service's pre-scraped store
before fanning out to external APIs."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.services import news_corpus


class FakeCol:
    def __init__(self, docs):
        self._docs = docs

    def find(self, q, proj=None):
        self.last_query = q
        # Apply the predicates the real collection would — quality gate,
        # freshness window, and the $or (ticker / title-regex) clauses.
        def _ok(d):
            if d.get("quality_status") != "ok":
                return False
            gte = (q.get("published_at") or {}).get("$gte")
            if gte and d.get("published_at") and d["published_at"] < gte:
                return False
            for clause in q.get("$or", []):
                if "ticker" in clause:
                    if d.get("ticker") not in clause["ticker"]["$in"]:
                        return False
                if "title" in clause:
                    import re as _re
                    if not _re.search(clause["title"]["$regex"], d.get("title") or "", _re.I):
                        return False
            return True
        self._docs_iter = [d for d in self._docs if _ok(d)]
        return self

    def sort(self, *a):
        return self

    def limit(self, n):
        return iter(self._docs_iter)


def _doc(i, ticker="AAPL", hours_old=2, status="ok"):
    return {
        "title": f"Story {i} about Apple suppliers",
        "publisher": "Reuters",
        "source": "rss",
        "published_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_old),
        "url": f"https://example.com/{i}",
        "summary": f"<p>Full body {i}</p> " * 20,
        "ticker": ticker,
        "quality_status": status,
    }


def test_corpus_news_filters_and_normalises():
    docs = [_doc(1), _doc(2, status="discarded"), _doc(3, hours_old=72), _doc(1)]  # last = dupe url
    with patch.object(news_corpus, "_collection", return_value=FakeCol(docs)):
        out = news_corpus.corpus_news(tickers=["AAPL"], hours=48, limit=10)
    assert [o["title"] for o in out] == ["Story 1 about Apple suppliers"]
    assert out[0]["summary"].startswith("Full body 1")  # tags stripped
    assert out[0]["related_tickers"] == ["AAPL"]
    assert out[0]["published"].endswith("UTC")


def test_corpus_news_fails_open():
    with patch.object(news_corpus, "_collection", side_effect=RuntimeError("down")):
        assert news_corpus.corpus_news(query="stock market") == []


def test_corpus_news_requires_a_selector():
    with patch.object(news_corpus, "_collection") as c:
        assert news_corpus.corpus_news() == []
        c.assert_not_called()


import time  # noqa: E402
from datetime import timezone  # noqa: E402
