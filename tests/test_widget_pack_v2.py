"""Widget-pack v2 (2026-07-21 deep-audit wave): new renderers + guardrails.

Covers the six new premade widgets (table, kpi_row, timeline, versus_card,
profile_card, progress), the generic multi-series chart contract, the widget
type alias map, and the escaping/sandbox/SSRF guardrails the audit added.
"""
import pytest

from app.widgets.factory import (
    WIDGET_RENDERERS, generate_widget_html, _normalize_series,
)
import app.main as m


# ── new renderers exist and stamp identity ──────────────────────────────────

NEW_TYPES = ["table", "kpi_row", "timeline", "versus_card", "profile_card",
             "progress", "multi_chart"]


@pytest.mark.parametrize("wtype", NEW_TYPES)
def test_new_types_registered(wtype):
    assert wtype in WIDGET_RENDERERS


def _sample_config(wtype):
    return {
        "table": {"title": "T", "columns": [{"key": "a", "label": "A"},
                                            {"key": "n", "label": "N", "format": "number"}],
                  "rows": [{"a": "x", "n": 3}, {"a": "y", "n": 1}]},
        "kpi_row": {"title": "K", "metrics": [{"label": "CPI", "value": "2.7", "unit": "%",
                                               "delta": "-0.3", "good": "down",
                                               "spark": [3.0, 2.9, 2.7]}]},
        "timeline": {"title": "TL", "events": [
            {"date": "2026-07-02", "title": "Later", "url": "https://reuters.com/x"},
            {"date": "2026-06-01", "title": "Earlier"}]},
        "versus_card": {"title": "A vs B", "entities": [{"name": "A"}, {"name": "B"}],
                        "rows": [{"label": "P/E", "values": ["1", "2"], "winner": 0}],
                        "verdict": "A wins."},
        "profile_card": {"title": "P", "facts": [{"label": "Born", "value": "1900"}],
                         "answer": "Bio.", "links": [{"label": "W", "url": "https://w.org"}]},
        "progress": {"title": "G", "items": [{"label": "Fund", "value": 82, "target": 100}]},
        "multi_chart": {"title": "M", "labels": ["a", "b"],
                        "series": [{"label": "S1", "values": [1, 2]},
                                   {"label": "S2", "values": [3, 4]}]},
    }[wtype]


@pytest.mark.parametrize("wtype", NEW_TYPES)
def test_new_renderers_stamp_identity(wtype):
    html = generate_widget_html(wtype, f"{wtype}-1", _sample_config(wtype))
    assert f'id="{wtype}-1"' in html
    assert 'data-sig="' in html
    assert f'data-widget-type="{wtype}"' in html


def test_table_sorts_and_formats():
    html = generate_widget_html("table", "t1", {
        "title": "T",
        "columns": [{"key": "m", "label": "M"},
                    {"key": "p", "label": "P", "format": "currency"}],
        "rows": [{"m": "big", "p": 900}, {"m": "small", "p": 100}],
        "sort": {"key": "p", "dir": "asc"}})
    assert html.index("small") < html.index("big")
    assert "$900" in html and "tabular-nums" in html


def test_table_escapes_cells():
    html = generate_widget_html("table", "t2", {
        "columns": [{"key": "a", "label": "A"}],
        "rows": [{"a": "<script>alert(1)</script>"}]})
    assert "<script>alert" not in html


def test_timeline_sorts_desc_and_keeps_unparseable_order():
    html = generate_widget_html("timeline", "tl1", {
        "events": [{"date": "2026-06-01", "title": "OLD"},
                   {"date": "2026-07-01", "title": "NEW"}]})
    assert html.index("NEW") < html.index("OLD")
    # Unparseable dates: emission order preserved, raw string rendered.
    html = generate_widget_html("timeline", "tl2", {
        "events": [{"date": "sometime", "title": "First"},
                   {"date": "later", "title": "Second"}]})
    assert html.index("First") < html.index("Second")


def test_versus_card_marks_winner_and_degrades():
    html = generate_widget_html("versus_card", "v1", _sample_config("versus_card"))
    assert "ring-emerald-400/25" in html
    # <2 entities degrades to a data_card, never a broken grid.
    degraded = generate_widget_html("versus_card", "v2",
                                    {"entities": [{"name": "A"}], "rows": []})
    assert 'data-widget-type="versus_card"' in degraded  # stamped as asked
    assert "data-card" in degraded


def test_multi_series_normalization():
    cfg = _normalize_series({"labels": ["a", "b"],
                             "series": [{"label": "X", "values": [10, 20]},
                                        {"label": "Y", "values": [1]}],
                             "normalize": True})
    ds = cfg["data"]["datasets"]
    assert len(ds) == 2
    assert ds[0]["data"] == [0.0, 100.0]          # % change from first value
    assert ds[1]["data"] == [0.0, None]           # padded to label count
    assert _normalize_series({"labels": [], "series": []}) is None


# ── alias map + degenerate gate ─────────────────────────────────────────────

@pytest.mark.parametrize("alias,real", [
    ("music", "mini_music_player"), ("embedded_app", "iframe_app"),
    ("embed", "iframe_app"), ("data_table", "table"), ("kpi", "kpi_row"),
    ("versus", "versus_card"), ("profile", "profile_card"),
])
def test_widget_type_aliases(alias, real):
    assert m._WIDGET_TYPE_ALIASES[alias] == real
    assert real in WIDGET_RENDERERS


def test_alias_map_targets_are_all_real():
    for real in m._WIDGET_TYPE_ALIASES.values():
        assert real in WIDGET_RENDERERS


@pytest.mark.parametrize("wtype,qkey", [
    ("profile_card", "profile_query"), ("timeline", "timeline_query"),
    ("table", "search_query"), ("kpi_row", "query"),
])
def test_query_only_new_widgets_are_degenerate(wtype, qkey):
    assert m._widget_is_degenerate(wtype, {qkey: "x", "title": "T"})


@pytest.mark.parametrize("wtype,cfg", [
    ("table", {"rows": [{"a": 1}]}),
    ("kpi_row", {"metrics": [{"label": "x", "value": 1}]}),
    ("versus_card", {"entities": [{"name": "A"}], "rows": [{"label": "x", "values": [1]}]}),
    ("profile_card", {"facts": [{"label": "x", "value": "y"}]}),
    ("timeline", {"events": [{"date": "2026-01-01", "title": "t"}]}),
])
def test_content_bearing_new_widgets_not_degenerate(wtype, cfg):
    assert not m._widget_is_degenerate(wtype, cfg)


def test_reusable_types_all_have_renderers():
    for t in m.REUSABLE_WIDGET_TYPES:
        assert t in WIDGET_RENDERERS, f"reuse-eligible type {t!r} has no renderer"


# ── guardrails ──────────────────────────────────────────────────────────────

def test_iframe_app_escapes_title_and_drops_same_origin():
    html = generate_widget_html("iframe_app", "if1", {
        "title": "</h3><script>alert(1)</script>", "url": "https://example.com"})
    assert "<script>alert" not in html
    assert "allow-same-origin" not in html


def test_iframe_app_embeddable_is_host_matched():
    evil = generate_widget_html("iframe_app", "if2",
                                {"url": "https://evil.com/?x=youtube.com/embed"})
    assert "/widgets/embed?u=" in evil
    yt = generate_widget_html("iframe_app", "if3",
                              {"url": "https://www.youtube.com/embed/abc"})
    assert 'src="https://www.youtube.com/embed/abc"' in yt


@pytest.mark.parametrize("url", [
    "http://localhost:8080/x", "http://127.0.0.1/", "http://10.0.0.16:3031/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "ftp://x/",
    "not-a-url", "",
])
def test_ssrf_guard_refuses_private(url):
    assert not m._is_public_http_url(url)


def test_ssrf_guard_allows_public():
    assert m._is_public_http_url("https://example.com/page")
