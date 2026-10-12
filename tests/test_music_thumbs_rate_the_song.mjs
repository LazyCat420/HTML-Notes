// Thumbs in the music widget rate the SONG that is playing, not its artist.
//
// User report 2026-10-11: "when thumbing up a song it should be the song not
// the artist". The widget stored the rating per ARTIST (liking one song lit
// the thumb on every song by them) and fed music-player only an artist-level
// radio preference plus an artist graph node.
//
// Runs the real musicPlayerWidget (see _music_widget_harness.mjs).
// Run: `node --test tests/test_music_thumbs_rate_the_song.mjs`.

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { streams, loadFactory, mount } from "./_music_widget_harness.mjs";

async function playing(env) {
  const w = await mount(env.factory, "jazz");
  streams.at(-1).fire("tracks", { tracks: [
    { id: "song1", title: "So What", artist: "Miles Davis" },
    { id: "song2", title: "Blue in Green", artist: "Miles Davis" },
  ] });
  return w;
}

function withTimers(fn) {
  return async () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try { await fn(); } finally { mock.timers.reset(); }
  };
}

test("thumbs up saves THIS song to music-player favorites", withTimers(async () => {
  const env = loadFactory();
  const w = await playing(env);
  w.sendThumb("up");
  const fav = env.fetchCalls.find(c => c.url.endsWith("/api/favorites") && c.method === "POST");
  assert.ok(fav, "the song must be POSTed to favorites");
  assert.equal(fav.body.path, "youtube://song1");
  assert.equal(fav.body.youtube_id, "song1");
  assert.equal(fav.body.title, "So What");
  assert.equal(fav.body.source, "youtube");
}));

test("no artist-level preference or artist node is written", withTimers(async () => {
  const env = loadFactory();
  const w = await playing(env);
  w.sendThumb("up");
  w.nextTrack();
  w.sendThumb("down");
  const artistLevel = env.fetchCalls.filter(c => /radio\/preference|artists\/add-node/.test(c.url));
  assert.deepEqual(artistLevel, []);
}));

test("liking one song does not mark the artist's other songs", withTimers(async () => {
  const env = loadFactory();
  const w = await playing(env);
  w.sendThumb("up");
  assert.equal(w.thumbState, "up");
  w.nextTrack();
  w.syncThumbState(w.currentTrack);     // x-effect on track change
  assert.equal(w.currentTrack.id, "song2");
  assert.equal(w.thumbState, null, "another song by the same artist is unrated");
  w.prevTrack();
  w.syncThumbState(w.currentTrack);
  assert.equal(w.thumbState, "up", "and the liked song remembers its rating");
}));

test("thumbs down (or clearing a like) takes the song back out of favorites", withTimers(async () => {
  const env = loadFactory();
  const w = await playing(env);
  w.sendThumb("up");
  w.sendThumb("down");
  const del = env.fetchCalls.find(c => c.method === "DELETE" && c.url.endsWith("/api/favorites"));
  assert.ok(del);
  assert.equal(del.body.path, "youtube://song1");
}));

test("wallgarden still gets the per-video rating", withTimers(async () => {
  const env = loadFactory();
  const w = await playing(env);
  w.sendThumb("down");
  const wg = env.fetchCalls.find(c => c.url.includes(":8007/sync/global"));
  assert.ok(wg);
  assert.equal(wg.body.fields.ratings.song1.r, -5);
}));
