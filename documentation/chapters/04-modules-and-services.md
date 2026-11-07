---
part: Modules and services
type: reference
status: verified
updated: 2026-11-07
last-verified: 2026-11-07
review-by: 2026-12-06
covers: html-notes:app/routes/message.py, html-notes:app/services/news_pipeline.py, html-notes:app/services/research/coordinator.py, html-notes:app/tooling/policy.py, html-notes:app/domain/canvas/service.py, html-notes:app/agents/orchestrator.py, html-notes:app/portal_registry.json
---

# Modules and services

## Module map

| Layer | Module | Owns |
|---|---|---|
| Entry | `app/main.py` | FastAPI app, router registration, lifespan, static mount. |
| Routes | `app/routes/message.py` | `/session/message`, `/session/transcribe`, `/session/{id}/history`; the fast lanes and SSE. |
| Routes | `app/routes/notes.py` | Note CRUD and versions. |
| Routes | `app/routes/api.py` | Widget and app APIs used by the canvas. |
| Routes | `app/routes/health.py` | `/health/app` and dependency status. |
| Routes | `app/routes/internal.py` | Internal quality and maintenance endpoints. |
| Routes | `app/routes/quality.py` | Content-quality probes. |
| Agents | `app/agents/orchestrator.py` | Agent turn orchestration and tool dispatch. |
| Agents | `app/agents/intake.py` | Ask intake and lane hints. |
| Agents | `app/agents/librarian.py`, `linker.py`, `writer.py`, `auditor.py` | Note retrieval, linking, writing and auditing. |
| Adapters | `app/adapters/runtime/` | lazy-agent-service client, model config and request models. |
| Services | `app/services/news_pipeline.py` | News collection, relevance gating and summaries. |
| Services | `app/services/search.py` | Search and Google News link resolution. |
| Services | `app/services/youtube_service.py`, `youtube_helpers.py` | YouTube search, candidate selection, live networks, CRT data. |
| Services | `app/services/research/` | Coordinator, budget, cache, ledger, intent, synthesizer and workers. |
| Services | `app/services/portal.py`, `finance.py`, `sports.py`, `location.py` | App and data providers for the widget catalog. |
| Services | `app/services/watches.py`, `profile_service.py`, `toolsvc.py` | Watches, profiles and the tool service adapter. |
| Domain | `app/domain/notes/service.py` | Note ownership and versions. |
| Domain | `app/domain/canvas/service.py`, `legacy_custom_widgets.py` | Canvas state and legacy widget migration. |
| Domain | `app/domain/apps/service.py` | App Hub registry and ownership. |
| Domain | `app/domain/watches/service.py` | Watch definitions and events. |
| Tooling | `app/tooling/policy.py`, `local_executor.py`, `execution_journal.py` | Tool policy, local execution and the execution journal. |
| Tooling | `app/tooling/html_notes_manifest.py`, `manifests/` | Tool manifests exposed to the runtime. |
| Presentation | `app/presentation/widgets/catalog.py` | Widget type catalog and metadata. |
| Presentation | `app/presentation/sse/` | SSE event encoding. |
| Widgets | `app/widgets/factory.py` | The server-side HTML renderer for every widget. |
| Client | `app/static/index.js`, `app/static/js/widgets.js` | Canvas mount, resize, self-heal, widget behaviour. |

## Dependency rules

1. **Routes never contain widget HTML.** A route resolves data and calls the
   factory; the factory owns markup.
2. **Services never write the database directly.** They return data to the route or
   domain service that persists it.
3. **Domain code never calls the runtime.** Only routes and the agent layer call
   `app/adapters/runtime/`.
4. **The client never invents server state.** It may keep transient UI state, but
   every rebuild reads `data-hn-config` or the stored canvas.

## App Hub and portal registry

`app/portal_registry.json` lists the apps the canvas can open. `app/domain/apps/service.py`
owns the registry: an app is addressable by id, has a URL and a health contract, and
the App Hub widget renders the registry, not a hardcoded list. The `doc-client` entry
launches the unified documentation hub at `http://10.0.0.16:8888/` (which redirects to
`/documentation` on `trading-client`), replacing the retired standalone `:8910`
`doc-client` server while `apps/doc-client/static/` remains the source directory for the
3D walkman and CRT cards. `tests/test_app_hub.py` checks the registry URL expectations;
its three failures after the dependency bump were URL-expectation mismatches, not
dependency breakage.

## Tool policy

`app/tooling/policy.py` decides which tools a turn may call and with what limits;
`local_executor.py` runs local tools and `execution_journal.py` records each call.
`app/tooling/manifests/` and `html_notes_manifest.py` expose the same catalog to the
runtime, so a tool is not available merely because a model asked for it.

## Research service

The research stack is the only multi-worker service in the app:

- `intent.py` classifies the ask and chooses worker types.
- `budget.py` caps calls and wall time.
- `cache.py` deduplicates repeated evidence lookups.
- `coordinator.py` dispatches `workers/*.py` and collects results.
- `ledger.py` writes `research_evidence`.
- `synthesizer.py` writes `research_answer_versions`.

A worker failure is recorded as missing evidence, not as a failed run; the
synthesizer states what it could not verify.
