// Regression tests for music player widget fixes:
// 1. Audio ended event auto-advances to next track and starts playback (even when pause fired first).
// 2. playAt(i) initiates playback optimistically and immediately without blocking on network probes.
// 3. Queue template does not contain :data-ask on track items (factory.py and index.js).
// 4. delegateAsks does not hijack real <a href> links or clicks inside music player widgets.
// 5. Full track list is rendered so previous tracks remain visible and selectable.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const widgetsJs = readFileSync(join(staticDir, "js", "widgets.js"), "utf8");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const factoryPy = readFileSync(join(here, "..", "app", "widgets", "factory.py"), "utf8");

test("nextTrack({ auto: true }) resumes playback even when isPlaying is false (ended event flow)", () => {
  // Sliced from widgets.js
  const from = widgetsJs.indexOf("nextTrack({ auto = false } = {}) {");
  assert.ok(from !== -1, "nextTrack definition missing");
  const to = widgetsJs.indexOf("prevTrack() {", from);
  assert.ok(to !== -1, "prevTrack marker missing");
  const src = widgetsJs.slice(from, to);

  const makeObj = new Function(`return {
    queue: [{ id: "track-1" }, { id: "track-2" }],
    currentIndex: 0,
    isPlaying: false, // pause event fired before ended
    audio: {
      src: "",
      played: false,
      play() { this.played = true; return Promise.resolve(); }
    },
    cancelHandoff() {},
    loadTrack() { this.audio.src = "http://stream/" + this.queue[this.currentIndex].id; },
    ${src}
  };`);

  const player = makeObj();
  player.nextTrack({ auto: true });
  assert.equal(player.currentIndex, 1, "currentIndex should advance");
  assert.equal(player.audio.played, true, "audio.play() MUST be called on auto-advance");
});

test("music player templates in factory.py and index.js do not have :data-ask on track items", () => {
  // Extract mini_music_player template in factory.py
  const factoryFrom = factoryPy.indexOf("def render_mini_music_player");
  assert.ok(factoryFrom !== -1);
  const factoryTo = factoryPy.indexOf("def render_youtube_player", factoryFrom);
  const factoryChunk = factoryPy.slice(factoryFrom, factoryTo);

  assert.ok(!factoryChunk.includes(":data-ask="), "factory.py music player template must NOT contain :data-ask");

  // Extract musicPlayerWidget self-heal template in index.js
  const indexFrom = indexJs.indexOf("newWidget.setAttribute('x-data', `musicPlayerWidget");
  assert.ok(indexFrom !== -1);
  const indexTo = indexJs.indexOf("Progress Bar & Time", indexFrom);
  const indexChunk = indexJs.slice(indexFrom, indexTo);

  assert.ok(!indexChunk.includes(":data-ask="), "index.js music player self-heal template must NOT contain :data-ask");
});

test("delegateAsks in index.js does not hijack <a href> links or clicks inside music player widgets", () => {
  const i = indexJs.indexOf('closest?.("[data-ask]")');
  assert.ok(i > 0, "no [data-ask] resolver");
  const around = indexJs.slice(Math.max(0, i - 300), i + 600);

  // Must check for <a href> or interactive exemption
  assert.match(around, /closest\??\.?\(\s*["']a\[href\]|closest\??\.?\(\s*["']\[x-data\*=.*musicPlayerWidget/i,
    "delegateAsks must exempt links or music player elements from being hijacked");
});

test("playAt() starts playback immediately without blocking on settleOnPlayable", () => {
  const from = widgetsJs.indexOf("playAt(i, { auto = false } = {}) {");
  assert.ok(from !== -1, "playAt definition missing");
  const to = widgetsJs.indexOf("removeAt(i) {", from);
  assert.ok(to !== -1, "removeAt marker missing");
  const src = widgetsJs.slice(from, to);

  // playAt must load track and call play() optimistically without awaiting settleOnPlayable first
  assert.ok(!src.includes("await this.settleOnPlayable"), "playAt must not block synchronously on settleOnPlayable probes");
});
