// A track the CDN will not serve must not end the listening session.
//
// YouTube currently refuses to serve most of an audio file unless the request
// carries a PO token or an authenticated cookie (see
// music-player/AUDIO_PIPELINE.md). Measured 2026-08-17 on a live 12-track jazz
// mix: 10 of 12 tracks served only their first ~1MB, so the <audio> element
// raised `NotSupportedError` before playing a note.
//
// That part is not ours to fix. What WAS ours: the widget's error handler set
// `error` and stopped. One dead track therefore killed a queue in which
// roughly one track in six still plays perfectly — the user got silence and a
// red message while a working track sat two rows down.
//
// So: on a stream error, advance. The bound matters as much as the skip — a
// queue where EVERY track is dead must stop and say so, not spin through the
// queue forever re-requesting dead URLs.
//
// Run: `node --test tests/test_music_dead_track_skip.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const widgetsJs = readFileSync(join(here, "..", "app", "static", "js", "widgets.js"), "utf8");

// Sliced and run for real, not retyped — a copy stops tracking the original.
function extract(startMarker, endMarker) {
  const from = widgetsJs.indexOf(startMarker);
  assert.ok(from !== -1, `could not find ${startMarker} in widgets.js`);
  const to = widgetsJs.indexOf(endMarker, from + startMarker.length);
  assert.ok(to !== -1, `could not find ${endMarker} after ${startMarker}`);
  return widgetsJs.slice(from, to);
}

const src = extract("handleStreamError() {", "openInFullPlayer() {");
const makeHandler = new Function(`return { ${src} };`);

function widget(overrides = {}) {
  const loaded = [];
  const self = {
    queue: [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }],
    currentIndex: 0,
    isPlaying: true,
    error: "",
    streamStatus: "",
    deadInARow: 0,
    maxDeadInARow: 8,
    audio: { src: "http://svc/api/youtube/stream/a", play() { this.played = true; }, played: false },
    get currentTrack() {
      return this.currentIndex >= 0 && this.currentIndex < this.queue.length
        ? this.queue[this.currentIndex] : null;
    },
    // Real nextTrack semantics: wrap, reload, resume if it was playing.
    nextTrack() {
      if (this.queue.length === 0) return;
      const wasPlaying = this.isPlaying;
      this.currentIndex = (this.currentIndex + 1) % this.queue.length;
      loaded.push(this.currentIndex);
      if (wasPlaying) this.audio.play();
    },
    ...overrides,
  };
  Object.assign(self, makeHandler());
  return { self, loaded };
}

test("a dead track advances to the next one instead of stopping", () => {
  const { self, loaded } = widget();
  self.handleStreamError();
  assert.equal(self.currentIndex, 1, "should have moved on");
  assert.deepEqual(loaded, [1]);
});

test("the skip keeps playing — a silent 'next track' is the same failure", () => {
  const { self } = widget();
  self.handleStreamError();
  assert.equal(self.audio.played, true, "must resume playback on the new track");
});

test("it resumes even after the failure already cleared isPlaying", () => {
  // The realistic case, and the one nextTrack cannot cover: a track that
  // errors has usually flipped isPlaying to false via the `pause` listener,
  // so nextTrack's own "resume if it was playing" does nothing and the widget
  // walks the queue in silence. Only an explicit play() here saves it.
  const { self, loaded } = widget({ isPlaying: false });
  self.handleStreamError();
  assert.deepEqual(loaded, [1], "still advances");
  assert.equal(self.audio.played, true, "and must actively start the new track");
});

test("it does not leave a red error up while it recovers", () => {
  const { self } = widget({ error: "Audio playback error." });
  self.handleStreamError();
  assert.equal(self.error, "", "a recoverable skip must clear the error");
});

test("an all-dead queue stops instead of spinning forever", () => {
  const { self, loaded } = widget();
  for (let i = 0; i < 40; i++) self.handleStreamError();
  assert.ok(loaded.length <= self.maxDeadInARow,
    `bounded at ${self.maxDeadInARow}, got ${loaded.length} skips`);
  assert.match(self.error, /\S/, "it must say something once it gives up");
  assert.equal(self.isPlaying, false, "and must not claim to be playing");
});

test("a track that plays resets the budget, so later failures can skip again", () => {
  const { self } = widget();
  for (let i = 0; i < 5; i++) self.handleStreamError();
  self.notePlaybackStarted();
  assert.equal(self.deadInARow, 0);
  const { loaded } = (() => {
    for (let i = 0; i < 6; i++) self.handleStreamError();
    return { loaded: null };
  })();
  assert.match(self.error, /^$/, "budget was reset, so these skips are still recoverable");
});

test("the teardown error (destroy sets src='') is not treated as a dead track", () => {
  // destroy() clears src, which itself fires `error`. Skipping on it would
  // resurrect a widget the user just closed.
  const { self, loaded } = widget({ audio: { src: "", play() { } } });
  self.handleStreamError();
  assert.equal(loaded.length, 0, "must not advance");
  assert.equal(self.currentIndex, 0);
});

test("a single-track queue reports rather than looping on itself", () => {
  const { self, loaded } = widget({ queue: [{ id: "only" }] });
  for (let i = 0; i < 12; i++) self.handleStreamError();
  assert.equal(loaded.length, 0, "nothing to skip to");
  assert.match(self.error, /\S/);
});
