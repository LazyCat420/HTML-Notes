---
part: Start Here
status: verified
type: spec
updated: 2026-10-23
review-by: 2026-11-23
---

# How this documentation is organised

HTML-Notes is a chat-driven canvas: the user asks, the router picks a path, the agent (shared `lazy-agent-service` runtime) returns widgets. The documents are grouped by **what we built**, not by date. Every page declares one area as `part:` in its front matter, and the Doc-Client Web Hub groups by it.

## The areas

| Area | What lives here | Folder |
|---|---|---|
| Runtime and architecture | boundary model, contract ownership, note ownership, runtime migration, the Prism exit | [docs/architecture/](../../docs/architecture/ARCHITECTURE.md) and [CONTRACT_OWNERSHIP](../../CONTRACT_OWNERSHIP.md) |
| Canvas and widgets | widget factory, App Hub, control plane, canvas feed, chart legend, widget-wave handoffs | [docs/canvas/](../../docs/canvas/APP_HUB.md) |
| Routing and agent | tier gates, classifiers, follow-up targeting, router reliability, the agent roadmap | [docs/routing/](../../docs/routing/AGENT_ROADMAP.md), plus [DEBUG_WAVE_PLAN](../../DEBUG_WAVE_PLAN.md) and [FOLLOWUP_TARGETING_PLAN](../../FOLLOWUP_TARGETING_PLAN.md) |
| News and research | the news pipeline, web search, research reliability, market-news grounding, benchmarks | [docs/news-research/](../../docs/news-research/NEWS_PIPELINE_2026-09-17.md), plus [WEB_SEARCH](../../docs/WEB_SEARCH.md) and [the web-data benchmark audit](../../docs/audit/AUDIT_2026-09-21_WEB_DATA_COLLECTION_BENCHMARK.md) |
| Music player | canvas mini player, hand-off to the music-player app, ratings sync, playback health | [docs/music/](../../docs/music/MUSIC_HANDOFF.md) |
| Ops and quality | test-suite health, dependency refresh, deploys | [docs/ops/](../../docs/ops/TEST_SUITE_REPAIR_2026-10-09.md) and [dependency-security-refresh](dependency-security-refresh.md) |

Superseded and finished pages live in [docs/archive/](../../docs/archive/INTEGRATION_2026-09-19.md) with a banner naming what replaced them.

Five pages stay at their old paths on purpose: code comments and tests cite them by path (`CONTRACT_OWNERSHIP.md`, `DEBUG_WAVE_PLAN.md`, `FOLLOWUP_TARGETING_PLAN.md`, `docs/WEB_SEARCH.md`, `docs/audit/AUDIT_2026-09-21_WEB_DATA_COLLECTION_BENCHMARK.md`). Move them only together with those comments.

## Page types (`type:` in front matter)

- **spec**: how a system works today. Edited in place.
- **decision**: a short dated note of what was decided and why.
- **plan**: work not yet done, or a design record. When it ships, fold the outcome into the spec and archive the plan.
- **record**: evidence of a finished wave, audit or handoff. Dated; not edited afterwards.

## Status words

`approved` and `in-progress` (live work, subject to the staleness check), `shipped`, `verified`, `reference` (kept for provenance; not checked against the code), `superseded`, `abandoned`.

## Rules that stop the pile

1. **One current page per topic.** A new wave updates the area's spec or adds one dated record; it does not start a parallel document.
2. **Root stays clean.** No new `*.md` in the repo root. File under `docs/<area folder>/` and give it the front matter above.
3. **Finished plans and one-off reports move to `docs/archive/`** with a banner naming the replacement. Check `git grep` for the file name first: if code, tests or scripts cite it, leave it and fix the citation in the same change.
4. **Pages are sorted by `part:`**, not by folder or date. Pick the area that matches the product surface.
5. **Dates in names are for records only**; specs carry no date in the file name.

## Known gaps

- `HANDOFF.md` (66 KB, now [docs/canvas/HANDOFF.md](../../docs/canvas/HANDOFF.md)) is a log of about twenty dated handoffs across several areas; it should be split by area.
- Pages marked `reference` and `type: plan` ([AGENT_ROADMAP](../../docs/routing/AGENT_ROADMAP.md), [RESEARCH_RELIABILITY_PLAN](../../docs/news-research/RESEARCH_RELIABILITY_PLAN.md), [DEV_CHECKLIST_2026-07-21](../../docs/canvas/DEV_CHECKLIST_2026-07-21.md), the three music plans, [FOLLOWUP_TARGETING_PLAN](../../FOLLOWUP_TARGETING_PLAN.md)) have not been checked against the code; each needs an owner to mark it shipped, superseded, or still open.
