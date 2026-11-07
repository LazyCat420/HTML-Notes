---
part: Operations
type: reference
status: verified
updated: 2026-11-06
last-verified: 2026-11-06
review-by: 2026-12-06
covers: html-notes:tests/, html-notes:scripts/routing_eval.py, html-notes:scripts/golden_routing_live.py, html-notes:scripts/news_check.py, html-notes:tests/routing_eval.json, html-notes:pytest.ini
---

# Verification and benchmarks

What is proven, by which command, and what is still unproven.

## Unit and integration suite

```bash
.venv/bin/python -m pytest tests/ -x -q          # serial; pytest.ini pins testpaths = tests
node --test tests/*.mjs                            # client harnesses (widget config, handoff, ghost audio)
```

- `pytest.ini` restricts collection to `tests/`; `scratch/test_*.py` are ad-hoc
  scripts whose import order breaks the wildcard builder names for the whole run.
- The suite was made runnable again in `8b7b709` (it was collecting nothing and
  mocking nothing). At the 2026-10-22 dependency refresh the baseline was
  **3 failed / 1328 passed**; the three failures are `tests/test_app_hub.py`
  portal-registry URL expectations, unrelated to code under review.
- `tests/test_build_and_data_gate.py` guards the build/data contract.
- `tests/test_local_tool_admission_security.py` and
  `tests/test_agent_guardrails.py` / `test_agent_loop_guards.py` guard the
  runtime boundary (see ch.12).

## Client harnesses (node)

The `.mjs` tests drive real factory HTML through real `app/static/index.js`
helpers in node (`_music_widget_harness.mjs`, `_crt_widget_harness.mjs`):

- `test_music_config_and_history.py` validates the widget's play event against
  music-player's real `PlayEvent` model, with a red witness that the old body
  fails it, plus twin-iframe parity.
- Adding `window.addEventListener` and an unref'd `setInterval` to the harness
  un-broke 12 behaviour tests: **node suite went 93/108 → 106/106**.
- Recorded false positive: `test_ghost_audio_and_history.mjs` only grepped for
  the string `/api/music/history`, so it stayed green through the 422 history
  ticket — assertions must parse the request body, not grep for endpoints.

## Routing eval (the benchmark)

`tests/routing_eval.json`: 87 real asks mined from the deployed `chat_messages`
(2,966 user rows, 1,450 distinct), labelled with type / kind / subject.

```bash
python scripts/routing_eval.py --mode system   # real /session/message in-process, live router
python scripts/routing_eval.py --mode router   # the model alone
python scripts/golden_routing_live.py          # live answers vs golden set
```

Music asks, measured 2026-11-03 (`8d3660c`):

| Run | Music asks correct | First player |
|---|---|---|
| main before the change | 31/52 (59.6%) | p50 0.06 s |
| branch, router blocking, 4 s budget, no refine | 19/52 under box load | p50 4.07 s |
| branch, 1.5 s budget, no refine | 31/52 | p50 1.57 s |
| **branch as shipped: 1.5 s budget + refine** | **48/52 (92.3%)** | p50 1.58 s |
| router alone, final catalog | 40/52 | p50 3.2 s, p95 23.3 s |

The four remaining misses are recorded with their causes in ch.05. Router latency
depends on shared-box load (measured 30 s vs 0.46 s for a 20-token reply minutes
apart); a slow router run is an environment fact, not a regression — rerun and
record both.

## Rendering and layout proofs

| Claim | Proof |
|---|---|
| Compiled Tailwind == CDN pixels | Screenshot diff, 10 freshly rendered widgets + a saved live canvas: **0 changed pixels** (1400×900); control with no Tailwind at all: 81,013 changed pixels, so the harness detects missing styles. |
| Every emitted utility is in the build | `tests/test_tailwind_build.py` (red witness: drop `factory.py` from the content globs). |
| Music widget plays after a swap | `test_ghost_audio_and_history.mjs` parses the play event; `scripts/music_handoff_check.py` against the live app. |
| CRT tube fit and tuning | `scripts/youtube_crt_check.py` (aspect, tube fit, tune, static, knob, drag, reset) and `scripts/youtube_crt_flow_check.py` (ask → channel → second ask wraps). |
| News card quality | `scripts/news_check.py`: vetted stories and summaries from the pipeline. |

## Live checks vs unit tests

The `scripts/*_check.py` probes run against the deployed app; they can fail for
environment reasons (box load, network). Record the result with its timestamp in
the ticket; never treat one red live probe as a code regression without a rerun.

## Not proven

- Video and news lanes are not router-scored above their deterministic overrides
  (ch.10 DEC-001 keeps them out of the router until they beat the eval set).
- `research_evidence` answer citation has ledger audits but no golden corpus.
- Device-widget resize under narrow column spans is checked by live probe only.
