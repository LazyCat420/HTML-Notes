# Music Player Widget Fix & Improvement Plan (Canvas HTML-Notes)

**Date**: 2026-09-23  
**Status**: APPROVED DECISIONS & IMPLEMENTATION SPECIFICATION (Brainstorming Plan)  
**Standard**: Universal Evidence-Driven Blueprint & Verified-Claim Plan Standard (`.agents/plan-verification-standard.md`)  
**Repositories Involved**:
- [`HTML-Notes`](file:///home/lazycat/github/projects/sun/HTML-Notes) (`LazyCat420/HTML-Notes`)

---

## 1. Executive Summary & Root Cause Synthesis

Following user review and alignment, the exact scope and architectural direction have been solidified:
1. **Track auto-advance on song end**: Fix the HTML5 audio `pause` before `ended` event trap in `nextTrack({ auto: true })`.
2. **Track list UI**: Keep the queue drawer hidden until the user clicks the queue button (preserving existing visual layout), but render the complete track list with an active equalizer/highlight on the currently playing track rather than slicing it into an empty `upcoming` list.
3. **`data-ask` Removal & Link De-Hijacking**:
   - Completely remove `:data-ask` from the music player track list rows.
   - De-hijack standard `<a>` links and interactive cards in [`delegateAsks`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L640-L651) so clicking an article or link actually navigates to the URL instead of dispatching an AI prompt.
4. **Instant Optimistic Playback**: Eliminate the 30s sequential `settleOnPlayable` bottleneck that overwhelmed the Synology NAS with parallel `yt-dlp` stream extractions. Play target tracks immediately in the user gesture callstack and reactively handle skips if YouTube refuses.

---

## 2. Technical Root Causes

### 2.1 The "Is it stuck in some kind of queue system?" Diagnosis
**Yes**, it was literally stuck in a sequential blocking probe queue across two services:
1. When `startStream()` enqueued tracks, it triggered [`pruneAhead()`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/js/widgets.js#L1395) and [`settleOnPlayable(0)`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/js/widgets.js#L1368).
2. `settleOnPlayable(0)` ran a sequential `while` loop calling `GET :8002/api/youtube/playable/{id}`.
3. Each probe to `music-player` forwarded to `scraper-service` (`http://10.0.0.16:8001/stream/{id}`), which spawned a full `yt-dlp` extraction process on the Synology NAS CPU.
4. On cold cache, each `yt-dlp` extraction takes 7 to 10 seconds. Probing 3 tracks in sequence blocked the frontend for 21 to 30 seconds before `loadTrack()` was ever called.
5. In addition, awaiting network requests caused the browser's user activation window to expire, causing `audio.play()` to be blocked by browser autoplay policy ("Autoplay prevented by browser policy").

### 2.2 Track End Advance Failure
In [`widgets.js:1050-1065`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/js/widgets.js#L1050-L1065), when audio reaches the end:
- Browser fires `pause`, setting `this.isPlaying = false`.
- Browser fires `ended`, calling `this.nextTrack({ auto: true })`.
- Inside `nextTrack()`, `const wasPlaying = this.isPlaying` evaluates to `false`.
- `if (wasPlaying) this.audio.play()` never executes because `auto: true` was ignored in the resume check.

### 2.3 `data-ask` Click Hijacking
- In [`factory.py:1310`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py#L1310), queue rows stamped `:data-ask="'who is ' + item.t.artist"`.
- For YouTube tracks, `item.t.artist` defaults to `"YouTube Music"`.
- The global capture listener in [`index.js:640-651`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L640-L651) intercepted all clicks in the capture phase, called `e.stopPropagation()`, blocked `@click="playAt()"`, and dispatched `HN.ask("who is YouTube Music")`.
- This launched 3 AI research agents, generated a new widget, unmounted the existing widget, wiped out the queue, and silenced playback.
- For news cards, `data-ask` prevented links from opening their destination URLs.

---

## 3. Approved Implementation Design

### 3.1 Track List in Music Widget
- Keep drawer hidden by default, toggled via `:class="showQueue ? 'h-[420px]' : 'h-[280px]'"` and `showQueue = !showQueue`.
- In the queue panel, iterate over `allTracks` with their absolute index:
  - If `index === currentIndex`: show active indicator (e.g. animated sound wave or highlight `bg-white/10 text-purple-200`) and "Playing" badge.
  - Clicking any row triggers `playAt(index)` immediately.
  - Hover shows remove button `removeAt(index)` for upcoming tracks.

### 3.2 Remove `data-ask` from Music Widget & Stop Link Hijacking
- In [`factory.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py) and [`index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js):
  - Completely strip `:data-ask` from the music player template.
- In [`index.js:delegateAsks`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L640-L651):
  - Check `if (e.target.closest("a[href]") && !e.target.closest(".hn-ask-chip")) return;`: Links must navigate to their destination! `data-ask` must NEVER intercept an `<a>` tag or prevent link navigation.
  - Ignore clicks inside `.music-player-widget` or interactive player controls.

### 3.3 Instant Optimistic Playback
- In `playAt(i, { auto = false } = {})`:
  - Immediately set `this.currentIndex = i`.
  - Immediately call `this.loadTrack()`.
  - Immediately call `this.audio.play()` in the user gesture callstack (resuming instantly without awaiting sequential probes).
  - If a track fails to stream, the existing `this.audio.addEventListener('error')` already triggers `handleStreamError()`, which cleanly skips to the next track in <0.3s.
  - Remove blocking sequential probes from `settleOnPlayable`. Let `pruneAhead` probe upcoming tracks in the background asynchronously with a single concurrency slot.

### 3.4 Auto-Advance on Track End
- In `nextTrack({ auto = false } = {})`:
  ```javascript
  const shouldResume = wasPlaying || auto;
  this.currentIndex = (this.currentIndex + 1) % this.queue.length;
  this.loadTrack();
  if (shouldResume && this.audio) {
      this.audio.play().catch(e => console.warn('[MusicPlayer] Play failed:', e));
  }
  ```

---

## 4. Test & Verification Plan

Following our TDD rules, all tests will be executed through `testrun` inside a dedicated git worktree:
1. **Red/Green Test 1 (Auto-advance)**:
   - Simulate `pause` event followed by `ended` event on `<audio>`.
   - Assert `currentIndex` increments and `audio.play()` is invoked.
2. **Red/Green Test 2 (`data-ask` De-Hijacking & Queue Clicks)**:
   - Verify clicking anywhere on a track row in the queue calls `playAt(i)` and does NOT dispatch `HN.ask`.
   - Verify clicking an `<a href="...">` inside cards navigates normally and is not swallowed by `delegateAsks`.
3. **Red/Green Test 3 (Full Track List)**:
   - Assert selecting the last track in the queue keeps all previous tracks visible in the list and does not render "Queue empty".
4. **Red/Green Test 4 (Optimistic Playback)**:
   - Verify `playAt(i)` calls `audio.play()` synchronously without awaiting network probes.

---

## 5. Next Steps
Once you give the command to proceed, we will:
1. Create a git worktree for `HTML-Notes`.
2. Write the failing tests in `tests/`.
3. Implement the fixes in `widgets.js`, `factory.py`, and `index.js`.
4. Run tests through `testrun`.
5. Check pre-commit diffs against GitGuardian rules.
6. Push to GitHub and deploy the container to Synology NAS (`npm run deploy -- --only=html-notes --skip-pull`).
