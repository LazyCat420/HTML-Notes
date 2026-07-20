# Follow-up → widget targeting: fix plan

**Problem.** A follow-up question fails to edit the widget it came from. It either
stacks a duplicate or silently edits the wrong (newest) widget.

**Not the problem.** Client-side reconcile is sound — `reconcileCanvas` keys on the
server-stamped `data-sig` and correctly preserves/updates live nodes. The update
mechanism (`soup.find(id=rid).replace_with(...)`) is also fine. The defect is
entirely in **which id we decide to render into**.

---

## Evidence

Existing suite: `tests/test_followup_reuse.py`, `test_turn_context.py`,
`test_canvas_context.py`, `test_prism_routing.py` — **79 passed**. The suite is
green while the behaviour is broken, because every test asserts the current
heuristic rather than the user-visible outcome. Probes written against desired
behaviour: **8 of 9 fail**.

Measured `_subject_overlap(query, widget_title)`, threshold 0.5:

| query | vs title | vs ledger detail | max |
|---|---|---|---|
| "what about cheaper sandals?" | 0.50 | 0.00 | 0.50 |
| "waterproof only please" | 0.00 | 0.50 | 0.50 |
| "under $50" | 0.00 | **1.00** | 1.00 |
| "show me teva instead" | 0.00 | 0.50 | 0.50 |
| "in celsius" | 0.00 | 0.00 | 0.00 |

Title-only scores **0.00 on 5 of 7** realistic follow-ups. The ledger `detail`
gist scores well on exactly the ones title misses — they are complementary, and
`max(title, detail)` clears threshold on 4 of 5.

Two-card disambiguation using `max(title, detail)`:

| query | dc-sandals | dc-bread | picks |
|---|---|---|---|
| "what about cheaper sandals?" | 0.50 | 0.00 | sandals ✓ |
| "show me teva instead" | 0.50 | 0.00 | sandals ✓ |
| "what hydration ratio?" | 0.00 | 1.00 | bread ✓ |
| "under $50" | 0.00 | 0.00 | tie → recency |

Content scoring picks the **topically correct** widget in 3 of 4 cases where
today's "last match wins" would pick the newest. The 4th is genuinely ambiguous
from text alone — that is where recency is the *right* tiebreaker, not the
default.

---

## Confirmed seams

**A — agent tier never validates the model's id.** `main.py:6421`:
`widget_id = tool_args.get("widget_id", f"widget-{uuid4().hex[:8]}")`. Verbatim,
unvalidated. A ghost id misses `soup.find` and appends a NEW widget
(`main.py:6631-6641`). The router tier already solves this via
`_resolve_widget_target` (`main.py:2159`) — it just isn't wired into the agent
branch. Tier 3 in-place update rests entirely on the model echoing an 8-hex id
exactly. **This is the primary failure and the cheapest fix.**

**B — `focus_id` is pure recency.** `main.py:2146-2150` takes the last widget of
the most recent producing turn. Never matched against the query. The message
rewrite at `main.py:6202` then makes this non-recoverable — the agent no longer
sees the original phrasing as a free choice.

**C — ledger is in-memory only.** `_session_turn_ledger` (`main.py:2071`). After
a restart the canvas survives (client resends `current_canvas`) but the ledger
does not → `focus_id is None` → the directive (`main.py:6094`) and the rewrite
(`main.py:6202`) both silently disable. Behaviour differs before/after restart
with identical on-screen state. Probe confirms: `focus_id` is `None`.

**D — regex gating is a hard gate.** `_REFINE_RE` (`main.py:1979`) is `^`-anchored:
`"only show waterproof ones"` → True, but `"waterproof only please"` → False,
`"under $50"` → False. Combined with title-only overlap, these fall through to a
new widget.

**E — type gating.** Reuse-eligible: `data_card, scoreboard, stock_card, weather,
map` (5). Factory renders **14**. Never reusable: `checklist, clock, notes,
iframe_app, mini_music_player, youtube_player, image, products, chart` — including
`products` and `chart`, the types users refine most. Also `wtype == widget_type`
is required (`main.py:2042`, `:2167`), so modality changes ("show that on a map")
always stack.

**F — router context truncated to 1200 chars** (`main.py:4711`), and
`CURRENT CANVAS` is appended last in `build_turn_context` (`main.py:2154`) — the
id list is the first thing cut, while the prompt says "never invent a widget id".

---

## Plan

### Phase 1 — stop the bleeding (server-only, no UI change)

1. **Wire the agent tier through the resolver.** Add `_resolve_agent_widget_id()`
   and call it at `main.py:6421` instead of reading `tool_args` raw. A model id
   that names a real canvas widget wins; a ghost id falls back to deterministic
   reuse; only then mint fresh. Reuses existing `_resolve_widget_target` logic.
   *Fixes A. Smallest diff, largest behavioural win.*

2. **Score against content, not just title.** Extend `find_reuse_target` to score
   `max(_subject_overlap(q, title), _subject_overlap(q, detail))` using the ledger
   detail already recorded by `_widget_detail`. *Fixes D's fallout.*

3. **Rank, don't take-last.** Replace "last match wins" (`main.py:2046`) with:
   highest score above threshold → that widget; tie or all-zero **and** deictic
   phrasing → `focus_id` recency; otherwise `None`. Recency becomes the tiebreaker
   rather than the rule. *Fixes B.*

4. **Recover `focus_id` from the canvas** when the ledger is empty — last widget
   in DOM order. *Fixes C.*

**Risk:** these change targeting for existing green tests. Expect
`test_turn_context.py` / `test_followup_reuse.py` to need updating where they
assert newest-wins. Each such edit must be justified as "test pinned the bug".

### Phase 2 — make it deterministic (client + server)

5. **Send a focus signal.** Client currently sends no widget context at all
   (`index.js:1223-1234`) and has no targeting UI (only dismiss). Add
   last-interacted-widget tracking, or a per-widget "ask about this" affordance,
   and a `focus_widget_id` field on `MessageRequest`. When present it outranks all
   inference. *This is the only fix that makes targeting exact rather than
   probabilistic.*

### Phase 3 — widen coverage

6. **Invert the type allowlist to a deny-list** so `products`, `chart`,
   `checklist` etc. become reuse-eligible. Needs per-type thought about what
   "update in place" means; do it after 1-5 are verified.

7. **Allow cross-type reuse** for explicit modality changes (E), and **move
   `CURRENT CANVAS` ahead of the ledger** in `build_turn_context` so truncation
   drops history rather than ids (F).

### Verification

- Convert the 9 probes into a durable `tests/test_followup_targeting.py`.
- Each must be shown to FAIL on the current code before the fix lands — no
  vacuous guards.
- End-to-end in a real browser (playwright harness, per prior waves): two cards
  open, follow-up on the older one → same id, `data-sig` changes, widget count
  unchanged, `is-updating` flash. API-level success has hidden a canvas that never
  painted before.

---

## Recommendation

Phase 1 items 1-3 are the high-value core; item 1 alone likely fixes the majority
of observed failures. Phase 2 item 5 is what makes it actually reliable, but it
touches the frontend and wants its own pass.
