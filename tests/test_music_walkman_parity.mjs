import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test, mock } from "node:test";
import { fileURLToPath } from "node:url";
import { loadFactory, mount, streams } from "./_music_widget_harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const rootDir = join(here, "..");
const walkmanHtml = readFileSync(join(rootDir, "app", "static", "walkman", "player.html"), "utf8");

function withTimers(fn) {
  return async () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      await fn();
    } finally {
      mock.timers.reset();
    }
  };
}

async function seedTwoArtists(env, opts = {}) {
  const w = env.factory({
    genre: "house",
    autoplay: true,
    base: "http://svc",
    wallgardenUrl: opts.wallgardenUrl || "http://10.0.0.99:8007",
    streamTicket: opts.streamTicket || "",
  });
  w.$el = { dataset: {} };
  await w.init();
  streams.at(-1).fire("tracks", {
    tracks: [
      { id: "v1", title: "Track One", artist: "Artist A" },
      { id: "v2", title: "Track Two", artist: "Artist A" },
      { id: "v3", title: "Track Three", artist: "Artist B" },
    ],
  });
  return w;
}

test("Walkman card drawer includes MIX, MORE, ENDLESS, BLOCK inside #queue without altering deck geometry", () => {
  const queueStart = walkmanHtml.indexOf('<div id="queue">');
  const queueEnd = walkmanHtml.indexOf('<div id="qrows">', queueStart);
  assert.ok(queueStart !== -1 && queueEnd > queueStart, "#queue bar missing");
  const drawerBar = walkmanHtml.slice(queueStart, queueEnd);
  for (const id of ['id="qsearch"', 'id="qmix"', 'id="qmore"', 'id="qendless"', 'id="qblock"']) {
    assert.ok(drawerBar.includes(id), `drawer missing ${id}`);
  }
  const keyPositions = [...walkmanHtml.matchAll(/slot="hotspot-k\d"\s+data-position="([^"]+)"/g)].map(m => m[1]);
  assert.equal(keyPositions.length, 5);
  for (const pos of keyPositions) {
    assert.ok(pos.endsWith(" -0.0535 0.0205"), `unexpected deck key position ${pos}`);
  }
  assert.match(walkmanHtml, /if\s*\(first\)\s*\{\s*click\(true\);\s*load\(Number\(first\.dataset\.i\)\);\s*return;\s*\}/);
  assert.match(walkmanHtml, /emit\(\{\s*action:\s*'request_mix'/);
  assert.match(walkmanHtml, /emit\(\{\s*action:\s*'more_like'/);
  assert.match(walkmanHtml, /emit\(\{\s*action:\s*'toggle_endless'/);
  assert.match(walkmanHtml, /emit\(\{\s*action:\s*'block_artist'/);
});

test("requestMix restarts mix stream for user query and clears prior queue", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env);
  const posted = [];
  const win = { postMessage(msg) { posted.push(msg); } };
  w.$refs = { walkman: { contentWindow: win } };
  w.onWalkmanLoad();

  w.onWalkmanMessage({ source: win, data: { type: "walkman", action: "request_mix", query: "amapiano" } });
  assert.equal(w.genreFilter, "amapiano");
  assert.equal(w.queue.length, 0);
  assert.ok(streams.at(-1).url.includes("/api/youtube/mix/amapiano/stream?type=genre"));
  w.destroy();
}));

test("moreLikeCurrent requests endless next-batch with seed_track_id and seed_artist", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env);
  const seenUrls = [];
  w.fetchJson = async (url) => {
    seenUrls.push(url);
    return { tracks: [{ id: "v4", title: "Followup", artist: "Artist C", duration: 210 }] };
  };
  await w.moreLikeCurrent();
  assert.equal(seenUrls.length, 1);
  assert.match(seenUrls[0], /\/api\/music\/history\/smart\/endless-mix\/next-batch\?/);
  assert.match(seenUrls[0], /seed_track_id=youtube%3A%2F%2Fv1/);
  assert.match(seenUrls[0], /seed_artist=Artist\+A/);
  assert.equal(w.queue.length, 4);
  assert.equal(w.queue[3].id, "v4");
  w.destroy();
}));

test("toggleEndless refills at queue end and falls back to wrap if refill returns 0 (R1-F1, R1-F3)", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env);
  w.currentIndex = w.queue.length - 1;
  let moreCalls = 0;
  w.moreLikeCurrent = async ({ autoPlayNext, resumePlayback } = {}) => {
    moreCalls++;
    assert.equal(autoPlayNext, true);
    assert.equal(resumePlayback, true);
    return 0;
  };
  assert.equal(w.isEndless, true);
  w.nextTrack({ auto: true });
  await Promise.resolve();
  assert.equal(moreCalls, 1, "endless mode tries refill at end");
  assert.equal(w.currentIndex, 0, "when refill returns 0, wraps instead of silence");

  w.toggleEndless(false);
  assert.equal(w.isEndless, false);
  w.currentIndex = w.queue.length - 1;
  w.nextTrack({ auto: true });
  assert.equal(w.currentIndex, 0, "when endless is off, nextTrack wraps cleanly");

  // R1-F3: successful refill advances and resumes via resumePlayback even if autoplay=false
  const wNoAuto = env.factory({ genre: "house", autoplay: false, base: "http://svc" });
  wNoAuto.$el = { dataset: {} };
  await wNoAuto.init();
  streams.at(-1).fire("tracks", { tracks: [{ id: "x1", title: "T1", artist: "A" }] });
  let playedCount = 0;
  const origPlay = wNoAuto.audio.play.bind(wNoAuto.audio);
  wNoAuto.audio.play = () => { playedCount++; return origPlay(); };
  wNoAuto.isPlaying = true;
  wNoAuto.fetchJson = async () => ({ tracks: [{ id: "x2", title: "T2", artist: "B", duration: 180 }] });
  wNoAuto.nextTrack({ auto: false });
  await new Promise(r => setImmediate(r));
  assert.equal(wNoAuto.currentIndex, 1);
  assert.equal(wNoAuto.currentTrack.id, "x2");
  assert.equal(playedCount, 1, "audio.play() must fire using shouldResume even when autoplay=false");
  w.destroy();
  wNoAuto.destroy();
}));

test("blockCurrentArtist posts target=artist, flushes skip, normalizes Topic channels, and seeds non-blocked tracks (R1-F5, R2-F4..F6)", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env);
  w.activePlayAccumulated = 12;
  w.blockCurrentArtist();
  const fb = env.fetchCalls.find(c => c.url.endsWith("/api/radio/feedback") && c.method === "POST");
  assert.ok(fb, "must POST /api/radio/feedback");
  assert.equal(fb.body.target, "artist");
  assert.equal(fb.body.sentiment, "disliked");
  assert.equal(fb.body.artist, "Artist A");
  assert.ok(env.fetchCalls.some(c => c.url.endsWith("/api/music/history") && c.body.end_reason === "skip"), "R2-F5 skip flushed");
  assert.deepEqual(w.queue.map(t => t.artist), ["Artist A", "Artist B"]);
  assert.equal(w.currentTrack.id, "v3", "must advance past blocked artist onto Artist B");
  // R1-F5 + R2-F4: subsequent refills filter both exact and '- Topic' variants
  const added = w.enqueue([
    { id: "v99", title: "Blocked Return", artist: "Artist A - Topic" },
    { id: "v100", title: "Allowed", artist: "Artist C" },
  ]);
  assert.equal(added, 1);
  assert.ok(!w.queue.some(t => t.id === "v99"));

  // R2-F6: blocking the last track must not seed next-batch with the blocked artist
  w.currentIndex = w.queue.length - 1; // v100 by Artist C (last track)
  const refillUrls = [];
  w.fetchJson = async (url) => { refillUrls.push(url); return { tracks: [] }; };
  w.blockCurrentArtist();
  await new Promise(r => setImmediate(r));
  assert.equal(refillUrls.length, 1);
  assert.ok(!refillUrls[0].includes("seed_artist=Artist+C"));
  w.destroy();
}));

test("requestMix bumps mixGeneration so stale refills discard without wrapping new mix (R1-F4, R2-F2, R2-F3)", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env);
  w.currentIndex = w.queue.length - 1;
  let resolveSlow = null;
  w.fetchJson = () => {
    if (!resolveSlow) return new Promise(res => { resolveSlow = res; });
    return Promise.resolve({ tracks: [] });
  };
  w.nextTrack({ auto: true });
  w.requestMix("techno");
  streams.at(-1).fire("tracks", {
    tracks: [
      { id: "t1", title: "Techno 1", artist: "DJ1" },
      { id: "t2", title: "Techno 2", artist: "DJ2" },
    ],
  });
  resolveSlow({ tracks: [{ id: "old1", title: "Stale", artist: "Old" }] });
  await new Promise(r => setImmediate(r));
  assert.equal(w.genreFilter, "techno");
  assert.equal(w.currentTrack.id, "t1", "R2-F3: stale refill must NOT wrap new mix onto t2");
  assert.ok(!w.queue.some(t => t.id === "old1"));
  w.destroy();
}));

test("sendThumb('down') sends target=song to radio/feedback without banning the artist", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env, { wallgardenUrl: "http://10.0.0.77:8007" });
  w.sendThumb("down");
  const fb = env.fetchCalls.find(c => c.url.endsWith("/api/radio/feedback"));
  assert.ok(fb);
  assert.equal(fb.body.target, "song");
  assert.equal(fb.body.sentiment, "disliked");
  assert.equal(w.queue.filter(t => t.artist === "Artist A").length, 2, "song dislike must keep artist tracks");
  const wg = env.fetchCalls.find(c => c.url === "http://10.0.0.77:8007/sync/global");
  assert.ok(wg, "must honor configured wallgardenUrl");
  w.destroy();
}));

test("streamTicket appends ?ticket= query parameter on YouTube stream URLs", withTimers(async () => {
  const env = loadFactory();
  const w = await seedTwoArtists(env, { streamTicket: "tok-123" });
  assert.equal(w.audio.src, "http://svc/api/youtube/stream/v1?ticket=tok-123");
  const m = await mount(env.factory, "jazz");
  assert.equal(m.streamTicket, "");
  w.destroy();
  m.destroy();
}));
