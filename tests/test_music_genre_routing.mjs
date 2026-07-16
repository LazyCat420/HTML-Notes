// Regression guard for "I asked for smooth jazz and got Burzum" AND
// "I asked for john lennon and got a reggae Johnny track".
//
// Bugs in the mini_music_player widget's init() (app/static/js/widgets.js):
//   1. matchesGenre() matched the WHOLE filter phrase, so "smooth jazz" matched
//      none of the library's jazz tracks — the filter returned empty.
//   2. On an empty filter result the widget dumped the ENTIRE library at track 0
//      — Burzum (black metal), the opposite of the request.
//   3. The genre-mix fetch timed out at 15s while a cold mix takes ~18s.
//   4. matchesGenre() used an UNBOUNDED includes(), so a "john lennon" ask
//      matched the reggae track "05 - Johnny Big Mouth.mp3" (john ⊂ Johnny) and
//      never reached the YouTube fallback. Fix: word-boundary matching, a named
//      ARTIST must match ALL words, and artists resolve from YouTube not local.
//
// Run: `node --test tests/test_music_genre_routing.mjs`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const widgetsJs = readFileSync(join(here, "..", "app", "static", "js", "widgets.js"), "utf8");

const LIBRARY = [
  { genre: "Unknown", title: "01 - Feeble Screams From Forests Unknown.mp3", artist: "01.Burzum - Aske" },
  { genre: "Unknown", title: "02 - Ea, Lord Of The Depths.mp3", artist: "01.Burzum - Aske" },
  { genre: "Rap", title: "Jazz (We've Got)", artist: "A Tribe Called Quest" },
  { genre: "Jazz", title: "So What", artist: "Miles Davis" },
];

// ── Static guards: the source must keep the fixed contract ──────────────────
test("widgets.js splits the genre filter into words (not whole-phrase match)", () => {
  assert.match(widgetsJs, /const filterWords = [^;]*split\(\/\\s\+\/\)/, "filter must be word-split");
});

test("widgets.js matches on WORD BOUNDARIES, not unbounded includes()", () => {
  assert.match(widgetsJs, /wordHit = \(hay, w\) =>/, "must use a word-boundary matcher");
  assert.match(widgetsJs, /filterWords\.some\(w => wordHit\(hay, w\)\)/, "genre matches ANY word");
  assert.match(widgetsJs, /filterWords\.every\(w => wordHit\(hay, w\)\)/, "artist matches ALL words");
});

test("widgets.js routes a named artist to YouTube, not the local library", () => {
  assert.match(widgetsJs, /if \(isArtistQuery\)[\s\S]*?ytSearch\(\)/, "artists must resolve from YouTube search");
});

test("widgets.js only dumps the full library when NO genre was specified", () => {
  assert.match(widgetsJs, /!filterWords\.length[\s\S]*?loadedTracks = allLocalTracks;/,
    "full-library fallback must be gated behind an empty filter");
});

test("widgets.js gives a cold genre mix enough time and retries once", () => {
  assert.match(widgetsJs, /fetchJson\(mixUrl, 2\d000\)/, "first mix fetch must allow >=20s for a cold cache");
  assert.match(widgetsJs, /ytData = await fetchJson\(mixUrl, \d+\);\s*\n\s*if \(!\(ytData/, "must retry the mix once on empty/timeout");
});

// ── Behavioral mirror of the fixed matchesGenre ─────────────────────────────
function wordHit(hay, w) {
  return new RegExp("\\b" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b").test(hay);
}
function matchesGenre(track, genreFilter, isArtist) {
  const filterWords = (genreFilter || "").toLowerCase().split(/\s+/).filter((w) => w.length > 2);
  if (!filterWords.length) return true;
  const hay = `${track.genre || ""} ${track.title || ""} ${track.artist || ""}`.toLowerCase();
  return isArtist ? filterWords.every((w) => wordHit(hay, w)) : filterWords.some((w) => wordHit(hay, w));
}

function resolveGenreTracks(genreFilter, allLocalTracks) {
  const filterWords = (genreFilter || "").toLowerCase().split(/\s+/).filter((w) => w.length > 2);
  let loaded = allLocalTracks.filter((t) => matchesGenre(t, genreFilter, false));
  if (loaded.length === 0 && allLocalTracks.length > 0 && !filterWords.length) {
    loaded = allLocalTracks; // bare "play music" only
  }
  return loaded;
}

test('"smooth jazz" matches the library\'s jazz tracks by the word "jazz"', () => {
  const matched = LIBRARY.filter((t) => matchesGenre(t, "smooth jazz", false));
  assert.ok(matched.length >= 2, "should reach both the Tribe track and Miles Davis via 'jazz'");
  assert.ok(matched.every((t) => !t.artist.includes("Burzum")), "must never match a Burzum track");
});

test('"smooth jazz" never falls back to the whole library (never plays Burzum)', () => {
  const played = resolveGenreTracks("smooth jazz", LIBRARY);
  assert.ok(played.length > 0, "the jazz tracks are found, so playback still works");
  assert.ok(played.every((t) => !t.artist.includes("Burzum")), "Burzum must not be in the queue for a jazz request");
});

test("a genre with genuinely no local match plays nothing rather than the whole library", () => {
  const onlyBurzum = LIBRARY.filter((t) => t.artist.includes("Burzum"));
  const played = resolveGenreTracks("smooth jazz", onlyBurzum);
  assert.equal(played.length, 0, "no jazz in this sub-library → play nothing, do NOT dump Burzum");
});

test("a bare music request (no genre) still uses the whole library", () => {
  const played = resolveGenreTracks("", LIBRARY);
  assert.equal(played.length, LIBRARY.length, "empty filter → full library is the intended behavior");
});

test("a named artist matches on WORD BOUNDARIES (john != Johnny)", () => {
  const reggae = { genre: "Reggae", title: "05 - Johnny Big Mouth.mp3", artist: "" };
  const lennon = { genre: "Rock", title: "Imagine", artist: "John Lennon" };
  // The exact reported bug: a reggae "Johnny" track must NOT satisfy "john lennon".
  assert.equal(matchesGenre(reggae, "john lennon", true), false, "'Johnny' must NOT match 'john lennon'");
  assert.equal(matchesGenre(lennon, "john lennon", true), true, "actual John Lennon must match");
  // A partial single-word artist collision is likewise rejected (needs ALL words).
  assert.equal(matchesGenre({ genre: "", title: "John Henry", artist: "Trad" }, "john lennon", true), false);
});
