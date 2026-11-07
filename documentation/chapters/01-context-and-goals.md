---
part: Runtime and architecture
type: spec
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:app/main.py, html-notes:app/routes/message.py, html-notes:app/config.py, html-notes:CONTRACT_OWNERSHIP.md
---

# Context and goals

## What the system is for

HTML-Notes is a local-first knowledge journal with a chat-driven canvas. The user
asks a question in the journal; a fast lane classifies the ask, an LLM router picks
a path, the shared lazy-agent-service runtime answers, and the server renders one
or more widgets (news card, stock card, weather, checklist, clock, KPI, table,
timeline, music player, YouTube CRT) onto the canvas. The canvas and its notes are
stored in the local SQLite database, and a saved canvas re-renders from stored
state rather than from the chat transcript.

The product is deliberately narrow: it renders a constrained set of widgets from
server-side templates, it keeps the notes and chat history on the operator's own
machine, and it does not pretend to be a general web app builder.

## Out of scope

| Not in scope | Why |
|---|---|
| A free-form HTML/JS generator | Every widget comes from `app/widgets/factory.py`; arbitrary agent HTML is not rendered. |
| The lazy-agent-service runtime itself | It is a separate service; HTML-Notes integrates through its HTTP API. |
| The music-player app itself | The canvas mini player hands playback to music-player; its catalog and playback live there. |
| Cloud model hosting | The router and agent calls go to the local vLLM boxes through the shared runtime. |
| A multi-user SaaS deployment | The app is local-first, single-operator and LAN-scoped. |
| Long-term analytics | The canvas is a journal, not a BI product. |

## Quality goals that govern design

| Goal | What it means in practice | Mechanism |
|---|---|---|
| **A widget is rendered by code, not by the model** | The model chooses content and a widget type; the HTML comes from a server template. | `app/widgets/factory.py` and `app/presentation/widgets/catalog.py`; tests assert template contracts. |
| **One ask produces one canvas** | A chat turn either updates the existing canvas or adds a widget to it; it does not scatter widgets across sessions. | `app/canvas_manager.py` and the canvas domain service own placement and serialization. |
| **A slow or missing model must not silence the app** | The router has a short budget and a deterministic fallback; a late answer refines the widget in place. | `MUSIC_ROUTER_BUDGET_S`, `MUSIC_ROUTER_REFINE_S`, and the fallback player in `app/routes/message.py`. |
| **Grounding beats fluency** | A router subject must share a stem with the user's message before it is trusted. | `music_query_grounded` and the routing eval set in `tests/routing_eval.json`. |
| **The client never needs a CDN** | Tailwind, icons and widget assets are built or served locally. | Compiled `app/static/tailwind.css`, local assets, and `tests/test_tailwind_build.py`. |
| **A widget can be repaired without losing its state** | The canvas self-heal rebuilds from the server's strict config, never from a default genre or timezone. | `data-hn-config` stamped by the factory and `musicWidgetNeedsRebuild` / `recoverMusicWidgetConfig` in `app/static/index.js`. |

> A goal is only real if a check enforces it. Section 09 names the check behind each
> claim; this chapter states what the product is trying to guarantee.

## Contract ownership

`CONTRACT_OWNERSHIP.md` records which side owns each boundary: the server owns the
widget factory, the canvas owns placement, the client owns only transient UI state
(drag, hover, playback carry), and lazy-agent-service owns the agent runtime and
tool catalog. A change that crosses a boundary updates the owner's contract and its
tests, not just the caller.

## Users and surfaces

- The **journal UI** at `app/static/index.html`: chat, canvas, notes and the widget
  manager.
- The **HTTP API** in `app/routes/`: sessions, messages, notes, watches, health and
  internal quality endpoints.
- The **documentation site**: this set of chapters rendered by
  `documentation/build_docs.py`.
