# Audit & Resolution: Dynamic Question Hijacking and Ghost Audio Playback

**Date:** 2026-09-26  
**Repository:** `html-notes`  
**Status:** RESOLVED & VERIFIED

---

## 1. Problem Statements & Root Causes

### Issue A: Open-ended News Follow-up Hijacked by Checklist
- **Symptom:** User asked `"how is trump going to \"reopen\" the strait"`, and instead of answering the question or displaying an answer/news card, a checklist widget popped up restoring a previous todo list.
- **Root Cause:**
  1. `LIST_RESTORE_RE` in `app/main.py` included isolated `|\brestore\b|\breopen\b`.
  2. The query contained `"reopen"`.
  3. `_resolve_restorable_list()` had no list-term guard; when given `"reopen"`, it fell back to returning `list:__last__` from SQLite.
  4. `ANSWER_ASK_RE` only matched `"how (do|can|to)"`, missing `"how is"`, `"how are"`, `"how will"`, etc.

### Issue B: Background Ghost Playback
- **Symptom:** During a follow-up query after `"close all"`, an underground rap track ("Primus" by danny ming & LST.83) and later Tycho's "A Circular Reeducation" started playing with no music widget visible on screen.
- **Root Cause:**
  1. `musicPlayerWidget` had an orphaned 60s fallback watchdog timer stored in a local variable `const watchdog`, which was not cleared on `closeStream()` or `destroy()`.
  2. `reconcileCanvas` in `app/static/index.js` removed or replaced DOM nodes using `existing.remove()` and `existing.replaceWith()` without calling Alpine's component teardown / `destroy()`.
  3. When the watchdog fired after 60s, it triggered `failover(term, 'artist')`, which initiated a background stream and played audio through an unattached `Audio` instance.

---

## 2. Changes Made

1. **Explicit List Vocabulary Guard in `app/main.py`:**
   - Modified `LIST_RESTORE_RE` to require explicit list keywords (`lists?|checklists?|todos?|to-dos?`) alongside `reopen` or `restore`.
   - Constrained `_resolve_restorable_list()` fallback to only return `list:__last__` if query explicitly references list terms.
   - Expanded `ANSWER_ASK_RE` to match auxiliary verbs: `how (do|can|to|is|are|will|would)|what (will|would)|why (will|would)`.

2. **Ghost Audio Prevention in `app/static/index.js` & `app/static/js/widgets.js`:**
   - Bound watchdog timer to `this.watchdogTimer` on the `musicPlayerWidget` instance.
   - Cleared `this.watchdogTimer` in `closeStream()`, `destroy()`, and SSE completion handlers.
   - Implemented `teardownWidget(el)` in `app/static/index.js` and wired it into `reconcileCanvas` before `existing.remove()` and `existing.replaceWith(newWidget)`.

3. **Music Playback History & Tabbed UI in `app/widgets/factory.py`, `app/static/index.js`, & `widgets.js`:**
   - Added `activeTab: 'queue'` ('queue' | 'history') state to the mini music player.
   - Added segmented tab toggles `[ Queue (N) ] [ History ]` inside the expandable drawer.
   - Added `console.log('[MusicPlayer] 🎵 Now Playing: ...')` to immediately identify any playing track.
   - Recorded every playback event to `POST /api/music/history` on the music-player backend and cached recent tracks in `localStorage['hn_music_history']`.

---

## 3. Verification & Evidence

- **Unit & Integration Tests:**
  - `tests/test_ghost_audio_and_history.mjs`: 4/4 passing (watchdog cleanup, teardownWidget wiring, history logging & sync, tabbed UI).
  - `tests/test_dynamic_routing.py`: 4/4 passing (end-to-end FastAPI test client asserting `"how is trump going to reopen the strait"` routes to `data_card`/`answer`, not `list`).
  - `tests/test_golden_routing.py` & `tests/test_edge_case_fixes.py`: 66/66 passing (zero regressions across golden routing suites).
  - `tests/test_music_player_fixes.mjs`: 4/4 passing.
