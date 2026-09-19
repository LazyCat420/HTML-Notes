# Contract Ownership & Source of Truth Specification

**Version**: 1.2.0  
**Status**: Authoritative Reference  
**Last Updated**: 2026-09-19  

---

## 1. Authority Matrix

To prevent divergence, race conditions, and duplicated schema copies, ownership across repositories is strictly demarcated:

| Artifact / Contract | Authoritative Owner | Distribution Mechanism | Consumer Repositories |
|---|---|---|---|
| **Run Protocol & State Machine** (`RunRequest`, `RunEvent`, `ContextReceipt`) | `lazy-agent-service` | Versioned package / contracts export (`dist/contracts/`) | `HTML-Notes`, `trading-service`, `lazycat-sdk` |
| **Agent Profile Spec** (`agent-profile-spec-v1.json`) | `lazy-agent-service` | `docs/contracts/agent-profile-spec-v1.json` | All consumer agents registering profiles |
| **Transport Normalization & Wire SDK** | `lazycat-sdk` | Python Package (`lazycat.client.RuntimeClient`) | `HTML-Notes`, `trading-service` |
| **HTML-Notes Profile** (`html_notes.profile.json`) | `HTML-Notes` | `app/tooling/manifests/html_notes.profile.json` | Registered with `lazy-agent-service` at deploy time |
| **HTML-Notes Domain Tools** (`html_notes.domain-tools.json`) | `HTML-Notes` | `app/tooling/manifests/html_notes.domain-tools.json` | `HTML-Notes` internal execution engine |
| **Widget Catalog** (`html_notes.widget-catalog.json`) | `HTML-Notes` | `app/tooling/manifests/html_notes.widget-catalog.json` | `HTML-Notes` UI and canvas factory |
| **Global Capabilities** (`global.web.*`, `global.data.*`) | `lazy-agent-service` | Capability registry in shared runtime | Referenced declaratively in application profiles |

---

## 2. Elimination of Sibling Filesystem Coupling

Historical test suites relied on relative sibling filesystem paths:
```python
# DEFECTIVE HISTORICAL PATTERN:
schema_path = pathlib.Path("../lazy-agent-service/tool_schemas.json")
```

This violated isolated deployment and container hermeticity rules. The following rules are now strictly enforced:
1. **Zero Cross-Repo Filesystem Traversals**: No test, import, or deployment step may reference `../lazy-agent-service` or sibling checkouts.
2. **Packaged Manifest Authority**: HTML-Notes reads its application profile, domain tool schemas, and widget catalog from in-repo manifests in `app/tooling/manifests/`.
3. **External Protocol Verification**: Contract parity tests validate against packaged schema definitions or shared artifacts.

---

## 3. Alias Retirement Table

To ensure zero downtime during multi-stream development, legacy aliases are mapped to canonical tools in `app/tooling/manifests/html_notes.domain-tools.json` and governed by removal gates:

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

## 4. Historical Document Supersession

The following historical documents and code comments are explicitly superseded:
- [`app/tools_schema.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tools_schema.py): Superseded by `app/tooling/manifests/html_notes.domain-tools.json` and this document.
- Prior notes referencing generating flat schemas in sibling checkouts: Superseded by packaged in-repo manifests.

---

## 5. Developer 2 Production Runtime Cutover (Completed 2026-09-19)

The shared runtime production route cutover is complete:
- **Route Cutover**: `app/routes/message.py` now routes all shared runtime local tool execution through `execute_local_runtime_tool` → `local_tool_executor.execute()` → `sse_formatter.from_local_result()`, eliminating legacy `execute_mutation` callback bridges for the shared runtime path.
- **Contract & Profile Preflight**: Handshake validation verifies `HTML_NOTES_CONTRACT_VERSION` compatibility with SDK and runtime before streaming commences.
- **Degraded Mode & Terminal Invariants**: Guaranteed exactly one terminal `done` SSE frame per stream; outages and denials produce structured error events without fabricating components or receipts.
- **Full Cutover Test Suite**: Verified with 20 comprehensive automated tests in `tests/test_runtime_cutover.py` covering scope validation, canonical tool dispatch, alias resolution, outage handling, and cancellation idempotency.

