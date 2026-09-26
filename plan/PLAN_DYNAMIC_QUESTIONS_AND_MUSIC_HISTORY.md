# Plan: Dynamic Questions Routing Fix in html-notes & Music Player History Tracking

**Document Version:** 1.0.0  
**Date:** 2026-09-26  
**Status:** PROPOSED (Brainstorming & Alignment Phase — Awaiting User Approval)  
**Standard:** Verified-Claim Plan Methodology (VCPM) & Phase 0–8 Blueprint  

---

## Executive Summary & Mystery Song Identification

### 1. The Mystery Background Song
From primary artifact inspection of the live Synology NAS container logs (`sudo docker logs music-player`) and the live SQLite database (`/app/data/music_library.db`):
- **Artist:** Tycho
- **Title:** "A Circular Reeducation"
- **YouTube Video ID:** `0QKxKMz8SW0`
- **Track Duration:** 327 seconds (5:27)
- **Exact Timestamp Proxied:** `2026-09-26 16:21:22 UTC` (`status=206 Partial Content`, `content-length=6,812,524 bytes`)
- **How it occurred:** The browser initialized `musicPlayerWidget` with `term="primus" kind="genre"`. The genre mix returned empty, triggering failover to the artist radio pipeline, which streamed a batch of related discovery artists including Tycho (`0QKxKMz8SW0`).
- **Why it didn't print in your browser console:** `widgets.js` in `html-notes` had no console logging when a track loads or starts playing (it only logged generic init and fetch warnings). The audio error you saw in `index.js:4066` (`Audio playback error`) was the spoken TTS voice narrator failing to fetch an audio blob, not the music player.

---

## Phase 0 — Orient: Ground Truth & Root Cause Diagnoses

### Problem 1: Dynamic Questions Hijacked by Fast-Path Utility Widgets
- **Symptom:** Query `"how is trump going to \"reopen\" the strait"` immediately routed to:
  `route: fast-path → checklist` with status `"bringing your list back..."` and restored an old checklist widget from previous sessions.
- **Root Cause Analysis (Verified Facts):**
  1. **`LIST_RESTORE_RE` Greedy Match (`app/main.py:1800` & `app/routes/message.py:853`):**
     ```python
     LIST_RESTORE_RE = re.compile(
         r'\b(bring|get|put|pull|give)\b[^.]*\bback\b'
         r'|\brestore\b|\breopen\b'
         r'|\blists?\b[^.]*\bagain\b|\bagain\b[^.]*\blists?\b'
         r'|\bback\b[^.]*\blists?\b')
     ```
     The standalone word `\breopen\b` matches any query containing "reopen" (e.g. "reopen the strait", "reopen negotiations", "reopen schools") even if no list is mentioned!
  2. **Unconstrained Fallback in `_resolve_restorable_list` (`app/main.py:2790`):**
     If the words in the query don't match any named list slug, the function unconditionally falls back to returning whatever was saved in `database.get_widget_state("list:__last__")`. Because a checklist existed in the DB, `restored and restored.get("items")` returned `True`, hijacking the dynamic question into a checklist widget.
  3. **`ANSWER_ASK_RE` Missing Dynamic Interrogatives (`app/main.py:1908`):**
     `ANSWER_ASK_RE` only checks for `how to|how do|how does|how can`. It is completely missing `how is|how are|how will|how would|how did|what will|what would|why would|why will`. Thus, "how is trump going to..." does not match the answer lane and falls through to heuristic collisions.

---

### Problem 2: Missing Music Playback History in `music-player` and `html-notes`
- **Root Cause Analysis (Verified Facts):**
  1. The `music-player` backend already has a fully functioning SQLite table `listening_history` and a REST endpoint `POST /api/music/history` (`PlayEvent`).
  2. The standalone web client (`apps/web/lib/historyService.ts`) has `recordPlay` and `getRecentlyPlayed`.
  3. **The embedded `musicPlayerWidget` in `html-notes` (`app/static/js/widgets.js`) NEVER calls `POST /api/music/history`!**
  4. `musicPlayerWidget` does not print `[MusicPlayer] 🎵 Now Playing: ...` to the console.
  5. `musicPlayerWidget` provides no UI for recently played tracks (only an upcoming `queue`), so once a song finishes or plays in the background, the user has no way to see what it was or re-listen to it.

## Phase 1 — Confirmed Specifications & Implementation Architecture

### User Selections (Confirmed 2026-09-26):
1. **Dynamic Question Presentation:** **Synthesized Research Card** (`data_card` with executive summary, key takeaways, and relevant source articles via `build_answer_config`).
2. **Music Player History UI:** **Tabbed panel inside widget** (`Queue` / `History` toggle in `musicPlayerWidget`) with one-click replay from recently played tracks.
3. **Capture Trigger:** **Instant capture on playback start** — log and dispatch record as soon as audio begins streaming so no track is lost even if skipped.

---

### Workstream A: Fix Dynamic Question Routing in `html-notes`

#### Claim A.1: Fix `LIST_RESTORE_RE` so it strictly requires a list context
- **Current Behavior:** Matches bare `"restore"` or `"reopen"`.
- **Proposed Fix:** Require the mention of a list or checklist alongside restore/reopen verbs:
  ```python
  LIST_RESTORE_RE = re.compile(
      r'\b(bring|get|put|pull|give)\b[^.]*\bback\b'
      r'|\b(restore|reopen)\b[^.]*\b(lists?|checklists?|todos?|to-dos?)\b'
      r'|\b(lists?|checklists?|todos?|to-dos?)\b[^.]*\b(restore|reopen)\b'
      r'|\blists?\b[^.]*\bagain\b|\bagain\b[^.]*\blists?\b'
      r'|\bback\b[^.]*\blists?\b', re.I)
  ```
- **Test:** Assert that `"reopen the strait"` and `"restore power to grid"` evaluate to `False`, while `"reopen my grocery list"` evaluates to `True`.

#### Claim A.2: Guard `_resolve_restorable_list` Fallback
- **Current Behavior:** Returns `list:__last__` even if the user query contains zero list-related words.
- **Proposed Fix:** Only return `list:__last__` if the query explicitly contains list-related vocabulary (`list`, `checklist`, `todo`, `tasks`, etc.). Otherwise, return `None`.

#### Claim A.3: Expand Dynamic Question Recognition in `ANSWER_ASK_RE` & Research Intent
- **Current Behavior:** Fails on questions starting with `how is`, `how will`, `how would`, `how are`, `what will`, `why would`, etc.
- **Proposed Fix:** Expand `ANSWER_ASK_RE` to include future/conditional interrogatives:
  ```python
  r'\b(how to|how do|how does|how can|how is|how are|how will|how would|how did|'
  r'what is|what are|whats|what\'s|what will|what would|'
  r'who is|who are|who was|who will|who would|'
  r'why is|why do|why does|why will|why would|explain)\b'
  ```
  And ensure dynamic news/geopolitical questions route either to `build_answer_config` (producing a rich `data_card` with researched synthesis) or the research protocol, rather than heuristic utility cards.

---

### Workstream B: Music Playback History & Observability

#### Claim B.1: Console Logging on Playback Start
- In `html-notes/app/static/js/widgets.js`:
  In `playAt()` and the `audio.addEventListener('playing')` handler:
  ```javascript
  console.log(`[MusicPlayer] 🎵 Now Playing: "${track.title}" by "${track.artist}" (ID: ${track.id})`);
  ```
  Ensure this is emitted both in browser DevTools and user-visible telemetry.

#### Claim B.2: Record Plays Instantly to `music-player` Backend (`POST /api/music/history`)
- **Trigger:** Immediately upon audio stream playback start (`audio.addEventListener('playing')` / `playAt()`):
  - Send asynchronous POST to `${this.base}/api/music/history` with:
    ```json
    {
      "id": crypto.randomUUID(),
      "track_id": track.isYoutube ? ("youtube://" + track.id) : track.path,
      "artist": track.artist || "Unknown Artist",
      "title": track.title || "Unknown Title",
      "album": track.album || "",
      "timestamp": new Date().toISOString(),
      "duration": Math.round(this.duration || 0),
      "completion_rate": 1.0,
      "source": "html-notes",
      "context": this.genreFilter || "radio"
    }
    ```
  - Also persist the last 50 played tracks in `localStorage` under `hn_music_history` as an offline/instant local cache.

#### Claim B.3: Tabbed Panel (`Queue` | `History`) in `musicPlayerWidget`
- In `html-notes/app/widgets/factory.py` (HTML structure) and `html-notes/app/static/js/widgets.js` (Alpine component):
  - Add state `activeTab: 'queue'` ('queue' | 'history').
  - Render a sleek segmented pill toggle in the widget panel: `[ Queue (N) ] [ History ]`.
  - When the user selects `History`:
    - Fetch `${this.base}/api/music/history/recent?limit=30` (with fallback to `localStorage.getItem('hn_music_history')`).
    - Display the list of recently played tracks with song title, artist, and click-to-play handler.
    - Clicking any item in the history instantly cues and plays that track!

---

### Workstream C: Prevent Ghost Audio & Orphaned Widget Background Playback

#### Root Cause of the News Follow-up Playback:
1. `musicPlayerWidget` was initialized when the page loaded (`term="primus"`).
2. The user issued `"close all"` and `"close everything"`.
3. The server cleared the canvas HTML, and the client removed the DOM elements (`existing.remove()`).
4. **Alpine `destroy()` was never called on removed widgets:**
   - In `index.js:1489` (`reconcileCanvas`) and `index.js:103` (`WidgetManager.dismiss`), `existing.remove()` only detached the DOM node.
   - It did NOT call `Alpine.$data(existing)?.destroy?.()`.
5. **The Orphaned Stream Continued in JavaScript Memory:**
   - The 60-second watchdog timer (`widgets.js:1150`) and `EventSource` (`this.es`) remained alive.
   - Exactly while the user was typing `"how is trump going to reopen the strait"`, the 60-second watchdog fired `failover(term, 'artist')`, which found Tycho and triggered `this.playAt(0, { auto: true })` on the orphaned in-memory `Audio` object!

#### Fix Specifications:
- In `html-notes/app/static/index.js`:
  1. In `WidgetManager.dismiss(widgetElement)`:
     - Before removing: call `Alpine.$data(widgetElement)?.destroy?.()` and dispatch a cleanup event.
  2. In `reconcileCanvas(container, rawHtml)`:
     - When an existing widget is removed (`existing.remove()` at line 1489): call `Alpine.$data(existing)?.destroy?.()`.
     - When an existing widget is replaced (`existing.replaceWith(newWidget)` at line 1542): call `Alpine.$data(existing)?.destroy?.()`.
  3. In `clear_all` / canvas wipe commands:
     - Disconnect and stop any running audio or media streams explicitly.

---

## Phase 2 — Testing & Validation Strategy (TDD)

1. **Unit & Regression Tests (`html-notes/tests/test_routing.py`):**
   - Test `LIST_RESTORE_RE` against adversarial queries:
     - `"how is trump going to reopen the strait"` → Must NOT match.
     - `"restore the power grid after storm"` → Must NOT match.
     - `"reopen my packing list"` → MUST match.
     - `"bring back my list"` → MUST match.
   - Test `_resolve_restorable_list`:
     - Must return `None` for queries lacking list keywords even when `list:__last__` is present in DB.
   - Test `ANSWER_ASK_RE`:
     - `"how is trump going to reopen the strait"` → MUST match answer lane and return an informational `data_card`.
2. **Music History Tests (`html-notes/tests/test_music_history.mjs` & `music-player`):**
   - Assert `musicPlayerWidget` dispatches `POST /api/music/history` with valid `PlayEvent` schema upon playback.
   - Assert `musicPlayerWidget` loads and displays tracks from `/api/music/history/recent`.
   - Assert console output format contains title, artist, and ID.

---

## Phase 3 — Next Steps & Confirmation

As required by user guidelines:
1. **This plan is for brainstorming and alignment only.** No implementation will proceed until you review and approve.
2. Please review the follow-up questions below so we can address your exact design preferences before writing any code.
