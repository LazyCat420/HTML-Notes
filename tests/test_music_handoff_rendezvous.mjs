// The canvas must not go silent on faith.
//
// Measured live 2026-08-17 with a REAL browser autoplay policy: after the
// click, the canvas paused instantly and the music-player tab sat loaded and
// paused at exactly the handed-over second, waiting for a click that the user
// never saw. Silence on both sides, and the earlier test suite missed it
// entirely because its harness launched Chromium with
// --autoplay-policy=no-user-gesture-required.
//
// So the widget now keeps playing and polls a rendezvous record on the
// music-player API — the only channel two different origins share — parking
// its LIVE position, and stops only once the other tab reports audio actually
// flowing.
//
// Run: `node --test tests/test_music_handoff_rendezvous.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const widgetsJs = readFileSync(join(here, "..", "app", "static", "js", "widgets.js"), "utf8");

function extract(startMarker, endMarker) {
  const from = widgetsJs.indexOf(startMarker);
  assert.ok(from !== -1, `could not find ${startMarker}`);
  const to = widgetsJs.indexOf(endMarker, from + startMarker.length);
  assert.ok(to !== -1, `could not find ${endMarker} after ${startMarker}`);
  return widgetsJs.slice(from, to);
}

// awaitHandoff + cancelHandoff, sliced and run for real.
const src = extract("awaitHandoff(handoffId, timeoutMs", "        setVolume(vol) {");
const makeHandoff = new Function("window", "fetch", "setInterval", "clearInterval", `return { ${src} };`);

const tick = () => new Promise(r => setTimeout(r, 0));

function widget({ started = false, failFetch = false } = {}) {
  const puts = [];
  let timerId = 0;
  const timers = new Map();

  const fakeFetch = async (url, opts) => {
    puts.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
    if (failFetch) throw new Error("network down");
    return { ok: true, json: async () => ({ found: true, started }) };
  };

  const self = {
    base: "http://svc:8002",
    streamStatus: "",
    handoffPending: null,
    handoffTimer: null,
    audio: { currentTime: 12.5, paused: false, pause() { this.paused = true; } },
    currentTrack: { id: "trackA" },
  };
  Object.assign(self, makeHandoff(
    {},
    fakeFetch,
    (fn) => { const id = ++timerId; timers.set(id, fn); return id; },
    (id) => timers.delete(id),
  ));
  return { self, puts, timers };
}

test("it does NOT stop while the other tab is still silent", async () => {
  const { self } = widget({ started: false });
  self.awaitHandoff("abc123def456");
  await tick();
  assert.equal(self.audio.paused, false, "must keep playing until handover is confirmed");
  assert.equal(self.handoffPending, "abc123def456");
});

test("it stops the moment the other tab reports audio flowing", async () => {
  const { self } = widget({ started: true });
  self.awaitHandoff("abc123def456");
  await tick();
  assert.equal(self.audio.paused, true, "must stop once the other tab has it");
  assert.equal(self.handoffPending, null, "and stop polling");
});

test("it parks the LIVE position, not the one from when the link was built", async () => {
  // The canvas plays on while it waits, so the position in the URL is already
  // stale by the time the other tab starts. The receiver prefers this one.
  const { self, puts } = widget({ started: false });
  self.audio.currentTime = 12.5;
  self.awaitHandoff("abc123def456");
  await tick();
  assert.ok(puts.length >= 1, "should have posted at least once");
  assert.equal(puts[0].body.position, 12.5);
  assert.equal(puts[0].body.track, "trackA");
  assert.match(puts[0].url, /\/api\/handoff\/abc123def456$/);
});

test("a cancelled handoff can never silence the music later", async () => {
  // The listener picked a different track here — a late "started" from the
  // other tab must not pause what they just chose.
  const { self, timers } = widget({ started: true });
  self.awaitHandoff("abc123def456");
  self.cancelHandoff();
  assert.equal(self.handoffPending, null);
  assert.equal(timers.size, 0, "poll must be cleared");
  // Drive a stale tick anyway: it must bail out rather than pause.
  await tick();
  assert.equal(self.audio.paused, false);
});

test("a network blip keeps the music playing rather than stopping it", async () => {
  const { self } = widget({ failFetch: true });
  self.awaitHandoff("abc123def456");
  await tick();
  assert.equal(self.audio.paused, false, "an unreachable API must never cause silence");
});

test("it gives up after the timeout instead of polling forever", async () => {
  const { self, timers } = widget({ started: false });
  self.awaitHandoff("abc123def456", -1); // already past the deadline
  await tick();
  assert.equal(self.handoffPending, null, "must stop polling");
  assert.equal(timers.size, 0);
  assert.equal(self.audio.paused, false, "and leave the music playing here");
});
