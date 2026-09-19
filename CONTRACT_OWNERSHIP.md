# Contract Ownership & Source of Truth Specification

**Version**: 1.0.0  
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
1. **Zero Cross-Repo Filesystem Traversals**: No test, import, or deployment step may reference `../lazy-agent-service` or sibling worktrees.
2. **Packaged Manifest Authority**: HTML-Notes reads its application profile, domain tool schemas, and widget catalog from in-repo manifests in `app/tooling/manifests/`.
3. **External Protocol Verification**: Contract parity tests validate against packaged schema definitions or shared artifacts.

---

## 3. Schema Coexistence & Deprecation Roadmap

### Phase 1: Dual Coexistence (Current State)
- `app/tooling/manifests/html_notes.domain-tools.json` provides authoritative metadata, effect classifications, and schemas for all domain tools.
- `app/schemas/tool-contract-v1.json` remains present temporarily to prevent breaking Dev 2's ongoing message route wiring.

### Phase 2: Route Cutover & Feature Flag Promotion (Dev 2 Completion)
- Dev 2 enables `USE_SHARED_RUNTIME=true` in `app/routes/message.py`, connecting `RuntimeChatAdapter` to `lazycat-sdk.RuntimeClient` and `LocalToolExecutor`.
- Message route switches tool execution to `local_tool_executor.execute()`.

### Phase 3: Retirement Gate (Dev 3 Cleanup)
- Once Dev 2 validates 100% green parity on the live route:
  1. Remove `app/schemas/tool-contract-v1.json`.
  2. Update `tests/test_tool_schema_enum.py` to validate against `html_notes.widget-catalog.json` and `html_notes.domain-tools.json`.
  3. Retire legacy aliases in `LocalToolExecutor` after backward-compatibility verification.

---

## 4. Historical Document Supersession

The following historical documents and code comments are explicitly superseded:
- [`app/tools_schema.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/tools_schema.py): Superseded by `app/tooling/manifests/html_notes.domain-tools.json` and this document.
- Prior notes referencing generating flat schemas in sibling checkouts: Superseded by packaged in-repo manifests.
