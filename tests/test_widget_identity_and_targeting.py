"""Widgets must be identifiable, reusable, and safe to remove.

From a real session: the user asked "traffic to san jose", then "traffic to sf",
then "san ramon to san jose" — and got THREE traffic widgets. Then "close the san
jose to sf map" closed a different widget.

Three independent defects, all of which presented as "the agent is dumb":

1. **Widgets were unidentifiable.** The only attributes on a widget root were an
   opaque id (`traffic-c20bc01b`), classes, and `data-sig`. The title existed only
   as `<h3>` prose. `iframe_app` had no type class at all, so a directions map
   classified as `custom` — the one widget the user called "the map" was the one
   line in the inventory that didn't say map.
2. **`canvas_read_dom` was blind.** It selected `.glass-card`, a convention no
   factory widget uses, so it matched nothing and reported zero components — and
   it never returned widget ids, so the agent couldn't build a `#id` selector even
   when it did match. Its own tool schema advertised "each with its widget_id,
   type, and what it shows".
3. **`canvas_modify_dom` never verified what it destroyed.** `select_one(sel)` +
   `decompose()`, then `{"success": true}` regardless of what died.
"""
import pytest
from bs4 import BeautifulSoup

import app.main as m
from app.main import (_classify_canvas_widget, _iter_canvas_widgets,
                      find_existing_widget, find_existing_widget_by_id_prefix,
                      get_canvas_summary)
from app.widgets.factory import generate_widget_html as G


def _canvas():
    """The canvas as it stood when the wrong widget was closed."""
    return (
        G("map", "traffic-10cd8af3", {
            "title": "Traffic: San Jose", "subtitle": "live flow",
            "center": {"lat": 37.33, "lon": -121.89}, "zoom": 13,
            "markers": [], "traffic": True})
        + G("iframe_app", "traffic-c20bc01b", {
            "title": "San Jose → SF",
            "url": "https://maps.google.com/maps?q=x&output=embed", "icon": "🚗"})
        + G("data_card", "news-top", {"title": "Top stories", "answer": "x"})
    )


class TestWidgetIdentity:
    @pytest.mark.parametrize("wtype,wid,cfg", [
        ("map", "m1", {"title": "Traffic: Oakland", "center": {"lat": 1, "lon": 2},
                       "zoom": 9, "markers": []}),
        ("iframe_app", "i1", {"title": "A → B", "url": "https://x.com"}),
        ("data_card", "d1", {"title": "News", "answer": "x"}),
        ("weather", "w1", {"location": "Tokyo", "title": "Weather"}),
    ])
    def test_every_widget_is_stamped_with_type_and_title(self, wtype, wid, cfg):
        soup = BeautifulSoup(G(wtype, wid, cfg), "html.parser")
        root = soup.find(id=wid)
        assert root is not None
        assert root.get("data-widget-type") == wtype
        assert root.get("data-widget-title") == cfg["title"]

    def test_iframe_app_is_no_longer_classified_custom(self):
        """The specific miss: a directions map had no type class and no x-data, so
        it classified as 'custom' and never read as a map to the agent."""
        soup = BeautifulSoup(G("iframe_app", "i1", {"title": "San Jose → SF",
                                                    "url": "https://x.com"}), "html.parser")
        assert _classify_canvas_widget(soup.find(id="i1")) == "iframe_app"

    def test_inventory_gives_id_type_and_title_for_every_widget(self):
        inv = list(_iter_canvas_widgets(_canvas()))
        assert len(inv) == 3
        for wid, wtype, title in inv:
            assert wid and wid != "unknown"
            assert wtype != "custom"
            assert title

    def test_user_phrase_resolves_to_exactly_one_widget(self):
        """'close the san jose to sf map' must be resolvable from the inventory."""
        inv = list(_iter_canvas_widgets(_canvas()))
        hits = [w for w in inv
                if "san jose" in w[2].lower() and "sf" in w[2].lower()]
        assert [h[0] for h in hits] == ["traffic-c20bc01b"]

    def test_canvas_summary_line_carries_the_id(self):
        summary = get_canvas_summary(_canvas())
        assert "#traffic-c20bc01b" in summary
        assert "San Jose → SF" in summary
        assert "custom" not in summary


class TestTrafficReuse:
    def test_prefix_reuse_spans_the_map_iframe_fork(self, monkeypatch):
        """build_traffic_widget returns `map` when geocoding succeeds and
        `iframe_app` when it misses. Type-keyed reuse can't see across that fork,
        so consecutive traffic asks stacked duplicates."""
        canvas = _canvas()
        monkeypatch.setattr(m, "get_session_canvas", lambda _s: canvas)
        assert find_existing_widget_by_id_prefix("s", "traffic") == "traffic-c20bc01b"

    def test_type_keyed_reuse_alone_misses_the_iframe_traffic_widget(self, monkeypatch):
        """Pins WHY the prefix lookup exists: searching for a 'map' finds only the
        geocoded one, so an iframe_app traffic widget would be duplicated."""
        canvas = G("iframe_app", "traffic-c20bc01b",
                   {"title": "San Jose → SF", "url": "https://x.com"})
        monkeypatch.setattr(m, "get_session_canvas", lambda _s: canvas)
        assert find_existing_widget("s", "map") is None
        assert find_existing_widget_by_id_prefix("s", "traffic") == "traffic-c20bc01b"

    def test_prefix_lookup_returns_none_on_an_empty_canvas(self, monkeypatch):
        monkeypatch.setattr(m, "get_session_canvas", lambda _s: "")
        assert find_existing_widget_by_id_prefix("s", "traffic") is None

    def test_prefix_lookup_does_not_match_a_different_role(self, monkeypatch):
        monkeypatch.setattr(m, "get_session_canvas",
                            lambda _s: G("data_card", "news-top", {"title": "N", "answer": "x"}))
        assert find_existing_widget_by_id_prefix("s", "traffic") is None


class TestRemoveSafety:
    """The remove path is destructive and had no verification at all."""

    def _widgets_for(self, canvas, selector):
        soup = BeautifulSoup(canvas, "html.parser")
        return [x for x in soup.select(selector)
                if "widget-container" in (x.get("class") or [])
                or "glass-card" in (x.get("class") or [])]

    def test_a_broad_selector_matches_many_widgets(self):
        """The precondition for the guard: these selectors are ambiguous, and the
        old code silently took the first match."""
        for sel in (".widget-container", "div"):
            assert len(self._widgets_for(_canvas(), sel)) > 1

    def test_an_id_selector_is_unambiguous(self):
        assert len(self._widgets_for(_canvas(), "#traffic-c20bc01b")) == 1

    def test_the_matched_widget_can_be_named_before_it_is_destroyed(self):
        """A remove must be able to report WHAT it removed, so a mistarget is
        visible instead of surfacing as a bare success."""
        w = self._widgets_for(_canvas(), "#traffic-c20bc01b")[0]
        assert w.get("id") == "traffic-c20bc01b"
        assert _classify_canvas_widget(w) == "iframe_app"
        assert w.get("data-widget-title") == "San Jose → SF"


class TestToolEndpoints:
    """End-to-end through the real internal tool executor, not just its helpers."""

    @staticmethod
    def _canvas():
        return _canvas()

    @pytest.mark.asyncio
    async def test_read_dom_returns_actionable_ids(self):
        from app.main import InternalToolRequest, internal_tool_execute
        res = await internal_tool_execute(InternalToolRequest(
            tool="canvas_read_dom", args={"canvas_html": self._canvas()}))
        # Was 0 on a canvas full of widgets: it selected `.glass-card`, which no
        # factory widget uses.
        assert res["component_count"] == 3
        for c in res["components"]:
            assert c["id"] and c["selector"] == f"#{c['id']}"
            assert c["type"] != "custom"
        assert {c["id"] for c in res["components"]} == {
            "traffic-10cd8af3", "traffic-c20bc01b", "news-top"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selector", [".widget-container", "div"])
    async def test_ambiguous_remove_is_refused_with_candidates(self, selector):
        from app.main import InternalToolRequest, internal_tool_execute
        res = await internal_tool_execute(InternalToolRequest(
            tool="canvas_modify_dom",
            args={"canvas_html": self._canvas(), "css_selector": selector,
                  "action": "remove"}))
        assert res.get("is_error") is True
        assert "refusing to guess" in res["error"]
        assert len(res["candidates"]) == 3

    @pytest.mark.asyncio
    async def test_id_remove_succeeds_and_reports_what_it_removed(self):
        from app.main import InternalToolRequest, internal_tool_execute
        res = await internal_tool_execute(InternalToolRequest(
            tool="canvas_modify_dom",
            args={"canvas_html": self._canvas(),
                  "css_selector": "#traffic-c20bc01b", "action": "remove"}))
        assert res["success"] is True
        # A bare {"success": true} is what let a mistarget go unnoticed.
        assert res["affected"] == {"id": "traffic-c20bc01b", "type": "iframe_app",
                                   "title": "San Jose → SF"}
        assert "traffic-c20bc01b" not in res["rendered_html"]
        assert "traffic-10cd8af3" in res["rendered_html"]  # the others survive
