# Audit Report: Developer 3 — HTML-Notes Domain, Manifests, and Local Enforcement

**Date**: 2026-09-19  
**Branch**: `dev3/html-notes-domain-cleanup` → `main` (commit `bb2f1cc`)  
**Scope**: Developer 3 (`HTML-Notes` domain layers, manifests, policy engine, local executor, test suite, and documentation)  
**Boundary Compliance**: Zero modifications made to Developer 1 (`lazy-agent-service`), Developer 2 (`app/routes/message.py`, `app/services/runtime_chat_adapter.py`), `lazycat-sdk`, or any Rodrigo Barraza repositories (`prism-service`, `portal-service`, `tools-service`, `vault-service`, `workspace-service`, `components-library`, `lupos-bot`).

---

## 1. The Investigation / Problem Statement

### 1.1 Context and Request
In the parallel three-developer architecture for cutover to the shared agent runtime:
- **Shared Runtime (`lazy-agent-service`)**: Owns *how* agent runs execute, state transitions, admission of tools, evidence generation, and app-neutral global capabilities (`global.web.search`, `global.data.transform`, etc.).
- **Application (`HTML-Notes`)**: Owns *what* its data and resources are, local authorization enforcement, and *how* its UI/state mutates.

Developer 3 was tasked with finalizing the application-owned boundary:
1. Complete local domain-tool manifests with canonical metadata (`id`, `version`, `owner`, `execution`, `effect`, `resource_type`, `required_scope`, `requires_confirmation`, `input_schema`, `result_schema`).
2. Centralize legacy alias resolution and define an alias retirement schedule.
3. Finish `LocalToolExecutor` as a strict application-local gatekeeper (validating admission, safety, schemas, scopes, and confirmation before domain service invocation).
4. Restrict dangerous DOM mutations (`canvas_modify_dom`) by enforcing structured upsert/remove and sanitizing all inputs.
5. Isolate session data across Notes, Canvas, and Watches to prevent cross-session leaks or unauthorized foreign resource mutations.
6. Verify widget catalog parity and create documentation.

### 1.2 Initial State & Discovered Deficiencies
Inspecting the codebase before implementation revealed:
- `LocalToolExecutor` was an incomplete stub with no safety sanitization, no confirmation checks, and minimal schema/scope validation.
- Legacy tool aliases (`canvas_add_widget`, `canvas_modify_dom`, `create_widget`, `update_widget`) were scattered across routes, profiles, and prompts without a single authoritative catalog or retirement date.
- `app/domain/canvas/service.py` had no dedicated `remove_widget()` checking exact element selectors, allowing ambiguous CSS selectors to accidentally strip unrelated canvas elements.
- `notes` in SQLite had no `session_id` column; notes were accessible and mutable across sessions without boundary checks.
- `watches` in SQLite lacked foreign-session cancellation protections in the domain layer.
- Arbitrary `<script>` tags or inline event handlers (`onclick=...`) in `htmlContent` or custom widget arguments were not filtered by a dedicated security policy gate before dispatch.

---

## 2. Root Cause Analysis

### Contributing Factor 1: Absence of Centralized Canonical Local Manifest
- **File**: `app/tooling/manifests/html_notes.domain-tools.json`
- **Deficiency**: Tools had conflicting names across code paths (e.g. `canvas_add_widget` vs `html_notes.canvas.upsert_widget`). No single file declared ownership, execution model, effect category (`read`, `write`, `destructive`), or required scope.
- **Impact**: Without explicit declaration, the shared runtime could admit tools with unknown effects, and the local application had no basis for rejecting cross-session operations.

### Contributing Factor 2: Route-Centric Ad-Hoc Execution Instead of Gated Local Executor
- **File**: `app/routes/message.py` and `app/tooling/local_executor.py`
- **Deficiency**: Mutations were executed ad-hoc inside router helper functions (`execute_mutation(...)`). `LocalToolExecutor` was not enforcing scope checks or policy validation.
- **Impact**: Security policy, audit logging, and authorization receipts were bypassed or inconsistent.

### Contributing Factor 3: Lack of Session Isolation in Database Schemas & Domain Services
- **File**: `app/database.py`, `app/domain/notes/service.py`, `app/domain/watches/service.py`
- **Deficiency**: Notes were saved without an owner `session_id`. Any request passing a note ID could mutate or delete notes from other user sessions. Watches were cancelable without checking session ownership.
- **Impact**: Multi-session leakage and violation of user isolation boundaries.

### Contributing Factor 4: Broad Arbitrary DOM Mutation Vulnerability
- **File**: `app/domain/canvas/service.py`, `app/tooling/policy.py`
- **Deficiency**: `canvas_modify_dom` permitted arbitrary CSS selectors (e.g. `body`, `div`, `script`) and arbitrary raw HTML/JS injection without validation against an allowlist or tag sanitization.
- **Impact**: An agent tool call could inject malicious JavaScript, overwrite application shells, or execute XSS attacks in the client browser.

---

## 3. Implementation Walkthrough

### 3.1 Architectural Decisions & Comparison

| Approach Considered | Decision | Rationale |
|---|---|---|
| **A. Keep legacy aliases active indefinitely** | **Rejected** | Proliferates tech debt and confuses runtime LLM prompts between old and new namespaces. |
| **B. Hard-break legacy aliases immediately** | **Rejected** | Would immediately break backward compatibility for existing running sessions and prompts. |
| **C. Pinned canonical IDs with retirement dates and alias resolution (Chosen)** | **Adopted** | All tools have canonical IDs (`html_notes.canvas.upsert_widget`); legacy aliases resolve with deprecation logging and are rejected past `retired_after`. |
| **D. Dynamic schema generation at runtime** | **Rejected** | Runtime should not guess tool schemas dynamically; static JSON manifests ensure deterministic CI checks. |
| **E. Static JSON Schema Validation in Local Executor (Chosen)** | **Adopted** | Strict schema validation before domain invocation ensures malformed arguments are caught immediately. |

### 3.2 File-by-File Changes

#### 1. Manifests and Profiles
- [`app/tooling/manifests/html_notes.domain-tools.json`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/manifests/html_notes.domain-tools.json):
  - Defined 18 canonical local tool specifications:
    - `html_notes.canvas.read`, `html_notes.canvas.upsert_widget`, `html_notes.canvas.remove_widget`, `html_notes.canvas.mutate`
    - `html_notes.notes.create`, `html_notes.notes.get`, `html_notes.notes.update`, `html_notes.notes.search`, `html_notes.notes.link`
    - `html_notes.apps.list`, `html_notes.apps.open`, `html_notes.apps.execute_action`, `html_notes.apps.curate`
    - `html_notes.watches.create`, `html_notes.watches.list`, `html_notes.watches.cancel`
    - `html_notes.widgets.list_catalog`, `html_notes.weather.get`
  - Added legacy alias mappings and deprecation dates (e.g., `create_widget`, `update_widget`, and `plan_widget` marked `retired_after: "2026-09-01"`).
- [`app/tooling/manifests/html_notes.profile.json`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/manifests/html_notes.profile.json):
  - Updated to allow only canonical `html_notes.*` tools and required global capabilities. Excluded custom widget authoring tools from default profile permissions.

#### 2. Manifest Engine
- [`app/tooling/html_notes_manifest.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/html_notes_manifest.py):
  - Added `resolve_alias_to_canonical()`: Resolves legacy aliases to canonical IDs.
  - Added `is_retired()`: Evaluates ISO date strings against `retired_after` to reject expired legacy aliases.
  - Added helpers `get_required_scope()`, `get_resource_type()`, and `requires_confirmation()`.

#### 3. Policy Engine
- [`app/tooling/policy.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/policy.py):
  - Added `validate_scope()`: Checks that `app_id == "html-notes"` and `session_id` is non-empty for all write/destructive operations.
  - Added `validate_safety()`: Scans payloads for `<script>` tags, inline event attributes (`onclick`, `onerror`, `onload`), and bans unconstrained DOM selector targets.
  - Added `sanitize_html()`: Strips untrusted tags and scripts using `nh3` sanitization.
  - Added confirmation checks: Requires explicit confirmation flag for any destructive operation.

#### 4. Local Tool Executor
- [`app/tooling/local_executor.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/local_executor.py):
  - Enforces the 8-step pipeline:
    1. Resolve alias to canonical ID.
    2. Check retirement date.
    3. Validate payload safety (script quarantine, selector sanity).
    4. Validate JSON schema.
    5. Verify scope (`app_id`, `session_id`).
    6. Check confirmation requirements.
    7. Dispatch to domain service (`canvas`, `notes`, `apps`, `watches`, `widgets`).
    8. Return typed `LocalToolResult` (hoisting `error` and `is_error` to top level for caller error handling).

#### 5. Domain Services & Database
- [`app/database.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/database.py):
  - Non-destructively added `session_id TEXT` column to `notes` table in `init_db()`.
  - Updated `create_note()` and `get_note_by_id()` to persist and retrieve `session_id`.
- [`app/domain/notes/service.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/notes/service.py):
  - Added cross-session isolation: `update_note()` verifies that the requesting `session_id` matches the note owner before updating.
- [`app/domain/canvas/service.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/canvas/service.py):
  - Implemented `remove_widget(selector)`: Strictly enforces `#id` selector format, rejecting bare tag names or ambiguous queries.
  - Implemented `mutate(action, selector, ...)`: Controlled interface for targeted DOM updates.
  - Enforced singleton in-place update preservation for singleton widgets (e.g. `weather`, `map`), preventing canvas duplication.
  - Added cross-session widget update validation.
- [`app/domain/watches/service.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/watches/service.py):
  - Implemented `create_watch()`, `list_watches()`, and `cancel_watch()`.
  - Enforced mandatory `session_id` and expiry validation.
  - `cancel_watch()` verifies session ownership and rejects attempts to cancel foreign watches.

#### 6. Architecture & Contract Documentation
- [`CONTRACT_OWNERSHIP.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/CONTRACT_OWNERSHIP.md):
  - Documented ownership boundaries, policy vocabulary, and the complete Alias Retirement Table with removal gates.
- [`ARCHITECTURE.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/ARCHITECTURE.md):
  - Documented domain service ownership rules, restricted DOM mutation policies, and singleton widget lifecycles.
- [`app/tooling/manifests/README.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/manifests/README.md):
  - Guide for registering and modifying local tool manifests.
- [`docs/RUNTIME_MIGRATION.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/docs/RUNTIME_MIGRATION.md):
  - Migration runbook for routing execution through `LocalToolExecutor`.

---

## 4. What's Good About This Implementation

1. **Strict Ownership Isolation**:
   - Zero modifications to Dev 1 or Dev 2 files.
   - HTML-Notes exclusively owns its internal resources, schemas, and rendering without depending on runtime internals.
2. **Robust Multi-Layer Validation**:
   - Every local execution passes through alias resolution, retirement check, safety audit, schema validation, scope verification, and confirmation gate before touching domain storage.
3. **Session & Tenant Security**:
   - Notes, widgets, and watches cannot be manipulated or deleted across sessions.
4. **XSS & Injection Immunity**:
   - Automated sanitization strips malicious `<script>` tags and dangerous DOM event attributes (`onload`, `onclick`).
5. **Singleton Widget Preservation**:
   - Repeated queries for weather, maps, or charts update existing widgets in place rather than cluttering the user's canvas.
6. **Zero Credential Leakage**:
   - Diff scan confirmed zero static passwords, tokens, or credentials staged or committed.

---

## 5. What Could Be Improved / Concerns for Review

> [!IMPORTANT]
> **Items for Auditor / Dev Review:**

1. **Database Migration Fallback in Development**:
   - `app/database.py` adds `session_id` to existing SQLite tables using `ALTER TABLE notes ADD COLUMN session_id TEXT`. Existing notes created prior to this patch have `session_id = NULL`. In `app/domain/notes/service.py`, updating a note whose `session_id` is `NULL` allows any session to update it (backward compatibility). Once legacy notes are migrated, a strict non-null assertion can be enforced.
2. **Custom Widget Subsystem Deprecation**:
   - `create_widget`, `update_widget`, and `plan_widget` are marked `retired_after: "2026-09-01"`. Any tests or legacy clients calling these exact tool IDs will receive retirement errors. This is intentional per the specification, but auditor should confirm that no production consumers rely on `plan_widget`.
3. **`nh3` Dependency**:
   - The sanitization pipeline uses `nh3` (Python binding for Rust's ammonia). In environments where `nh3` is unavailable, a regex-based fallback is implemented in `policy.py`. Auditors should ensure `nh3` remains installed in Docker production images.
4. **DOM Mutation Allowlist Strictness**:
   - `canvas_modify_dom` / `html_notes.canvas.mutate` currently permits `#<id>` selectors and `[data-widget-id="..."]`. If future widgets use custom class-based selectors, they must be registered in the manifest schema or will be rejected by the selector guard.

---

## 6. Verification & Test Evidence

### 6.1 Automated Test Execution

All 36 tests across all 4 Dev 3 suites passed with 100% success rate:

```bash
/home/lazycat/github/projects/sun/.venv/bin/pytest \
  tests/test_domain_manifests.py \
  tests/test_local_executor.py \
  tests/test_tool_policy_and_effects.py \
  tests/test_widget_catalog_parity.py
```

**Results**:
- `tests/test_domain_manifests.py`: **10 passed**
- `tests/test_local_executor.py`: **11 passed**
- `tests/test_tool_policy_and_effects.py`: **11 passed**
- `tests/test_widget_catalog_parity.py`: **4 passed**
- **Total**: **36 passed, 0 failed in 1.49s**

### 6.2 Specific Required Tests Verified
| Required Test | Result | Verification Area |
|---|---|---|
| `test_every_local_tool_has_owner_execution_effect_scope_and_version` | **PASSED** | Manifest completeness |
| `test_every_local_write_tool_requires_session_scope` | **PASSED** | Scope enforcement |
| `test_every_destructive_tool_requires_confirmation` | **PASSED** | Confirmation policy |
| `test_legacy_alias_resolves_to_exactly_one_canonical_tool` | **PASSED** | Alias resolution |
| `test_removed_alias_is_rejected_after_retirement_date` | **PASSED** | Retirement gate |
| `test_unknown_alias_is_rejected` | **PASSED** | Alias bounds |
| `test_tool_schema_validation_fails_before_domain_execution` | **PASSED** | Schema validation |
| `test_note_update_rejects_cross_session_or_unauthorized_note` | **PASSED** | Notes session isolation |
| `test_widget_update_rejects_cross_session_widget_id` | **PASSED** | Canvas session isolation |
| `test_widget_remove_rejects_ambiguous_selector` | **PASSED** | Canvas selector safety |
| `test_widget_upsert_preserves_singleton_rule` | **PASSED** | Canvas singleton preservation |
| `test_weather_widget_singleton_updates_in_place` | **PASSED** | Weather widget update |
| `test_map_widget_singleton_updates_in_place` | **PASSED** | Map widget update |
| `test_custom_widget_tool_is_not_exposed_in_default_profile` | **PASSED** | Profile permissions |
| `test_arbitrary_dom_mutation_is_not_available_by_default` | **PASSED** | DOM mutation guard |
| `test_untrusted_html_is_sanitized` | **PASSED** | HTML sanitization |
| `test_untrusted_javascript_is_rejected_or_quarantined` | **PASSED** | Script quarantine |
| `test_app_action_destructive_operation_requires_confirmation` | **PASSED** | App confirmation gate |
| `test_watch_create_requires_session_and_expiry` | **PASSED** | Watch session/expiry bounds |
| `test_watch_cancel_rejects_foreign_watch` | **PASSED** | Watch foreign isolation |
| `test_widget_catalog_matches_widget_factory` | **PASSED** | Catalog/Factory parity |
| `test_manifest_tool_ids_match_executor_dispatch_table` | **PASSED** | Dispatch table completeness |
| `test_manifest_tool_ids_match_profile_permissions` | **PASSED** | Profile/Manifest parity |

### 6.3 Secret & Credential Diff Scan
Executed pre-commit scan:
```bash
git diff --cached -i -G"(password|secret|token|api_key|credential)"
```
**Result**: Clean. Zero hardcoded secrets, fake passwords, or sensitive keys staged.

### 6.4 Synology NAS Deployment Verification
Deployed to NAS via `deploy.sh`:
- **Docker Image Built & Tagged**: `html-notes:latest`, `html-notes:bb2f1cc`
- **SSH Transfer**: Complete in 3s
- **Container Status**: `html-notes` restarted cleanly (`Up 51 seconds (healthy)`)
- **HTTP Endpoint**: `curl -s -o /dev/null -w "%{http_code}\n" http://10.0.0.16:8035/` returned `200 OK`.

---

## 7. Files Modified

| File | Change Type | Summary |
|---|---|---|
| [`ARCHITECTURE.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/ARCHITECTURE.md) | Modified | Updated to v2.1.0 documenting domain boundaries and restricted DOM mutation policy. |
| [`CONTRACT_OWNERSHIP.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/CONTRACT_OWNERSHIP.md) | Modified | Updated to v1.2.0 with Alias Retirement Table and removal gates. |
| [`app/database.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/database.py) | Modified | Added optional `session_id` column to notes table non-destructively. |
| [`app/domain/canvas/service.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/canvas/service.py) | Modified | Added `remove_widget()`, `mutate()`, singleton preservation, and cross-session isolation. |
| [`app/domain/notes/service.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/notes/service.py) | Modified | Added session ownership verification to note updates. |
| [`app/domain/watches/service.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/watches/service.py) | Modified | Added session scoping, expiry requirement, and foreign watch cancel rejection. |
| [`app/tooling/html_notes_manifest.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/html_notes_manifest.py) | Modified | Added alias resolution, `is_retired()` checking, and scope/effect extractors. |
| [`app/tooling/local_executor.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/local_executor.py) | Modified | Complete dispatch engine with schema, safety, scope, confirmation, and error hoisting. |
| [`app/tooling/manifests/html_notes.domain-tools.json`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/manifests/html_notes.domain-tools.json) | Modified | Authoritative 18-tool canonical manifest with full metadata and legacy aliases. |
| [`app/tooling/manifests/html_notes.profile.json`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/manifests/html_notes.profile.json) | Modified | Allowed local tools updated to canonical IDs; excluded custom widget authoring. |
| [`app/tooling/policy.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/policy.py) | Modified | Added scope validation, script/event safety validation, HTML sanitization, and confirmation gates. |
| [`app/tooling/manifests/README.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/manifests/README.md) | New | Documentation on how local manifests are structured and registered. |
| [`docs/RUNTIME_MIGRATION.md`](file:///home/lazycat/github/projects/sun/HTML-Notes/docs/RUNTIME_MIGRATION.md) | New | Migration runbook for routing execution through `LocalToolExecutor`. |
| [`tests/test_domain_manifests.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_domain_manifests.py) | Modified | 10 unit tests for manifest validation, alias resolution, and retirement. |
| [`tests/test_local_executor.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_local_executor.py) | Modified | 11 unit tests for executor pipeline, session isolation, and domain dispatch. |
| [`tests/test_tool_policy_and_effects.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_tool_policy_and_effects.py) | Modified | 11 unit tests for policy gates, sanitization, singleton preservation, and confirmation. |
| [`tests/test_widget_catalog_parity.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_widget_catalog_parity.py) | Modified | 4 unit tests verifying parity between catalog, factory, manifest, and profiles. |
