// Dead tracks should be dropped BEFORE the listener hears them.
//
// About three quarters of YouTube tracks currently serve only their first
// ~1MB (music-player/AUDIO_PIPELINE.md item 4). handleStreamError already
// recovers from that, but only AFTER a track has failed — which the listener
// hears as a stutter between songs. pruneAhead probes the upcoming few via
// the music-player API and quietly removes the ones that will not play.
//
// The failure modes worth pinning are all about NOT throwing away good music:
// an unreachable probe, a non-JSON response, or a slow one must leave the
// queue alone, because a wrongly-pruned track is silently unrecoverable.
//
// Run: `node --test tests/test_music_prune_dead_tracks.mjs`.

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
  assert.ok(to !== -1, `could not find ${endMarker}`);
  return widgetsJs.slice(from, to);
}

const src = extract("async pruneAhead(", "        // A stream the CDN will not serve");
const make = new Function("fetch", "setTimeout", "clearTimeout", "AbortController", `return { ${src} };`);

function widget({ dead = [], failFetch = false, badJson = false } = {}) {
  const asked = [];
  const fakeFetch = async (url) => {
    const id = url.split("/").pop();
    asked.push(id);
    if (failFetch) throw new Error("network down");
    if (badJson) return { ok: true, json: async () => { throw new Error("not json"); } };
    return { ok: true, json: async () => ({ video_id: id, playable: !dead.includes(id) }) };
  };
  const self = {
    base: "http://svc:8002",
    currentIndex: 0,
    pruneInFlight: false,
    queue: ["a", "b", "c", "d", "e"].map(id => ({ id, isYoutube: true })),
  };
  Object.assign(self, make(fakeFetch, (fn, ms) => 0, () => { }, class { abort() { } signal = null; }));
  return { self, asked };
}

const ids = w => w.queue.map(t => t.id);

test("a dead track is gone before it can be heard", async () => {
  const { self } = widget({ dead: ["b", "d"] });
  await self.pruneAhead();
  assert.deepEqual(ids(self), ["a", "c", "e"]);
});

test("back-to-back dead tracks are BOTH removed", async () => {
  // The splice makes the next track slide into the slot just vacated, so a
  // loop that keeps advancing skips it. Non-adjacent dead tracks pass either
  // way, which is why this case is pinned explicitly.
  const { self } = widget({ dead: ["b", "c"] });
  await self.pruneAhead();
  assert.deepEqual(ids(self), ["a", "d", "e"]);
});

test("it never removes the track that is playing right now", async () => {
  // currentIndex is the one in the listener's ears; pruning it would cut the
  // music off mid-song to fix a problem they do not have.
  const { self } = widget({ dead: ["a", "b"] });
  await self.pruneAhead();
  assert.equal(self.queue[0].id, "a", "the current track must survive");
  assert.ok(!ids(self).includes("b"));
});

test("an unreachable probe leaves the queue exactly as it was", async () => {
  const { self } = widget({ dead: ["b", "c", "d"], failFetch: true });
  await self.pruneAhead();
  assert.deepEqual(ids(self), ["a", "b", "c", "d", "e"], "a blip must not empty the queue");
});

test("a malformed response also leaves the queue alone", async () => {
  const { self } = widget({ dead: ["b"], badJson: true });
  await self.pruneAhead();
  assert.deepEqual(ids(self), ["a", "b", "c", "d", "e"]);
});

test("it looks a bounded distance ahead, not over the whole queue", async () => {
  const { self, asked } = widget({});
  await self.pruneAhead(2);
  assert.equal(asked.length, 2, `probed ${asked.length} tracks, expected 2`);
  assert.deepEqual(asked, ["b", "c"]);
});

test("each track is probed once, however often pruning runs", async () => {
  const { self, asked } = widget({});
  await self.pruneAhead(4);
  await self.pruneAhead(4);
  assert.equal(new Set(asked).size, asked.length, `re-probed: ${asked}`);
});

test("a second pruning pass cannot run while one is in flight", async () => {
  // Two overlapping passes splice the same array from under each other.
  const { self } = widget({});
  self.pruneInFlight = true;
  await self.pruneAhead();
  assert.deepEqual(ids(self), ["a", "b", "c", "d", "e"]);
});

test("local-library tracks are left alone — the probe is YouTube-only", async () => {
  const { self, asked } = widget({});
  self.queue[1] = { id: "local1", isYoutube: false };
  await self.pruneAhead(2);
  assert.ok(!asked.includes("local1"));
  assert.ok(ids(self).includes("local1"));
});
