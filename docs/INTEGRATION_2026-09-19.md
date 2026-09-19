# Integration & Release Report: Runtime Hardening (2026-09-19)

> Historical report follows. The release follow-up and verified NAS evidence at the end supersede its earlier security and deployment claims.

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
  # Set USE_SHARED_RUNTIME=false in the staged deployment environment,
  # then recreate the affected container through deploy-kit.
  # A restart alone does not apply changed environment variables.
  ```


## Release follow-up: verified gaps and corrections

The earlier pass counts above describe the prior test suite, not a successful
end-to-end runtime release. Follow-up inspection found a test-signature bypass,
incompatible fallback keys, timestamp serialization mismatch, missing startup
and request preflight, and an unconnected runtime admission helper.

The follow-up removes signature shortcuts and default signing keys, binds the
producer's exact serialized arguments into HMAC receipts, preserves wire expiry
precision, uses UUID nonces, and prevents a canonical tool override from changing
the signed tool. Both services prefer RUNTIME_AUTH_SECRET, falling back only to
the configured INTERNAL_EXECUTE_TOKEN. Compose forwards the dedicated setting.
The existing old HMAC envelope remains verifiable during the coordinated rollout.

Startup records readiness and chat preflight fails closed before mutation. Health
reports runtime_ready explicitly. The canonical profile defaults agree, and the
server canvas profile selects the deployed vllm-2 / GLM-5.3-Flash-EXL3 pair.
The runtime now supplies an event emitter, profile-filtered application schemas,
and an admission callback to the agent harness. Local calls are dispatched to
the app with a receipt; the runtime does not claim their execution succeeded.
Capabilities requiring confirmation fail closed in the canonical execution path.

The HTTP tests use isolated SQLite databases and mocked external dependencies;
valid receipts persist a note, while forged, unsigned, mismatched, changed-argument
and repeated receipts are rejected. TestClient requires execution outside this
workspace's restricted sandbox (it hangs at the thread bridge inside it).
The shared-runtime SSE wrapper emits one terminal event for connected streams,
including a finalization/persistence exception. Disconnected clients cannot be
guaranteed delivery of a terminal frame.

Validation and NAS release results are recorded below after integration.
Replay state is process-local; this release assumes the existing single-worker
container. It does not provide replay durability across a restart or replicas.


## Final verified release evidence

- HTML-Notes production code: `bd52cf6` (includes runtime changes at `6225cc3`).
- lazy-agent-service production code: `68d8803`.
- Both implementation batches were integrated into `main` and pushed.
- Integrated Python gate: **175 passed** across the 12 runtime/security/ownership
  suites, including startup preflight, internal HTTP ownership, and sessionless
  creation. The claim HTTP test now synchronizes both database claim attempts
  with a barrier; one request succeeds and the other returns 409. That replacement
  was also validated independently.
- Runtime TypeScript check: passed. Contract gate: **43 passed**. Deploy-kit ran
  the broader runtime suite: **759 passed, 40 files**.
- Secret review: newly added signing credentials are generated in memory for
  tests; no static credentials were added. Git's POSIX regex rejects `(?i)`, so
  the staged diff scan used equivalent case-insensitive character classes.
- NAS deployment used `npm run deploy -- --only=lazy-tool-service,html-notes
  --skip-pull --skip-deps`, followed by a targeted HTML-Notes redeploy for the
  final creation guard. Image transfer, restart and HTTP availability verified.
- A stale workspace `html-notes` symlink pointed to the obsolete
  `wt-dev3-domain-cleanup` worktree. The first deployment briefly installed that
  old HTML-Notes image; the alias was corrected to the primary `HTML-Notes`
  checkout and the targeted deployment rerun. Final container revision checks
  confirmed the production commits listed above.
- `/health/agent`: HTTP 200, `runtime_ready=true`, `use_shared_runtime=true`,
  profile `html-notes-canvas-v1`, contract `1.2.0`.
- Controlled session `release-check-d8de19477d6c`: one canonical
  `html_notes.canvas.upsert_widget` call, one component, exactly one terminal
  `done`, no errors. Widget `release-probe-be6ded5ce0` was found in the assistant
  row in `/app/data/notes.db`, restored through session history, and still present
  after the final container restart. The upstream verification run completed.
- Final live negative check: sessionless `POST /notes/create` returns HTTP 401.
- Deploy-kit reported unrelated edge DNS reconciliation conflicts; both targeted
  service deployments succeeded and the Caddyfile was unchanged. No DNS edits
  were made to resolve those conflicts in this task.

Remaining architectural limit: local execution has no acknowledgement channel
back to the runtime yet. The runtime reports local admission, not successful
application execution; success is established here from application SSE and
persisted SQLite state. Replay durability remains limited as described above.
