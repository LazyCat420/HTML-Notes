# Runtime Hardening Integration & Release Plan (Phase F)

> **Role**: Developer 3 (Integrator & Release Authority)  
> **Target Branch**: `integrate/runtime-hardening-release` (worktree: `HTML-Notes/.worktrees/dev3-integrator`)  
> **Baseline Commit**: `b8a7fc8`  
> **Scope**: `HTML-Notes/app/adapters/runtime/config.py`, `app/services/runtime_chat_adapter.py`, `app/routes/message.py`, `app/routes/health.py`, `app/main.py`, `docker-compose.yml`, `tests/test_runtime_hardening_integration.py`, `tests/test_runtime_integration.py`, `docs/INTEGRATION_2026-09-19.md`  
> **External Coordination**: Dev 1 (`lazy-agent-service` + `models.py`/`local_executor.py`), Dev 2 (`database.py` + `domain/notes` + `routes/notes.py`/`internal.py`). Zero modifications to Rodrigo Barraza repositories (`prism-service`, `portal-service`, `tool-service`, `vault-service`, `workspace-service`, `components-library`, `lupos-bot`).

---

## 1. Problem Diagnosis & Evidence Baseline

Following the Verified-Claim Plan Methodology (VCPM), each reproduced defect from the audit at baseline `b8a7fc8` is classified with direct artifact evidence:

| ID | Priority | Finding & Impact | Primary Evidence Source | Classification |
|---|---|---|---|---|
| **GAP-1** | **P1** | **Producer omits run context from receipt**: Nested `authorization_receipt` in `lazy-agent-service` omits `run_id`, `tool_call_id`, `session_id`, `app_id`, `profile_id`, and `expires_at`. The adapter forwards only `data.get("authorization_receipt")`, returning `MISSING_RUN_ID` on write calls. | [`lazy-agent-service/src/services/RunExecutionEngine.ts:728-735`](file:///home/lazycat/github/projects/sun/lazy-agent-service/src/services/RunExecutionEngine.ts#L728-L735), [`HTML-Notes/app/services/runtime_chat_adapter.py:275`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/services/runtime_chat_adapter.py#L275), [`HTML-Notes/app/adapters/runtime/models.py:185`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/adapters/runtime/models.py#L185) | **Verified Fact** |
| **GAP-2** | **P1** | **Unkeyed hash & incomplete receipt binding**: Producer generates `sha256` hash without a key/secret (`crypto.createHash("sha256")`). `LocalToolExecutor` supplies no signature verifier, accepts unsigned receipts when `signature` is None, and does not assert active run/call IDs match the receipt. | [`RunExecutionEngine.ts:723-726`](file:///home/lazycat/github/projects/sun/lazy-agent-service/src/services/RunExecutionEngine.ts#L723-L726), [`models.py:280-293`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/adapters/runtime/models.py#L280-L293), [`local_executor.py:156-162`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tooling/local_executor.py#L156-L162) | **Verified Fact** |
| **GAP-3** | **P1** | **Bypassed note ownership**: `api_update_note` and internal tools call `database.update_note()` directly, bypassing `NotesDomainService`. `NotesDomainService.update_note()` checks `if note_session and session_id`, allowing updates with `session_id=None` to pass. `link_notes` accepts no `session_id` and does not verify note owners or target existence. | [`app/routes/notes.py:49`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/notes.py#L49), [`app/routes/internal.py:63`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/internal.py#L63), [`app/domain/notes/service.py:65`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/notes/service.py#L65), [`app/domain/notes/service.py:127`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/domain/notes/service.py#L127) | **Verified Fact** |
| **GAP-4** | **P1** | **Readiness check uncalled & fails open**: `check_runtime_readiness()` is never invoked in `main.py` lifespan, `/health/agent`, or chat routing. In `config.py:287`, if `registered_profiles` is `None` (not provided by `/v1/contracts/spec`), profile checking is completely skipped. Missing local manifests (`html_notes.domain-tools.json`, `html_notes.profile.json`) are never checked. | [`app/adapters/runtime/config.py:286-297`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/adapters/runtime/config.py#L286-L297), [`app/routes/health.py:72`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/health.py#L72), [`lazy-agent-service/src/routes/ContractRoutes.ts:19-28`](file:///home/lazycat/github/projects/sun/lazy-agent-service/src/routes/ContractRoutes.ts#L19-L28) | **Verified Fact** |
| **GAP-5** | **P2** | **Incomplete replay protection**: `ReplayCache` keys the nonce with `f"{auth.run_id}:{auth.tool_call_id}:{auth.nonce}"`. Using the same nonce with a different `tool_call_id` bypasses the cache. The cache TTL is fixed at 300s rather than tracking the receipt's `expires_at`. | [`app/adapters/runtime/models.py:298-310`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/adapters/runtime/models.py#L298-L310), [`models.py:117`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/adapters/runtime/models.py#L117) | **Verified Fact** |
| **GAP-6** | **P2** | **Non-atomic claims & missing claim endpoint**: `database.claim_note()` reads note version and executes an unconditional `UPDATE notes ... WHERE id = ?`, permitting race conditions. No HTTP API route exists for claiming notes. | [`app/database.py:366-389`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/database.py#L366-L389), [`app/routes/notes.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/notes.py) | **Verified Fact** |
| **GAP-7** | **P2** | **Integration test coverage gap & deployment omission**: `test_runtime_hardening_integration.py` invoked `executor.execute()` directly with mocks, skipping the HTTP route bridge. `docker-compose.yml` does not specify `USE_SHARED_RUNTIME` (defaults `False`). Deploy checks only tested homepage `200` without verifying runtime readiness or mutations. | [`tests/test_runtime_hardening_integration.py:76`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_runtime_hardening_integration.py#L76), [`docker-compose.yml:10-31`](file:///home/lazycat/github/projects/sun/HTML-Notes/docker-compose.yml#L10-L31), [`deploy-kit/.env.deploy`](file:///home/lazycat/github/projects/sun/deploy-kit/.env.deploy) | **Verified Fact** |

---

## 2. Interface Decisions Record (Dev 3 Authority)

To prevent cross-developer conflicts and race conditions, the following four canonical contracts are established:

### 2.1 Interface 1: Receipt Schema and Verification Inputs
- **Producer (`lazy-agent-service`) Contract**:
  - The nested `authorization_receipt` inside `tool.invoked` MUST contain:
    - `receipt_id`: `string` (unique nonce formatted `auth_rec_<runId>_<uuid>`)
    - `run_id`: `string` (matching the active run)
    - `tool_call_id`: `string` (matching the active tool call)
    - `tool_name`: `string` (canonical or admitted name)
    - `app_id`: `string` (`html-notes`)
    - `session_id`: `string` (from run execution context)
    - `profile_id`: `string` (from run execution context)
    - `execution`: `"local"`
    - `effect`: `"read"` | `"write"` | `"destructive"`
    - `issued_at`: ISO 8601 UTC string
    - `expires_at`: ISO 8601 UTC string (default: `issued_at + 300s`)
    - `signature`: `string` formatted `hmac-sha256-<hex>` computed using shared secret (`INTERNAL_EXECUTE_TOKEN` or `RUNTIME_AUTH_SECRET`) over `run_id:tool_call_id:tool_name:app_id:session_id:profile_id:receipt_id:expires_at`.
- **Adapter Bridge (`HTML-Notes`) Contract**:
  - `RuntimeChatAdapter` combines `event.run_id`, `data.tool_call_id`, `data.required_scope`, and `data.authorization_receipt` into a normalized typed `LocalToolAuthorization` dictionary.
  - Passes full context to `execute_local_tool_cb(canonical_name, tool_args, authorization_dict, request_context, runtime_context)`.
- **Executor & Validator (`HTML-Notes`) Contract**:
  - `verify_local_authorization(...)` signature:
    ```python
    def verify_local_authorization(
        authorization: Union[LocalToolAuthorization, Dict[str, Any]],
        expected_tool_id: str,
        expected_app_id: str,
        expected_session_id: str,
        expected_run_id: Optional[str] = None,
        expected_tool_call_id: Optional[str] = None,
        expected_profile_id: Optional[str] = None,
        signature_verifier: Optional[Callable[[LocalToolAuthorization], bool]] = None,
        replay_cache: Optional[ReplayCache] = None,
    ) -> AuthorizationVerificationResult:
    ```
  - Replay protection checks the `receipt_id`/`nonce` independently: `cache.check_and_add(f"nonce:{auth.nonce}", expires_at_ts=auth.expires_at.timestamp())`. If seen, returns `REPLAYED_RECEIPT`.
  - Signature is mandatory: missing signature fails with `UNSIGNED_RECEIPT`. Signature verification against the shared token must pass.

### 2.2 Interface 2: Note-Service Session Parameters and Signatures
- **Domain Service Signatures (`app/domain/notes/service.py`)**:
  - `update_note(note_id: str, session_id: str, **fields) -> Dict[str, Any]`:
    - `session_id` is **mandatory** (`str`, non-empty). If missing or empty, rejects with `code="SESSION_REQUIRED"`.
    - If note is currently unclaimed (`session_id is None`), rejects update with `code="NOTE_UNCLAIMED"`; caller must claim first.
    - If `note.session_id != session_id`, rejects update with `code="NOTE_SESSION_MISMATCH"`.
  - `link_notes(source_note_id: str, target_note_id: str, session_id: str) -> Dict[str, Any]`:
    - `session_id` is **mandatory**.
    - Verifies `source_note` exists and `source_note.session_id == session_id`.
    - Verifies `target_note` exists (returns `NOTE_NOT_FOUND` if missing).
  - `create_note(...)`: accepts mandatory `session_id`.
- **HTTP & Internal Routes Wiring**:
  - `app/routes/notes.py` (`POST /notes/update`, `POST /notes/link`):
    - Reads `session_id` from request body (or auth header).
    - Calls `notes_service.update_note(...)` and `notes_service.link_notes(...)`. **Never** calls `database.update_note` directly!
  - `app/routes/internal.py`:
    - All note mutation calls dispatch through `notes_service` with verified `session_id`.

### 2.3 Interface 3: Claim Response and Atomic DB Claim Contract
- **Atomic Database Operation (`app/database.py`)**:
  - `claim_note(note_id: str, session_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]`:
    ```sql
    UPDATE notes
    SET session_id = ?, owner_type = 'session', owner_id = ?, claimed_at = ?, updated_at = ?, version = version + 1
    WHERE id = ? AND (session_id IS NULL OR owner_type = 'legacy_unclaimed');
    ```
  - Evaluates `cursor.rowcount == 1`. If 0, reads current note state to return appropriate error (`NOTE_NOT_FOUND` vs `NOTE_ALREADY_CLAIMED`).
- **HTTP Endpoint (`app/routes/notes.py`)**:
  - Route: `POST /notes/claim`
  - Request Model: `ClaimNoteRequest(note_id: str, session_id: str, owner_id: Optional[str] = None)`
  - Response:
    - 200 OK: `{"status": "success", "note": {...}}`
    - 409 Conflict: `{"detail": "Note is already claimed by another session", "code": "NOTE_ALREADY_CLAIMED"}`
    - 404 Not Found: `{"detail": "Note not found", "code": "NOTE_NOT_FOUND"}`

### 2.4 Interface 4: Readiness Behavior and Probing
- **Readiness Validator (`app/adapters/runtime/config.py:check_runtime_readiness`)**:
  1. Checks HTTP reachability and latency of `/v1/contracts/spec`.
  2. Asserts contract version compatibility: `major == 1`, `minor >= 2`.
  3. Checks profile support: verifies required profile (e.g. `html-notes-canvas-v1`) against `/v1/contracts/spec` or `/v1/contracts/bundle`. If spec omits registered profiles, actively validates profile loading.
  4. Local manifest check: verifies that `html_notes.domain-tools.json` and `html_notes.profile.json` exist, parse as valid JSON, and contain valid canonical definitions.
  5. Returns structured `RuntimeReadinessResult`.
- **Production Wiring**:
  - `app/main.py:lifespan`: When `USE_SHARED_RUNTIME=True`, executes `check_runtime_readiness()`. Logs actionable startup diagnostics; if unavailable, flags runtime state.
  - `app/routes/health.py:health_agent`: If `USE_SHARED_RUNTIME=True`, queries `check_runtime_readiness()` and responds with HTTP `503` if runtime is unhealthy or contract mismatched.
  - `app/routes/message.py`: Before initiating turn, verifies runtime readiness. If down, fails closed with typed SSE error or handles according to degraded-mode policy.

---

## 3. Developer Ownership & Proposed Changes

### 3.1 Developer 3 Implementation Scope (Our Exclusive Domain)

#### [MODIFY] [`app/adapters/runtime/config.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/adapters/runtime/config.py)
- Update `check_runtime_readiness()`:
  - Add local manifest validation (verifying `app/tooling/manifests/html_notes.domain-tools.json` and `html_notes.profile.json` exist and load).
  - Add strict profile existence check (verifying profile against spec or bundle).
  - Prevent silent fall-through when profile list is missing from spec data.

#### [MODIFY] [`app/services/runtime_chat_adapter.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/services/runtime_chat_adapter.py)
- In `stream_chat_turn()`:
  - Extract and construct full authorization envelope from `event` and `data` (injecting `run_id`, `profile_id`, `tool_call_id`, and `required_scope` if nested receipt lacks them).
  - Pass complete typed dictionary to `execute_local_tool_cb`.
  - Handle runtime connection outages cleanly by yielding typed error frame followed by exactly one `done` frame.

#### [MODIFY] [`app/routes/message.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/message.py)
- In `execute_local_runtime_tool`:
  - Pass `expected_run_id=active_run_id`, `expected_tool_call_id=tool_call_id`, and `expected_profile_id=active_profile` into runtime context for `local_tool_executor.execute()`.
  - Ensure guaranteed single terminal SSE event (`{"type": "done"}`) regardless of success, refusal, cancellation, or runtime failure.

#### [MODIFY] [`app/routes/health.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/health.py)
- In `health_agent()`:
  - When `USE_SHARED_RUNTIME=True`, call `check_runtime_readiness()`.
  - Set status code 503 if runtime is unreachable or reports contract incompatibility.

#### [MODIFY] [`app/main.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/main.py)
- In `_lifespan()`:
  - Perform preflight `check_runtime_readiness()` at startup if `USE_SHARED_RUNTIME=True`.
  - Log clear diagnostics with contract version and readiness outcome.

#### [MODIFY] [`docker-compose.yml`](file:///home/lazycat/github/projects/sun/HTML-Notes/docker-compose.yml)
- Add environment variable forwarding:
  ```yaml
  - USE_SHARED_RUNTIME=${USE_SHARED_RUNTIME:-true}
  - RUNTIME_AUTH_SECRET=${RUNTIME_AUTH_SECRET:-${INTERNAL_EXECUTE_TOKEN}}
  ```

#### [MODIFY] [`deploy.sh`](file:///home/lazycat/github/projects/sun/HTML-Notes/deploy.sh)
- Ensure deploy-kit forwards `USE_SHARED_RUNTIME=true` during staging and production builds.

---

### 3.2 Developer 1 Scope (Authorization Contract)
*Managed in branch `security/local-tool-admission-hardening`*:
- **`lazy-agent-service/src/services/RunExecutionEngine.ts`**:
  - Issue receipts with all context fields: `receipt_id`, `run_id`, `tool_call_id`, `tool_name`, `app_id`, `session_id`, `profile_id`, `issued_at`, `expires_at`, and HMAC signature.
- **`HTML-Notes/app/adapters/runtime/models.py`**:
  - Replay cache: Key by `nonce` independently (`nonce:{nonce}`); set TTL to `expires_at`.
  - `verify_local_authorization`: Compare active `expected_run_id`, `expected_tool_call_id`, `expected_profile_id`; reject unsigned receipts; verify HMAC signature with shared token.
- **`HTML-Notes/app/tooling/local_executor.py`**:
  - Supply signature verifier and pass expected run/call context into `verify_local_authorization`.

---

### 3.3 Developer 2 Scope (Note Ownership & Atomic Claims)
*Managed in branch `readiness/profile-config-and-legacy-notes`*:
- **`HTML-Notes/app/database.py`**:
  - Implement atomic `claim_note()` with conditional `WHERE (session_id IS NULL OR owner_type = 'legacy_unclaimed')`.
- **`HTML-Notes/app/domain/notes/service.py`**:
  - Make `session_id` mandatory for `update_note()` and `link_notes()`.
  - Validate both source and target notes on `link_notes()`.
- **`HTML-Notes/app/routes/notes.py` & `app/routes/internal.py`**:
  - Route all note mutations through `notes_service`.
  - Expose `POST /notes/claim` endpoint.

---

## 4. Multi-Developer Integration & Handoff Workflow

Following the strict integration protocol:
1. **Dev 3 records interface decisions** (Section 2 of this plan).
2. **Dev 1 implements and tests authorization contracts** in their worktree, producing clean commits.
3. **Dev 2 implements and tests note ownership & atomic claims** in their worktree, producing clean commits.
4. **Dev 3 integrates Dev 1 commit** into `integrate/runtime-hardening-release`:
   - Recheck commit log and file boundary.
   - Run security suite.
5. **Dev 3 integrates Dev 2 commit** into `integrate/runtime-hardening-release`:
   - Recheck commit log and file boundary.
   - Run ownership and migration suites.
6. **Dev 3 completes adapter, routing, readiness wiring, and docker-compose updates**.
7. **Dev 3 runs full adversarial integration test suite** (Section 5).

---

## 5. Adversarial Integration Test Matrix

The test suite in [`tests/test_runtime_hardening_integration.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_runtime_hardening_integration.py) will be upgraded from unit stubs to full HTTP route tests using FastAPI `TestClient` across all 8 adversarial conditions:

| Scenario | Adversarial Condition | Expected Behavior & Assertion |
|---|---|---|
| **ADV-1** | Forged / unsigned receipt | Local tool rejected; `403` / `UNAUTHORIZED` error emitted; zero database/canvas mutation. |
| **ADV-2** | Mismatched run ID / tool call ID / profile | Receipt rejected with `TOOL_MISMATCH` or `CONTEXT_MISMATCH`; execution aborted before domain layer. |
| **ADV-3** | Repeated nonce with different tool call ID | First execution succeeds; second execution with same nonce fails with `REPLAYED_RECEIPT`. |
| **ADV-4** | Concurrent claims race | Two simultaneous requests attempt to claim the same note; exactly one returns `200 OK`, the second returns `409 Conflict`. |
| **ADV-5** | Sessionless note update via HTTP route | `POST /notes/update` without `session_id` rejected with `400 Bad Request` or `401/403`. |
| **ADV-6** | Cross-session note update via `/internal/execute` | Tool call attempting to update another session's note rejected with `NOTE_SESSION_MISMATCH`. |
| **ADV-7** | Unavailable runtime / timeout | Route preflight catches unreachable runtime, yields typed error status, and exits cleanly without hang. |
| **ADV-8** | Terminal SSE event invariant | Under all conditions (success, error, cancellation, rejection), stream yields exactly one `{"type": "done"}` frame. |

---

## 6. Verification & Release Gates

### Gate 1: Automated Unit & Adversarial Tests
```bash
/home/lazycat/github/projects/sun/.venv/bin/pytest \
  tests/test_local_tool_admission_security.py \
  tests/test_notes_session_ownership.py \
  tests/test_legacy_note_migration.py \
  tests/test_runtime_readiness.py \
  tests/test_runtime_chat_adapter.py \
  tests/test_runtime_hardening_integration.py \
  tests/test_domain_manifests.py \
  tests/test_tool_policy_and_effects.py \
  tests/test_widget_catalog_parity.py -v
```
**Pass Criterion**: 100% pass rate across all suites.

### Gate 2: Pre-Commit Zero Secret Leakage Scan
```bash
git diff origin/main...HEAD -i -G"(password|secret|token|api_key|credential)"
```
**Pass Criterion**: Zero hardcoded secrets, fake passwords, or high-entropy tokens staged.

### Gate 3: Synology NAS Staging & Container Validation
1. Deploy via deploy-kit: `npm run deploy -- --only=html-notes --skip-pull`
2. Verify container state: `docker ps --filter name=html-notes` -> Healthy.
3. Verify forwarded environment: `docker exec html-notes env | grep USE_SHARED_RUNTIME` -> `USE_SHARED_RUNTIME=true`.
4. Verify readiness probe: `curl -f http://10.0.0.16:8035/health/agent` -> `200 OK`, `{"status": "ok", "runtime_ready": true}`.
5. Verify live mutation: Send controlled session message, verify persisted canvas change in `/app/data/notes.db`.

---

## 7. Rollback & Fallback Plan

If post-deploy validation detects runtime instability:
1. **Immediate Feature Flag Toggle**:
   - Set `USE_SHARED_RUNTIME=false` in NAS environment.
   - Restart container: `docker-compose restart html-notes`.
   - HTML-Notes immediately routes through proven legacy Prism path without service interruption.
2. **Container Rollback**:
   - Re-deploy previous known-good image tag `html-notes:bb2f1cc`.
