---
part: State and storage
type: reference
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:app/database.py, html-notes:app/canvas_manager.py, html-notes:app/domain/notes/service.py, html-notes:app/domain/canvas/service.py, html-notes:app/services/research/ledger.py, html-notes:app/static/index.js
---

# State and storage

## Server store

HTML-Notes keeps one SQLite database (`notes.db`), created and migrated by
`app/database.py`. It is the only durable store; the canvas client holds transient
state only.

| Table | Join key | Written by | Retention |
|---|---|---|---|
| `notes` | `id` | `app/domain/notes/service.py`, `app/routes/notes.py` | permanent; the current body and metadata. |
| `note_versions` | `note_id`, `version` | `app/domain/notes/service.py` | permanent; append-only history. |
| `chat_sessions` | `id` | `app/routes/message.py` | permanent. |
| `chat_messages` | `session_id`, `id` | `app/routes/message.py` | permanent; the routing eval mines this table. |
| `watches` | `id` | `app/services/watches.py` | permanent. |
| `agent_memory` | `session_id` | `app/agents/` | per-session; pruned by the agent layer. |
| `research_runs` | `id` | `app/services/research/coordinator.py` | permanent. |
| `research_tasks` | `run_id`, `id` | `app/services/research/coordinator.py` | permanent. |
| `research_evidence` | `run_id`, `evidence_id` | `app/services/research/ledger.py` | permanent. |
| `research_answer_versions` | `run_id`, `version` | `app/services/research/synthesizer.py` | permanent; append-only. |

Writes are transactional per request where a request touches more than one table.
The chat transcript and the canvas are written in the same turn: a message without
its canvas is a bug, and a canvas without its message is a bug.

## Canvas state

`app/canvas_manager.py` owns the canvas model:

- A canvas is a list of widgets with their type, position, size and serialized
  state.
- `app/domain/canvas/service.py` owns placement, z-order and legacy migration
  (`legacy_custom_widgets.py`).
- A widget's serialized state is the same strict JSON the factory stamps in
  `data-hn-config`; a rebuild reads that state, never a guessed default.
- A corrupt widget row is skipped and logged; it does not abort the canvas.

Only `{cols}` is saved for a device-style widget's size. A stationary click (≤ 3 px)
is not a resize; double-clicking the device drops the saved size.

## Client-side state

| Store | Key | What it holds | Lifetime |
|---|---|---|---|
| `localStorage` | `hn_music_song_prefs` | Per-song thumbs, keyed by track id. | Until cleared. |
| `localStorage` | `hn_music_widget_prefs` | Volume, power and last channel of the music/CRT widgets. | Until cleared. |
| `window.__hnMusicCarry` | n/a | The playing audio element parked during a widget swap. | Until a successor adopts it or 2 s pass. |
| Alpine component state | per widget | Drag, hover, drawer and playback UI state. | While the widget is mounted. |
| SSE buffer | per session | The in-flight assistant message and widget frames. | Until the turn ends. |

The client never writes the canvas directly. It sends the widget's action to the
server, the server updates the stored canvas, and the next render reads it back.

## Research ledger

The research ledger is the audit trail for an answer:

1. `research_runs` records the intent, the budget and the outcome.
2. `research_tasks` records each worker dispatch and its status.
3. `research_evidence` records the source, the excerpt and the confidence.
4. `research_answer_versions` records each synthesized answer.

An answer that cannot cite its evidence is not written; the synthesizer returns a
partial answer with the missing evidence named.

## Backup and retention

- `notes.db` is a single file; a backup copies the file while the app is stopped or
  uses SQLite's backup API.
- There is no TTL on notes or chat history. The database grows with use; the
  routing eval mines it read-only.
- `research_*` rows are kept for audit; a manual prune is allowed only after a
  backup and a row count.
