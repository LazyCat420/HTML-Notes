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

## 4. Required Runtime Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `USE_SHARED_RUNTIME` | `false` | Feature flag toggling shared agent runtime vs legacy Prism route. |
| `LAZYCAT_RUNTIME_URL` | `http://10.0.0.16:5591` | Base URL of `lazy-agent-service` instance. |
| `HTML_NOTES_RUNTIME_PROFILE` | `html-notes-canvas-v1` | Canonical agent profile ID declared in local manifest. |
| `HTML_NOTES_CONTRACT_VERSION` | `1.2.0` | Required contract version specification. |
| `RUNTIME_CONNECT_TIMEOUT_SECONDS`| `5.0` | HTTP client connection timeout to runtime. |
| `RUNTIME_READ_TIMEOUT_SECONDS` | `90.0` | HTTP client read/stream timeout for agent runs. |
| `RUNTIME_MAX_CANVAS_CONTEXT_CHARS`| `4000` | Bounded character limit for canvas DOM context prompt. |

---

## 5. Startup & Preflight Readiness Validation

When `USE_SHARED_RUNTIME=true`, the application verifies before processing turns:
1. **Reachability**: Runtime endpoint responds to HTTP GET at `/v1/contracts/spec`.
2. **Contract Compatibility**: Runtime reports `1.2.0` or compatible v1 minor semver (`rep_maj == 1`, `rep_min >= 2`).
3. **Profile Alignment**: Configured profile matches declared profile in `app/tooling/manifests/html_notes.profile.json`.
4. **Capability Whitelist**: Profile permits required global capabilities (`global.web.search`, `global.web.read_page`).
5. **Fail-Closed Gate**: Preflight failures return structured `RuntimeReadinessResult` without silent fallback.

---

## 6. Legacy Note Ownership Migration Procedure

Notes with `session_id = NULL` are transitioned to `owner_type = 'legacy_unclaimed'` to prevent cross-session tampering:
```bash
# Preview candidates
python3 scripts/migrate_legacy_note_ownership.py --dry-run

# Execute migration
python3 scripts/migrate_legacy_note_ownership.py

# Rollback if needed
python3 scripts/migrate_legacy_note_ownership.py --rollback
```

---

## 7. Verification & Testing

Run the Dev 2 readiness, adapter, cutover, and ownership test suites:
```bash
uv run pytest \
  tests/test_runtime_readiness.py \
  tests/test_runtime_chat_adapter.py \
  tests/test_runtime_cutover.py \
  tests/test_legacy_note_migration.py \
  tests/test_notes_session_ownership.py -v
```

