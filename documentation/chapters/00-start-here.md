---
part: Start Here
type: reference
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:documentation/tickets.jsonl, html-notes:documentation/build_docs.py
---

# Start here: how this documentation works

This is the technical design document (TDD) for **HTML-Notes**, the local-first AI
knowledge journal: a chat-driven canvas where the router picks a path, the shared
lazy-agent-service runtime answers, and server-rendered widgets land on a canvas
that is stored in the local SQLite database. It is one document with a fixed table
of contents; nothing here is dated and no chapter is written "for today's batch".

## The 13 sections and what each answers

| Section | Question it answers | Read it when |
|---|---|---|
| 00 Start here | How this document works, and how to record work | First, and before adding anything |
| 01 Context and goals | What the product is for, what it is not, what it must not do | You need the boundaries |
| 02 Architecture and data | Which processes exist, what each owns, where data lands | You are tracing a value across services |
| 03 Execution pipeline | The ordered stages of one chat turn, from message to stored canvas | A turn routed, rendered or persisted unexpectedly |
| 04 Modules and services | What each module owns and how the layers depend on each other | You are changing or looking for code |
| 05 Domain features | News, research, music, YouTube and the widget behaviours behind them | A feature answer looks wrong |
| 06 State and storage | Every table, cache and client-side store, with its writer and retention | You need to know where a fact lives |
| 07 UI and widgets | The canvas, widget factory, HUD theme and the device-style cards | The screen disagrees with the data |
| 08 Operations and runbooks | How to build, start, deploy, check and recover the app | You are deploying or something is stuck |
| 09 Verification and benchmarks | What is proven, by which command, and what is still unproven | You need to know whether a claim is tested |
| 10 Decisions | The settled architectural choices, numbered, with their consequences | You are about to change something settled |
| 11 Tickets | Every open and closed problem, generated from the ledger | You are picking up work |
| 12 Security and dependencies | Credentials, tool policy, and the dependency baselines | You touch a boundary or a dependency |

## The rules that keep this from rotting

1. **Work goes in the ledger, not in a new chapter.** A bug, a gap or a "we should
   later" becomes a ticket in `documentation/tickets.jsonl`. The chapter count is
   capped at 13 by the documentation contract; date-stamped filenames are rejected.
2. **A section is rewritten in place.** When the product changes, edit the section
   that owns it, not a new record of the change. Git history is the change log; the
   document is the current truth.
3. **A closed ticket needs proof.** `status: closed` is rejected without
   `verified_by` naming the command that proved it and the date it ran.
4. **Closed tickets stay in the file.** They are the searchable history: "did we
   ever look at this?" is answered months later without reading old chapters.
5. **A section declares what it covers.** `covers:` lists the code paths a section
   describes, with a repo prefix. A section whose covered code moved is stale until
   it is re-read.
6. **Old chapters are archived, never deleted.** The pre-2026-11 chapter files are
   kept in git history; their content is folded into the sections above.

## Adding a ticket

Append one JSON object per line to `documentation/tickets.jsonl`, keeping ids
ascending. Every ticket names its owner repo, severity, area, the node it belongs
to, the code paths it covers and the review date.

```json
{"id":"TCK-1001","title":"...","status":"open","severity":"P2","area":"routing","owner":"html-notes","opened":"2026-11-06","updated":"2026-11-06","verified_by":null,"linked_commits":["8d3660c"],"linked_cycles":[],"closed_by":null,"covers":["html-notes:app/routes/message.py"],"review_by":"2026-12-06","source_ref":"ch.03 §Music fast lane"}
```

## Where the code lives

- `app/main.py` — the FastAPI application and router wiring.
- `app/routes/` — HTTP and SSE surfaces (`message.py`, `notes.py`, `api.py`,
  `health.py`, `internal.py`, `quality.py`).
- `app/services/` — news, search, music/YouTube, portal, finance, sports, location,
  watches, profile, research coordination.
- `app/agents/` — orchestrator, intake, librarian, linker, writer, auditor.
- `app/adapters/runtime/` — the lazy-agent-service runtime adapter.
- `app/domain/` — notes, canvas, apps and watches domain services.
- `app/widgets/factory.py` — the server-side widget renderer.
- `app/static/` — the canvas client, HUD theme, compiled Tailwind CSS and device cards.
- `tests/` and `scripts/` — the checks and live probes named in section 09.
