// Handing the mini player's track off to the full music-player app.
//
// Clicking the title/artist opens music-player in a new tab at the SAME
// position and stops playing here. Three things make that work, and each one
// fails quietly on its own:
//
// 1. THE POSITION MUST BE READ BEFORE THE PAUSE. Reordering those two lines
//    still opens a tab and still pauses — it just always hands over 0, and
//    only a human watching the receiving tab would notice.
// 2. THE PAUSE MUST NOT DEPEND ON window.open's RETURN. With `noopener` the
//    handle is null even on success, so `if (win) pause()` leaves both players
//    running at once.
// 3. BOTH TEMPLATE TWINS NEED THE HANDLER. factory.py renders the widget and
//    index.js re-renders it during self-heal; a card that lost its Alpine
//    attrs gets the twin, so a handler in only one is a click that works until
//    the page rehydrates.
//
// Run: `node --test tests/test_music_handoff.mjs`.

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

// ─── openInFullPlayer, lifted out of widgets.js and run for real ────────────
// Sliced rather than retyped: a copy would keep passing after the original
// changed, which is the whole point of testing it.
function extract(startMarker, endMarker) {
  const from = widgetsJs.indexOf(startMarker);
  assert.ok(from !== -1, `could not find ${startMarker} in widgets.js`);
  const to = widgetsJs.indexOf(endMarker, from + startMarker.length);
  assert.ok(to !== -1, `could not find ${endMarker} after ${startMarker}`);
  return widgetsJs.slice(from, to);
}

const handoffSrc = extract("openInFullPlayer() {", "setVolume(vol) {");
// awaitHandoff rides along in this slice; give it stubs so a test can never
// start a real 700ms poll against a real network (it leaks a live timer and
// hangs the runner).
const makeHandoff = new Function("window", "fetch", "setInterval", "clearInterval",
  `return { ${handoffSrc} };`);

// A widget mid-playback. `opened` records what window.open was handed.
function widget(overrides = {}) {
  const opened = [];
  const audio = {
    currentTime: 51.7,
    paused: false,
    pause() { this.paused = true; },
  };
  const self = {
    webBase: "http://10.0.0.16:3232",
    genreFilter: "jazz",
    audio,
    currentTrack: {
      id: "dQw4w9WgXcQ",
      title: "Pomponio",
      artist: "Bobby Hutcherson",
      isYoutube: true,
    },
    ...overrides,
  };
  const fakeWindow = { open: (url, target, features) => { opened.push({ url, target, features }); return null; } };
  Object.assign(self, makeHandoff(
    fakeWindow,
    async () => ({ ok: true, json: async () => ({ started: false }) }),
    () => 1,
    () => { },
  ));
  return { self, opened, audio };
}

test("the handoff URL carries the track and the position it was at", () => {
  const { self, opened } = widget();
  self.openInFullPlayer();

  assert.equal(opened.length, 1, "should open exactly one tab");
  const url = new URL(opened[0].url);
  assert.equal(url.origin, "http://10.0.0.16:3232");
  assert.equal(url.searchParams.get("track"), "dQw4w9WgXcQ");
  // Floored, not rounded: resuming a hair early is fine, skipping ahead is not.
  assert.equal(url.searchParams.get("t"), "51");
  assert.equal(url.searchParams.get("title"), "Pomponio");
  assert.equal(url.searchParams.get("artist"), "Bobby Hutcherson");
  assert.equal(url.searchParams.get("genre"), "jazz");
  assert.equal(url.searchParams.get("autoplay"), "1");
});

test("the position is read before the pause, not after", () => {
  // The ordering trap: pausing first leaves currentTime readable, so a
  // reversed implementation still produces a URL — just always a wrong one.
  // Pinning a NON-ZERO t is what catches it.
  const { self, opened } = widget();
  self.openInFullPlayer();
  assert.equal(new URL(opened[0].url).searchParams.get("t"), "51");
});

test("playback KEEPS GOING here until the other tab actually starts", () => {
  // Pausing on faith is what produced silence on BOTH sides: this tab stopped,
  // and the new tab never started because a cross-origin tab cannot inherit
  // the click that opened it, so Chrome refuses to autoplay there. Measured
  // live 2026-08-17 with the default autoplay policy: the receiver sat loaded
  // and paused at exactly the handed-over second until the card was clicked.
  const { self, audio, opened } = widget();
  assert.equal(audio.paused, false);
  self.openInFullPlayer();
  assert.equal(audio.paused, false, "must still be playing right after the click");
  assert.match(opened[0].features || "", /noopener/);
});

test("the link carries a rendezvous id so the other tab can report back", () => {
  const { self, opened } = widget();
  self.openInFullPlayer();
  const handoff = new URL(opened[0].url).searchParams.get("handoff");
  assert.match(handoff || "", /^[A-Za-z0-9_-]{8,64}$/, `bad handoff id: ${handoff}`);
});

test("a title or artist with URL metacharacters survives the trip", () => {
  const { self, opened } = widget({
    currentTrack: { id: "abc_123-XYZ", title: "Blue & Green?", artist: "A/B #2", isYoutube: true },
  });
  self.openInFullPlayer();
  const url = new URL(opened[0].url);
  assert.equal(url.searchParams.get("title"), "Blue & Green?");
  assert.equal(url.searchParams.get("artist"), "A/B #2");
  assert.equal(url.searchParams.get("track"), "abc_123-XYZ");
});

test("with no track loaded the click does nothing at all", () => {
  const { self, opened, audio } = widget({ currentTrack: null });
  self.openInFullPlayer();
  assert.equal(opened.length, 0, "must not open a tab for a track that isn't there");
  assert.equal(audio.paused, false, "and must not stop playback either");
});

test("a local-library track opens the app without a track param", () => {
  // Local tracks are identified by a filesystem path this app cannot resolve,
  // so there is nothing to hand over — open the app, don't fake a deep link.
  const { self, opened } = widget({
    currentTrack: { id: "x", title: "Local", artist: "Someone", isYoutube: false },
  });
  self.openInFullPlayer();
  assert.equal(opened[0].url, "http://10.0.0.16:3232");
});

test("a track at 0:00 still hands over a position", () => {
  const { self, opened } = widget({
    audio: { currentTime: 0, paused: false, pause() { this.paused = true; } },
  });
  self.openInFullPlayer();
  assert.equal(new URL(opened[0].url).searchParams.get("t"), "0");
});

// ─── both twins, and the config that reaches them ──────────────────────────

test("both template twins wire the click handler", () => {
  for (const [name, src] of [["factory.py", factoryPy], ["index.js", indexJs]]) {
    assert.match(src, /@click="openInFullPlayer\(\)"/,
      `${name} must bind the handoff click`);
  }
});

test("the server hands the widget the music-player WEB url, not just the api", () => {
  assert.match(factoryPy, /webBase: \{json_escape\(MUSIC_PLAYER_WEB_URL\)\}/,
    "factory.py must pass webBase into the Alpine component");
  assert.match(widgetsJs, /webBase: cfg\.webBase \|\|/,
    "widgets.js must default webBase when the server did not supply it");
  // :8002 is the API. Opening a browser tab there serves JSON, not the player.
  assert.match(widgetsJs, /webBase: cfg\.webBase \|\| `http:\/\/\$\{window\.location\.hostname\}:3232`/,
    "the webBase fallback must point at the web app's port");
});
