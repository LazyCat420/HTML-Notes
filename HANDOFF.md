# Handoff — 2026-07-20

Follow-up widget targeting: a follow-up question now edits the widget it came
from instead of stacking a duplicate or silently editing the wrong one.

**Current:** html-notes `main@a74e03c`, deployed to synology 2026-07-20T01:07:54Z.
Suite **211 passing** (was 209; 23 new guards).
**OPEN:** Prism MCP registry is empty — see the last section.

## What was wrong

Client reconcile (`data-sig`) and the update mechanism
(`soup.find(id).replace_with`) were both fine — they were fixed in earlier waves
and were never at fault. The defect was entirely in **which id we decide to
render into**.

The existing suite was 79/79 green while the behaviour was broken, because every
test asserted the current heuristic rather than the user-visible outcome. The
two-open-cards case was simply never covered.

## Six seams, all fixed

| | Seam | Fix |
|---|---|---|
| A | Agent tier took `tool_args["widget_id"]` verbatim — a near-miss id appended a NEW widget | `_resolve_agent_widget_id()` |
| B | `find_reuse_target` used "last match wins" | score + rank; recency is the tiebreaker only |
| C | In-memory ledger lost `focus_id` on restart, silently disabling the directive AND the message rewrite | recover from canvas DOM order |
| D | Scoring read the widget TITLE only — 0.00 on 5 of 7 real follow-ups | `max(title, ledger detail)` |
| E | Only 5 of 14 widget types reuse-eligible; `products`/`chart`/`checklist` could never update in place | `REUSABLE_WIDGET_TYPES` |
| F | `CURRENT CANVAS` appended last, so the router's 1200-char truncation cut the widget ids | canvas first, history is expendable |

Seam A was the biggest: tier-3 in-place update rested *entirely* on the model
echoing an 8-hex id exactly right, with no server-side reconciliation.
`_resolve_widget_target` already handled exactly this for the router tier — it
just was never wired into the agent branch.

**Phase 2:** the client now sends `focus_widget_id` — which widget the question
came from is a *fact*, so it outranks the model's guess and the topical score.
Tracked by delegated `pointerdown`/`focusin` on the canvas (survives reconcile
replacing nodes), validated server-side against the live canvas **and type** so a
dismissed or stale id can't clobber, and shown as a quiet focus ring.

## Measurements that drove the design

`_subject_overlap(query, ·)`, threshold 0.5 — title and detail are complementary,
neither works alone:

| query | vs title | vs detail |
|---|---|---|
| "what about cheaper sandals?" | 0.50 | 0.00 |
| "under $50" | 0.00 | **1.00** |
| "show me teva instead" | 0.00 | 0.50 |

Two-card disambiguation with `max()` picks the topically correct widget in 3 of 4
cases where "last match wins" picked the newest. The 4th ("under $50") ties at
0.00 — genuinely ambiguous from text, and precisely the case where recency IS the
right answer. Hence: recency as tiebreaker, never as the rule.

Full rationale and the pre-fix probe data: `FOLLOWUP_TARGETING_PLAN.md`.

## Gotchas found along the way

- **Discarding the model's id broke new widgets.** First cut of
  `_resolve_agent_widget_id` minted a fresh id whenever the model's id wasn't on
  canvas — which renamed every *first*-of-its-kind widget. Caught by
  `test_sse_duplication::test_sse_no_duplicate_widget`. Correct rule: retarget
  only when a real reuse target exists; otherwise keep the model's id.
- **`_REFINE_RE` is `^`-anchored.** "only show waterproof ones" → True, but
  "waterproof only please" and "under $50" → False. Added `_REFINE_MARKERS_RE`
  for non-opener refinements (anaphors, comparatives, bare constraints).
- **Filler words dilute the overlap coefficient.** "is teva any good?" against a
  card listing "Teva, Chaco, Keen" scored 0.33 (1 of 3 query tokens) purely
  because "any"/"good" carry no subject. Fixed via `_SUBJECT_STOP` — the right
  lever, rather than lowering the threshold and eating false positives.
- **`is-focused` is a transient class.** Added to the `crt-on`/`crt-off` strip in
  `getCleanedCanvasHtml()` along with `is-entering`/`is-updating` — nothing ever
  removes a class from server-persisted markup, so they'd bake in permanently.

## Verified live

- `weather-307132ce` is the SAME id across turn 1 ("weather in tokyo") and turn 2
  ("what about osaka") on the deployed container — updated in place, not stacked.
- `focus_widget_id` accepted by the live schema (200, not 422).
- New symbols confirmed present inside the running container.

## OPEN — blocks testing the research path

**Prism's MCP registry is empty.** `GET :7777/mcp-servers` → `[]`, so html-notes
reports `mcp_connected: false, tool_count: 0`. lazy-tool-service itself is healthy
(`:5591/health` → ok). It self-registers on its own boot, so the registration was
lost — most likely to a prism restart. The 2026-07-19 handoff recorded "Prism MCP
connected, 75 tools", so this is a regression in the environment.

**Pre-existing and unrelated to this change** — this deploy touched html-notes
only. But it means tier-3 research follow-ups (products / answer / image /
wikipedia cards) can't be exercised until it's restored. Tier-2 local asks
(weather, stock, sports, clock, music, map, traffic, news) are unaffected and were
verified working.

Fix is almost certainly a lazy-tool-service restart to re-trigger
self-registration. Not done here: other sessions deploy these services
concurrently, so it wasn't mine to bounce unannounced.

## Next

Feedback pending from live use. Phase 3 was deliberately left conservative:

- `mini_music_player`, `youtube_player`, `clock`, `notes`, `iframe_app` stay OUT
  of `REUSABLE_WIDGET_TYPES` — they own user state (a typed note, a playing
  track) that a mistargeted follow-up would destroy.
- Cross-type reuse is still unhandled: both resolvers require
  `wtype == widget_type`, so a modality change ("show that on a map") stacks a
  second widget by design. Fixing it needs a rule for what "convert this widget"
  means per type pair.
