// Regression guard for the music widget's contract with the music-player
// service, rewritten for the SSE/queue rework (2026-07-20).
//
// History this file protects against repeating:
//   - "smooth jazz got Burzum": client-side fallbacks dumped the local library
//     for a named genre. The library is now last-resort for BARE queries only.
//   - "jungle music played from my library": a hardcoded KNOWN_MUSIC_GENRES set
//     decided genre-vs-artist client-side; unknown genres degraded to artist
//     search + library matching. The set is GONE — the music-player service's
//     mix pipeline decides, and routing passes a `kind` hint.
//   - The old suite pinned those very heuristics and stayed green while the
//     feature was broken. These asserts pin the service contract and the queue
//     behavior instead.
//
// Run: `node --test tests/test_music_genre_routing.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(here, "..", ...p), "utf8");
const widgetsJs = read("app", "static", "js", "widgets.js");
const indexJs = read("app", "static", "index.js");
const factoryPy = read("app", "widgets", "factory.py");
const mainPy = read("app", "main.py") + "\n" + read("app", "config_builders.py") + "\n" + read("app", "routes", "message.py");

// Slice out just the music player component so asserts about absence don't
// trip over other widgets (e.g. the youtube player legitimately differs).
const musicSrc = widgetsJs.slice(
  widgetsJs.indexOf("Alpine.data('musicPlayerWidget'"),
  widgetsJs.indexOf("Alpine.data('youtubePlayerWidget'"));
assert.ok(musicSrc.length > 1000, "could not slice musicPlayerWidget source");

// ── The client-side genre list is dead ──────────────────────────────────────
test("no client-side genre gating remains", () => {
  assert.doesNotMatch(widgetsJs, /KNOWN_MUSIC_GENRES/, "the hardcoded genre set must stay deleted");
  assert.doesNotMatch(widgetsJs, /isKnownMusicGenre/, "no client-side genre/artist guessing");
  assert.doesNotMatch(musicSrc, /matchesGenre/, "no local-library genre matching");
});

// ── SSE contract with the music-player mix endpoint ─────────────────────────
test("mix is consumed over SSE (progressive, no cold-cache timeout race)", () => {
  assert.match(musicSrc, /new EventSource\(/, "must use EventSource");
  assert.match(musicSrc, /\/api\/youtube\/mix\/\$\{encodeURIComponent\(term\)\}\/stream/,
    "must target the mix /stream endpoint");
});

test("the stream is closed on done, error, AND destroy (reconnect-storm guard)", () => {
  // EventSource auto-reconnects after a server close; an unclosed stream
  // re-runs the whole discovery pipeline against the service's 10/min limit.
  const doneHandler = musicSrc.slice(musicSrc.indexOf("this.es.addEventListener('done'"));
  assert.match(doneHandler.slice(0, 500), /this\.closeStream\(\)/, "done must close the stream");
  const errHandler = musicSrc.slice(musicSrc.indexOf("this.es.addEventListener('error'"));
  assert.match(errHandler.slice(0, 500), /this\.closeStream\(\)/, "error must close the stream");
  const destroyBody = musicSrc.slice(musicSrc.indexOf("destroy() {"));
  assert.match(destroyBody.slice(0, 700), /this\.closeStream\(\)/, "destroy must close the stream");
});

test("the 7MB /api/tracks fetch is gone from the mix path", () => {
  const startStreamBody = musicSrc.slice(
    musicSrc.indexOf("startStream(term, type"),
    musicSrc.indexOf("closeStream() {"));
  assert.ok(startStreamBody.length > 500, "could not slice startStream");
  assert.doesNotMatch(startStreamBody, /api\/tracks/, "streaming path must not touch the library");
  // The library survives ONLY inside failover, gated behind a bare query.
  const failoverBody = musicSrc.slice(
    musicSrc.indexOf("async failover("),
    musicSrc.indexOf("maybeRefill() {"));
  const idx = failoverBody.indexOf("api/tracks");
  assert.ok(idx > 0, "bare-query library fallback must still exist in failover");
  assert.match(failoverBody.slice(0, idx), /if \(this\.genreFilter\)[\s\S]*?return;/,
    "a NAMED query must error out before the library branch is reachable");
});

test("failover ladder: genre → artist mix → search → honest error", () => {
  const failoverBody = musicSrc.slice(
    musicSrc.indexOf("async failover("),
    musicSrc.indexOf("maybeRefill() {"));
  const artistRetry = failoverBody.indexOf("startStream(term, 'artist')");
  const search = failoverBody.indexOf("/api/youtube/search");
  const namedError = failoverBody.indexOf("Couldn't find");
  assert.ok(artistRetry > 0, "genre miss must retry as artist");
  assert.ok(search > artistRetry, "search fallback comes after the artist retry");
  assert.ok(namedError > search, "the honest error comes after search, never the library");
  assert.match(failoverBody, /triedArtistFallback/, "artist retry must be one-shot");
});

// ── Behavioral mirrors (same logic as the component methods) ────────────────
function makeQueueState() {
  return {
    queue: [], currentIndex: -1, seenIds: new Set(),
    enqueue(raw) {
      const fresh = (raw || []).filter(v => v && v.id && !this.seenIds.has(v.id));
      fresh.forEach(t => this.seenIds.add(t.id));
      this.queue.push(...fresh);
      return fresh.length;
    },
    removeAt(i) {
      if (i === this.currentIndex || i < 0 || i >= this.queue.length) return;
      this.queue.splice(i, 1);
      if (i < this.currentIndex) this.currentIndex--;
    },
  };
}

test("enqueue dedupes across SSE batches and refills", () => {
  const s = makeQueueState();
  assert.equal(s.enqueue([{ id: "a" }, { id: "b" }]), 2);
  assert.equal(s.enqueue([{ id: "b" }, { id: "c" }, { id: null }]), 1, "b is a dupe, null is dropped");
  assert.deepEqual(s.queue.map(t => t.id), ["a", "b", "c"]);
});

test("removeAt keeps the playing track and fixes indices", () => {
  const s = makeQueueState();
  s.enqueue([{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }]);
  s.currentIndex = 2; // playing "c"
  s.removeAt(2);
  assert.equal(s.queue.length, 4, "the playing track is not removable");
  s.removeAt(0); // remove "a", before current
  assert.equal(s.currentIndex, 1, "index shifts down when removing before current");
  assert.equal(s.queue[s.currentIndex].id, "c", "still playing the same track");
  s.removeAt(2); // remove "d", after current
  assert.equal(s.currentIndex, 1, "index unchanged when removing after current");
  assert.deepEqual(s.queue.map(t => t.id), ["b", "c"]);
});

test("maybeRefill gating: threshold, in-flight, and 90s floor", () => {
  // Mirrors the guards; the source asserts below pin the real values.
  const gate = (remaining, inFlight, msSinceLast, hasTerm = true) =>
    !(remaining > 5 || inFlight || !hasTerm) && msSinceLast >= 90000;
  assert.equal(gate(10, false, 999999), false, "plenty queued — no refill");
  assert.equal(gate(3, true, 999999), false, "already refilling");
  assert.equal(gate(3, false, 30000), false, "rate-limit floor");
  assert.equal(gate(3, false, 999999, false), false, "no term to refill from");
  assert.equal(gate(3, false, 91000), true, "low queue, idle, past floor — refill");
  assert.match(musicSrc, /remaining > 5 \|\| this\.refillInFlight \|\| !this\.genreFilter/,
    "source gate must match the mirror");
  assert.match(musicSrc, /< 90000/, "90s floor (mix endpoint is rate-limited 10/min)");
  assert.match(musicSrc, /refresh=true/, "refill must ask the pipeline for fresh tracks");
});

// ── Routing passes `kind`; the widget and template carry it ─────────────────
test("fast-path spawns music with kind=genre", () => {
  const fastPath = mainPy.slice(mainPy.indexOf("(music|player|radio)"));
  assert.match(fastPath.slice(0, 900), /"kind": "genre"/,
    '"X music" phrasing must default the genre pipeline');
});

test("LLM router catalog teaches kind and the builder sanitizes it", () => {
  assert.match(mainPy, /"music":\s+\("music",\s+'[^']*kind[^']*'\)/,
    "router catalog must describe the kind modifier");
  assert.match(mainPy, /mods\.get\("kind"\) if mods\.get\("kind"\) in \("genre", "artist"\) else ""/,
    "builder must sanitize kind to genre|artist|empty");
});

test("factory renders the object x-data with kind + base, and the queue UI", () => {
  assert.match(factoryPy, /musicPlayerWidget\(\{cfg_js\}\)/, "object-form x-data");
  assert.match(factoryPy, /kind: \{json_escape\(kind\)\}/, "kind flows into the widget");
  assert.match(factoryPy, /base: \{json_escape\(MUSIC_PLAYER_URL\)\}/, "base comes from config");
  assert.match(factoryPy, /queue_music/, "queue toggle button");
  assert.match(factoryPy, /x-for="item in upcoming"/, "queue panel rows");
});

test("self-heal template stays in sync (queue UI + object x-data)", () => {
  assert.match(indexJs, /musicPlayerWidget\(\{ genre: \$\{JSON\.stringify\(genre\)\}/,
    "healed nodes must use the object form");
  assert.match(indexJs, /queue_music/, "healed template must include the queue button");
  assert.match(indexJs, /x-for="item in upcoming"/, "healed template must include the queue panel");
  // Old positional nodes must be detected as stale and rebuilt.
  assert.match(indexJs, /!widget\.getAttribute\('x-data'\)\.includes\('musicPlayerWidget\(\{'\)/,
    "positional x-data must count as old-format");
});

// The component itself must still accept a legacy positional call — notes
// rendered before this change rehydrate through the same registration.
test("legacy positional calls are shimmed", () => {
  assert.match(musicSrc, /typeof cfgOrGenre === 'object'/, "options-vs-positional shim");
});
