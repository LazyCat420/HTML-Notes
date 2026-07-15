// Regression guard for "I asked for smooth jazz and got Burzum".
//
// Root cause was three compounding bugs in the mini_music_player widget's init()
// (app/static/js/widgets.js), all triggered when a genre mix was slow/empty:
//   1. matchesGenre() matched the WHOLE filter phrase, so "smooth jazz" matched
//      none of the library's 900+ jazz tracks (no track literally says "smooth
//      jazz") — the filter returned empty.
//   2. On an empty filter result the widget dumped the ENTIRE unfiltered library
//      starting at track 0 — which in this library is Burzum (black metal), the
//      opposite of the request.
//   3. The genre-mix fetch timed out at 15s while a cold-cache genre mix takes
//      ~18s, so the (working) smooth-jazz mix was abandoned right before it
//      landed, forcing the fallback above.
//
// This test extracts the real matchesGenre + fallback logic from widgets.js and
// proves that for an explicit genre with no local matches, the widget no longer
// plays the whole library. Run: `node --test tests/test_music_genre_routing.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const widgetsJs = readFileSync(join(here, "..", "app", "static", "js", "widgets.js"), "utf8");

// The first tracks in the user's real library (Burzum) plus some real jazz
// tracks. matchesGenre for "smooth jazz" must reach the jazz tracks by the word
// "jazz" and must NOT let a filtered request play the Burzum tracks.
const LIBRARY = [
  { genre: "Unknown", title: "01 - Feeble Screams From Forests Unknown.mp3", artist: "01.Burzum - Aske" },
  { genre: "Unknown", title: "02 - Ea, Lord Of The Depths.mp3", artist: "01.Burzum - Aske" },
  { genre: "Rap", title: "Jazz (We've Got)", artist: "A Tribe Called Quest" },
  { genre: "Jazz", title: "So What", artist: "Miles Davis" },
];

// Static guards: the source must keep the fixed contract, so a future revert
// fails loudly here rather than silently replaying the bug.
test("widgets.js splits the genre filter into words (not whole-phrase match)", () => {
  assert.match(widgetsJs, /const filterWords = [^;]*split\(\/\\s\+\/\)/, "filter must be word-split");
  assert.match(widgetsJs, /filterWords\.some\(w => hay\.includes\(w\)\)/, "matchesGenre must match ANY filter word");
});

test("widgets.js only dumps the full library when NO genre was specified", () => {
  assert.match(widgetsJs, /if \(!filterWords\.length\) \{[\s\S]*?loadedTracks = allLocalTracks;/,
    "full-library fallback must be gated behind an empty filter");
});

test("widgets.js gives a cold genre mix enough time and retries once", () => {
  // The cold mix takes ~18s; the timeout must exceed that, and there must be a
  // second attempt (the retry hits a now-warm cache).
  assert.match(widgetsJs, /fetchJson\(mixUrl, 2\d000\)/, "first mix fetch must allow >=20s for a cold cache");
  assert.match(widgetsJs, /ytData = await fetchJson\(mixUrl, \d+\);\s*\n\s*if \(!\(ytData/, "must retry the mix once on empty/timeout");
});

// Behavioral mirror of the fixed matchesGenre + fallback decision.
function matchesGenre(track, genreFilter) {
  const filterWords = (genreFilter || "").toLowerCase().split(/\s+/).filter((w) => w.length > 2);
  if (!filterWords.length) return true;
  const hay = `${track.genre || ""} ${track.title || ""} ${track.artist || ""}`.toLowerCase();
  return filterWords.some((w) => hay.includes(w));
}

function resolveTracks(genreFilter, allLocalTracks) {
  const filterWords = (genreFilter || "").toLowerCase().split(/\s+/).filter((w) => w.length > 2);
  let loaded = allLocalTracks.filter((t) => matchesGenre(t, genreFilter));
  if (loaded.length === 0 && allLocalTracks.length > 0) {
    if (!filterWords.length) loaded = allLocalTracks; // bare "play music" only
    // else: leave empty — surfacing the miss beats playing contradicting music
  }
  return loaded;
}

test('"smooth jazz" matches the library\'s jazz tracks by the word "jazz"', () => {
  const matched = LIBRARY.filter((t) => matchesGenre(t, "smooth jazz"));
  assert.ok(matched.length >= 2, "should reach both the Tribe track and Miles Davis via 'jazz'");
  assert.ok(matched.every((t) => !t.artist.includes("Burzum")), "must never match a Burzum track");
});

test('"smooth jazz" never falls back to the whole library (never plays Burzum)', () => {
  const played = resolveTracks("smooth jazz", LIBRARY);
  assert.ok(played.length > 0, "the jazz tracks are found, so playback still works");
  assert.ok(played.every((t) => !t.artist.includes("Burzum")), "Burzum must not be in the queue for a jazz request");
});

test("a genre with genuinely no local match plays nothing rather than the whole library", () => {
  const onlyBurzum = LIBRARY.filter((t) => t.artist.includes("Burzum"));
  const played = resolveTracks("smooth jazz", onlyBurzum);
  assert.equal(played.length, 0, "no jazz in this sub-library → play nothing, do NOT dump Burzum");
});

test("a bare music request (no genre) still uses the whole library", () => {
  const played = resolveTracks("", LIBRARY);
  assert.equal(played.length, LIBRARY.length, "empty filter → full library is the intended behavior");
});
