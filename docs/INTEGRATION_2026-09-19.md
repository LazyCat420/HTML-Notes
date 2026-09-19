# Integration & Release Report: Runtime Hardening (2026-09-19)

## Executive Summary
This release implements the **two-implementation-developer + one-integrator/release-developer** model to harden runtime tool admission, configuration readiness/preflight, receipt authentication/context binding, and legacy-note session ownership on `HTML-Notes` and `lazy-agent-service`.

- **Developer 1 (Authorization Contract & Security)**:
  - Enforced fail-closed HMAC-SHA256 authenticated receipts binding `run_id`, `tool_call_id`, `canonical_tool_id`, `profile_id`, `app_id`, `session_id`, `issued_at`, `expires_at`, and `nonce`.
  - Replay cache: Keyed by `nonce` independently with TTL expiration pruning.
  - Implemented runtime receipt issuance and signing in `lazy-agent-service` (`RunExecutionEngine.ts`) and profile registration in `/v1/contracts/spec`.
  - Upgraded `HTML-Notes` `LocalToolExecutor` with cryptographic verification and context cross-checking.

- **Developer 2 (Note Ownership & Atomic Claims)**:
  - Atomic note claiming via SQLite conditional CAS query `WHERE id = ? AND (session_id IS NULL OR owner_type = 'legacy_unclaimed' OR session_id = ?)` with version logging to `note_versions`.
  - Enforced mandatory session ownership for every note mutation route (`update_note`, `claim_note`, `link_notes`).
  - Enforced two-sided link ownership validation (both source and target notes must belong to session).
  - Exposed explicit `POST /notes/claim` endpoint and routed all internal tools through `NotesDomainService`.

- **Developer 3 (Integrator & Release Authority)**:
  - Enforced readiness checking with local manifest validation (`validate_all()`) and contract profile verification.
  - Wired `/health/agent` to report 503 on unready runtime when `USE_SHARED_RUNTIME=true`.
  - Forwarded `USE_SHARED_RUNTIME=${USE_SHARED_RUNTIME:-true}` and runtime configuration in `docker-compose.yml`.
  - Added full adversarial integration test suite covering forged/missing receipts, wrong run/call/profile, repeated nonce, concurrent claims, sessionless/foreign updates, unavailable runtime, and exactly one terminal SSE event.

---

## Phase F Adversarial Integration Tests (16/16 Passed)
Located in `tests/test_runtime_hardening_integration.py`:
1. `test_runtime_admitted_local_write_with_valid_scope_and_valid_receipt_executes` — **PASSED**
2. `test_runtime_admitted_local_write_with_missing_scope_does_not_execute` — **PASSED**
3. `test_runtime_admitted_local_write_with_invalid_receipt_does_not_execute` — **PASSED**
4. `test_runtime_admitted_local_write_with_expired_receipt_does_not_execute` — **PASSED**
5. `test_runtime_admitted_local_write_with_foreign_session_does_not_execute` — **PASSED**
6. `test_runtime_global_tool_never_calls_local_executor` — **PASSED**
7. `test_runtime_outage_creates_no_local_side_effect` — **PASSED**
8. `test_runtime_denial_creates_no_local_side_effect` — **PASSED**
9. `test_shared_runtime_request_emits_exactly_one_done` — **PASSED**
10. `test_legacy_note_cannot_be_edited_from_foreign_session` — **PASSED**
11. `test_adversarial_forged_or_missing_receipt_via_http` — **PASSED**
12. `test_adversarial_context_mismatch_run_call_profile` — **PASSED**
13. `test_adversarial_repeated_nonce_rejected_across_calls` — **PASSED**
14. `test_adversarial_concurrent_claim_protection_via_http` — **PASSED**
15. `test_adversarial_readiness_probe_fails_on_unreachable_runtime` — **PASSED**
16. `test_adversarial_exactly_one_terminal_sse_event_all_cases` — **PASSED**

---

## Combined Test Suite Verification
Complete combined test command:
```bash
/home/lazycat/github/projects/sun/.venv/bin/pytest \
  tests/test_domain_manifests.py \
  tests/test_widget_catalog_parity.py \
  tests/test_tool_policy_and_effects.py \
  tests/test_local_executor.py \
  tests/test_local_tool_admission_security.py \
  tests/test_runtime_readiness.py \
  tests/test_runtime_chat_adapter.py \
  tests/test_legacy_note_migration.py \
  tests/test_notes_session_ownership.py \
  tests/test_runtime_hardening_integration.py -v
```
**Total Pass Count**: 124 passed (100% pass rate).

---

## Security and Secret Scan Gate
Pre-commit git secret scan command:
```bash
git diff origin/main...HEAD -i -G"password\|secret\|token\|api_key\|credential"
```
**Result**: 0 violations detected. Dynamic credentials and standard cryptographic generation utilized throughout.

---

## Runtime Contract & Feature Flags
- **Target Contract Version**: `1.2.0`
- **Feature Flag**: `USE_SHARED_RUNTIME=true` (staging/production)
- **Fallback Guarantee**: Setting `USE_SHARED_RUNTIME=false` safely routes requests through the legacy pipeline without breaking changes.
- **Rollback Procedure**:
  ```bash
  export USE_SHARED_RUNTIME=false
  # or revert commit on main
  git revert HEAD -m 1
  ```

