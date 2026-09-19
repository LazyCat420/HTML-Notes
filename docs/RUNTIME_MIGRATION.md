# HTML-Notes Shared Runtime Migration & Cleanup Guide

**Version**: 1.2.0  
**Owner**: Developer 3  
**Date**: 2026-09-19  

---

## 1. Boundary Principle

The shared system (`lazy-agent-service` + `lazycat-sdk`) owns **how agent runs execute and app-neutral capabilities work**.  
HTML-Notes owns **what its data/resources are and how its UI/state changes**.

```text
Shared Runtime (lazy-agent-service)
  ↳ Admits run, enforces budget, manages state machine, streams RunEvents
     ↳ Global capabilities (global.web.search, global.web.read_page) executed by runtime
     ↳ Local tools admitted by runtime, emitted as tool.invoked events with authorization receipts
        ↳ HTML-Notes LocalToolExecutor executes domain operations locally
```

---

## 2. Local Domain Tool Manifest (`html_notes.domain-tools.json`)

All local tools strictly declare:
- `owner`: `"html-notes"`
- `execution`: `"local"`
- `effect`: `"read"` | `"write"` | `"destructive"`
- `resource_type`: `"canvas_widget"`, `"note"`, `"portal_app"`, `"watch"`, etc.
- `required_scope`: `["app_id", "session_id"]` for all write/destructive operations.
- `requires_confirmation`: `true` for destructive actions.
- `legacy_aliases`: List of backwards-compatible tool names.
- `input_schema` and `result_schema`.

---

## 3. Alias Retirement Table

| Legacy Tool Alias | Canonical Replacement | Effect / Scope | Removal Gate | Status |
|---|---|---|---|---|
| `canvas_add_widget` | `html_notes.canvas.upsert_widget` | `write` / `["app_id", "session_id"]` | Runtime emits only canonical ID for 30 days / verified parity | Active Alias |
| `canvas_modify_dom` | `html_notes.canvas.mutate` or dedicated remove/update tools | `write` / `["app_id", "session_id"]` | Strict scope model implemented and exact `#id` required | Active Alias |
| `canvas_read_dom` | `html_notes.canvas.read` | `read` / `["app_id"]` | Canonical DOM inspection deployed | Active Alias |
| `canvas_remove_widget` | `html_notes.canvas.remove_widget` | `write` / `["app_id", "session_id"]` | Dedicated remove widget tool validated | Active Alias |
| `html_notes_create_note` | `html_notes.notes.create` | `write` / `["app_id", "session_id"]` | Canonical ID emitted by default prompt | Active Alias |
| `html_notes_update_note` | `html_notes.notes.update` | `write` / `["app_id", "session_id"]` | Multi-session update isolation validated | Active Alias |
| `html_notes_get_note` | `html_notes.notes.get` | `read` / `["app_id"]` | Note retrieval canonical ID deployed | Active Alias |
| `html_notes_search_notes` | `html_notes.notes.search` | `read` / `["app_id"]` | Note search canonical ID deployed | Active Alias |
| `html_notes_link_notes` | `html_notes.notes.link` | `write` / `["app_id", "session_id"]` | Backlink graph canonical ID deployed | Active Alias |
| `html_notes_list_services` | `html_notes.apps.list` | `read` / `["app_id"]` | Apps hub discovery deployed | Active Alias |
| `html_notes_open_app` | `html_notes.apps.open` | `read` / `["app_id"]` | Apps launcher deployed | Active Alias |
| `html_notes_list_actions` | `html_notes.apps.list_actions` | `read` / `["app_id"]` | Action spec discovery deployed | Active Alias |
| `html_notes_app_action` | `html_notes.apps.execute_action` | `destructive` / `["app_id", "session_id"]` | Destructive action confirmation gated | Active Alias |
| `html_notes_curate_app` | `html_notes.apps.curate` | `write` / `["app_id", "session_id"]` | App pinning/curation deployed | Active Alias |
| `html_notes_create_watch` | `html_notes.watches.create` | `write` / `["app_id", "session_id"]` | Mandatory expiry & session scope enforced | Active Alias |
| `html_notes_list_watches` | `html_notes.watches.list` | `read` / `["app_id", "session_id"]` | Session-scoped list deployed | Active Alias |
| `html_notes_cancel_watch` | `html_notes.watches.cancel` | `write` / `["app_id", "session_id"]` | Foreign watch rejection validated | Active Alias |
| `list_widget_types` | `html_notes.widgets.list_catalog` | `read` / `["app_id"]` | Widget catalog is canonical | Active Alias |
| `plan_widget` | None (omitted from profile) | `read` / `["app_id"]` | Removed from default agent profile | **Retired** (after 2026-09-01) |
| `create_widget` | `html_notes.canvas.create_custom_widget` | `write` / `["app_id", "session_id"]` | Legacy custom-widget users migrated | **Retired** (after 2026-09-01) |
| `update_widget` | `html_notes.canvas.update_custom_widget` | `write` / `["app_id", "session_id"]` | Legacy custom-widget users migrated | **Retired** (after 2026-09-01) |

---

## 4. Verification

Run the complete test suite:
```bash
pytest tests/test_domain_manifests.py tests/test_local_executor.py tests/test_tool_policy_and_effects.py tests/test_widget_catalog_parity.py
```
