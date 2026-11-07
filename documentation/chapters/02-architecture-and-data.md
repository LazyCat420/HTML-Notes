---
part: Runtime and architecture
type: reference
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:app/main.py, html-notes:app/routes/api.py, html-notes:app/routes/notes.py, html-notes:app/database.py, html-notes:app/adapters/runtime/config.py, html-notes:docker-compose.yml
---

# Architecture and data

## Processes (C4 level 2)

| Process | Where | Owns | Talks to |
|---|---|---|---|
| **html-notes** | FastAPI, port `8035` (`npm run dev` / `npm start`) | The journal UI, chat sessions, canvas state, widget rendering, the SQLite store. | lazy-agent-service over HTTP (`LAZY_AGENT_URL`, default `:5591`) for agent calls and health; music-player over HTTP (`:8002`) for playback history and favorites; the local vLLM boxes only through the shared runtime. |
| **lazy-agent-service** | LAN service, port `5591` | Agent harness, tool catalog, model routing, embeddings. | DGX Spark and Jetson vLLM endpoints; the shared web-search and scraper services. |
| **music-player** | LAN service, ports `8002` (API), `8007` (web) | Track catalog, playback events, favorites, YouTube mixes. | Its own SQLite catalog and YouTube. |
| **MongoDB / NAS** | Not required by HTML-Notes | Other workspace services; HTML-Notes keeps its own SQLite file. | n/a |

HTML-Notes is the only process that writes `notes.db`. The shared runtime never
writes the journal; it returns answers and widget payloads that HTML-Notes renders
and stores.

## Layering

```
app/static (canvas client)
        │  HTTP + SSE
app/routes (message, notes, api, health, internal, quality)
        │
app/agents + app/adapters/runtime  ──►  lazy-agent-service
        │
app/services (news, search, youtube, portal, research, …)
        │
app/domain (notes, canvas, apps, watches) + app/database.py
        │
notes.db
```

The dependency direction is downward: routes call services and domain code, domain
code calls the database, and nothing in `app/domain/` calls a route. The widget
factory is a presentation boundary: services produce data, the factory produces
HTML, the canvas manager stores the rendered widget.

## Data map

`app/database.py` creates the following tables in `notes.db`:

| Table | Writer | Purpose |
|---|---|---|
| `notes` | `app/domain/notes/service.py`, `app/routes/notes.py` | The journal notes and their current body. |
| `note_versions` | `app/domain/notes/service.py` | Append-only versions of a note body. |
| `chat_sessions` | `app/routes/message.py` | One row per chat session. |
| `chat_messages` | `app/routes/message.py` | User, assistant and tool messages, used for history and the routing eval mining. |
| `watches` | `app/services/watches.py`, `app/domain/watches/service.py` | User-defined watches and their event stream. |
| `agent_memory` | `app/agents/` | Per-session agent memory. |
| `research_runs` | `app/services/research/coordinator.py` | One research run with its intent and budget. |
| `research_tasks` | `app/services/research/coordinator.py` | The workers dispatched for a run. |
| `research_evidence` | `app/services/research/ledger.py` | Collected evidence with its source and confidence. |
| `research_answer_versions` | `app/services/research/synthesizer.py` | Append-only synthesized answers. |

Canvas state is serialized by `app/canvas_manager.py` and stored with the note it
belongs to; the client keeps only transient UI state (drag position, open drawer,
audio carry) in memory and `localStorage`.

## External contracts

| Contract | Direction | Shape |
|---|---|---|
| `/session/message` | client → server | A user message plus session id; the server streams SSE events and returns the final message/canvas. |
| `/session/{id}/history` | client → server | The persisted message list for a session. |
| `LAZY_AGENT_URL` `/agent`, `/models`, `/health` | server → runtime | Agent request and health probes; the same `x-project` / `x-username` attribution headers are sent on every call. |
| music-player `/api/music/history`, `/api/favorites` | server → music-player | Play events in the `PlayEvent` shape and song favorites keyed by `youtube://<id>`. |
| `/api/youtube/candidates` | server → YouTube helper | Additional embeddable video candidates for the CRT widget. |

## Failure boundaries

- The runtime being down degrades to the deterministic lanes (music fallback,
  news classifier, local widget data) rather than a blank canvas.
- A failed widget render returns an error card; the chat message is still stored.
- A missing `notes.db` is created on boot; a corrupt canvas row is skipped, not
  allowed to break the whole canvas.
