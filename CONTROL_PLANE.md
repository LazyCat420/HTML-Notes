# Canvas control plane — driving the other containers from chat

The App Hub opens apps. This lets the canvas *operate* them: start a trading
cycle, ask wallgarden what to watch, record a play into music-player's history.

## Shape

ONE generic tool, backed by a JSON registry:

```
html_notes_app_action(app_id, action, params)   # run it
html_notes_list_actions(app_id?)                # discover exact param names
```

Everything lives in **`app/app_actions.json`**. Adding a repo or a capability
is a JSON edit — no tool-schema rebuild and no lazy-agent redeploy. That is the
whole reason for a registry: the gateway caches tool schemas at **startup**, so
a tool-per-repo design would cost a two-service redeploy every time you thought
of a new action.

Executor: `app/services/app_actions.py`. Routes: `GET /api/actions`,
`POST /api/actions/run`, `POST /api/actions/cancel`.

## Safety model — the model can ask, only you can fire

Every action declares `destructive`.

- **Safe** (`cycle_status`, `suggest_videos`, `record_play`) runs inline and
  returns the result.
- **Destructive** (`start_cycle`, `stop_cycle`, `enrich_strains`) is **never
  executed by the agent**. The tool parks it server-side and the SSE
  interceptor puts an `action_confirm` card on the canvas. Only your click on
  *Run it* calls `/api/actions/run`.

Three properties make that gate real rather than decorative:

1. **The model has no path to execution.** It cannot call `/api/actions/run` —
   that endpoint takes a `pending_id` minted by the server, and the tool
   handler returns `confirmation_required` instead of a result. A hallucinated
   ticker cannot start a cycle.
2. **A pending action is consumed on use.** Double-clicking cannot start two
   cycles — the second call gets "expired".
3. **Pending actions live in memory with a 15-minute TTL.** A stale card cannot
   fire something you asked for an hour ago, and a restart clears them all.

The card is rendered by the interceptor, not by `canvas_add_widget`, so the
model cannot skip it or invent its own config.

## Verified live (2026-08-16)

| Case | Result |
|---|---|
| `trading-client.cycle_status` (safe) | ran, returned the real cycle record |
| `trading-client.start_cycle` with `trade:true` (destructive) | **parked**, `confirmation_required`, nothing ran |
| confirm → `/api/actions/run` | executed once (`{"status":"stopping"}`) |
| same `pending_id` again | refused — "expired" |
| bogus `pending_id` | refused |
| unknown action `delete_everything` | rejected, and the valid catalog returned |
| `youtube-wallgarden.user_state` | 78 ratings, 338 watched, 60 mined topics, taste profile |

40 tests in `tests/test_app_hub.py`; the full-suite failure set equals the
clean-main baseline (the two tests that differ flip on clean main too — 3
failed / 18 passed / 1 failed across three identical runs there).

## Registry conventions

- `body` templating: `"$name"` is optional and the **key is dropped when
  absent** (so an omitted param never lands as a literal `"$name"` or a null an
  API would treat as an explicit value); `"$name!"` is required and fails
  before any network call.
- `destructive` is the only thing standing between a chat sentence and a real
  action. When in doubt, mark it true — a test pins the money/job actions.
- Prefer an app's **own guarded endpoint** over poking its database.
  `POST :8888/api/v1/run-cycle` is used rather than inserting into
  `v3_system_commands` directly, because the endpoint carries a 120-second
  double-spawn guard that a raw INSERT bypasses.

## Video flow (as designed with the user)

- **Specific** ask ("bloomberg live news feed") → search normally and play.
- **Vague** ask ("something to watch") → `youtube-wallgarden.suggest_videos`
  first, so the pick is informed by the user's own ratings/topics rather than a
  cold search.

## Open item — wallgarden's LLM endpoints are DOWN (blocks the smart path)

`suggest_videos` and `taste_topics` currently fail:

```
Prism /chat returned 500: Unknown provider "vllm-2".
Available: openai, anthropic, google, moonshot, elevenlabs, inworld, (+ local instances)
```

`lazy-agent-service/src/routes/WallgardenRoutes.ts` calls **prism `/chat`**
(`:32` — "calls vLLM via prism /chat"), and prism has **zero local provider
instances registered**, so `vllm` *and* `vllm-2` are both unknown. Passing an
explicit provider/model does not help — verified.

Consequences beyond this feature: wallgarden's own topic generation, taste
profile and "similar" all run through the same route, so **the app's feed
refill is broken too**, not just the canvas path.

This is the same root cause already filed in `lazy-agent-service/HANDOFF.md`
(open item 3) that forced HTML-Notes off prism-mode. The fix is caller-side —
point WallgardenRoutes at the gateway's local instances (`vllm` = Jetson,
`vllm-2` = Gold Spark) instead of prism. `youtube-wallgarden.user_state` is
unaffected and already useful: it exposes the ratings/watched/mined data the
agent can ground a recommendation in without any LLM call.

## Also worth knowing

Wallgarden's ranking algorithm is **browser JavaScript**, not a service. Writing
signals (ratings/watched) into its sync store enriches what it reasons over, but
nothing re-ranks until a wallgarden tab is open and polls (45 s). "Canvas makes
wallgarden smarter" is true asynchronously, not live.
