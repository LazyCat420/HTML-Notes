"""Google News RSS links resolve to the publisher before anything reads them.

Live 2026-10-11 with nemotron35: "news about ai regulation" showed 5 stories,
3 with no summary. Their links were news.google.com/rss/articles/CBMi...,
which never redirect server-side, so the scraper spent ~24s failing on each
and the shared 12s batch call timed out for every story.
"""
import asyncio
import json

from app import main  # noqa: F401  (load main first: the service modules copy its namespace)
from app.services import search as S
from app import config_builders as cb

GOOGLE = "https://news.google.com/rss/articles/CBMiAbc123?oc=5"
REAL = "https://www.reuters.com/technology/ai-rules-2026-10-11/"


class _Resp:
    def __init__(self, text, status_code=200):
        self.text, self.status_code = text, status_code


def _google_client(fail=False):
    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            assert url == "https://news.google.com/rss/articles/CBMiAbc123"
            return _Resp('<c-wiz data-n-a-sg="SIG" data-n-a-ts="1791700000"></c-wiz>')
        async def post(self, url, data=None, headers=None):
            req = json.loads(json.loads(data["f.req"])[0][0][1])
            assert req[2:] == ["CBMiAbc123", 1791700000, "SIG"]
            if fail:
                return _Resp(")]}'\n\n[]")
            inner = json.dumps(["garturlres", REAL, 1])
            return _Resp(")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner]]))
    return C


def test_a_google_link_becomes_the_publisher_link(monkeypatch):
    monkeypatch.setattr(S.httpx, "AsyncClient", _google_client())
    items = [{"url": GOOGLE}, {"url": "https://ap.org/x"}]
    asyncio.run(S._resolve_google_news_links(items))
    assert [it["url"] for it in items] == [REAL, "https://ap.org/x"]


def test_an_undecodable_link_keeps_the_google_url(monkeypatch):
    monkeypatch.setattr(S.httpx, "AsyncClient", _google_client(fail=True))
    items = [{"url": GOOGLE}]
    asyncio.run(S._resolve_google_news_links(items))
    assert items[0]["url"] == GOOGLE


def test_one_slow_article_does_not_drop_the_other_bodies(monkeypatch):
    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            job = json["jobs"][0]["url"]
            if "slow" in job:
                raise cb.httpx.ReadTimeout("12s")
            class R:
                status_code = 200
                def json(self):
                    return {"results": [{"url": job, "success": True, "content": "Body " + job}]}
            return R()

    monkeypatch.setattr(cb.httpx, "AsyncClient", C)
    items = [{"url": "https://a.com/1"}, {"url": "https://slow.com/2"}, {"url": "https://b.com/3"}]
    asyncio.run(cb._attach_article_bodies(items))
    assert [it.get("body", "") for it in items] == ["Body https://a.com/1", "", "Body https://b.com/3"]
