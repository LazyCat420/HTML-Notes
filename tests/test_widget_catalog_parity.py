import pytest
from app.widgets.factory import WIDGET_RENDERERS, generate_widget_html
from app.presentation.widgets.catalog import widget_catalog

def test_every_catalog_widget_has_live_renderer():
    widgets = widget_catalog.get_all_widgets()
    assert len(widgets) >= 25, "Widget catalog must list all supported widgets"

    for w in widgets:
        w_type = w["type"]
        assert w_type in WIDGET_RENDERERS, f"Catalog widget '{w_type}' missing in WIDGET_RENDERERS"
        renderer_func = WIDGET_RENDERERS[w_type]
        assert callable(renderer_func), f"Renderer for '{w_type}' is not callable"

def test_widget_catalog_singleton_checks():
    assert widget_catalog.is_singleton("weather") is True
    assert widget_catalog.is_singleton("map") is True
    assert widget_catalog.is_singleton("app_grid") is True
    assert widget_catalog.is_singleton("settings") is True
    assert widget_catalog.is_singleton("clock") is False
    assert widget_catalog.is_singleton("data_card") is False

def test_generate_widget_html_for_catalog_types():
    sample_configs = {
        "clock": {"mode": "clock", "timezone": "UTC"},
        "stock_card": {"symbol": "AAPL"},
        "weather": {"location": "London"},
        "scoreboard": {"league": "nba"},
        "data_card": {"title": "Test Card", "items": [{"title": "Item 1", "description": "Desc"}]},
        "table": {"title": "Test Table", "columns": [{"key": "col1", "label": "Col 1"}], "rows": [{"col1": "Val"}]},
        "checklist": {"title": "To Do", "items": ["Task 1"]},
        "notes": {"title": "Notes", "content": "Note body"}
    }

    for wtype, cfg in sample_configs.items():
        html = generate_widget_html(wtype, f"test_{wtype}", cfg)
        assert f"test_{wtype}" in html or "widget-container" in html, f"Failed rendering {wtype}"
