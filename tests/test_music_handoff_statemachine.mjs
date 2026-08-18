/**
 * Comprehensive State-Machine Unit Tests for Music Handoff Synchronization (V1).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { HANDOFF_STATES, createHandoffPayload } from "../app/static/js/music/music-contract.js";
import { MusicQueue } from "../app/static/js/music/queue.js";
import { PlayabilityChecker } from "../app/static/js/music/playability.js";
import { HandoffStateMachine } from "../app/static/js/music/handoff.js";

// Mock fetch factory
function createMockFetch(routes = {}) {
  return async (url, options = {}) => {
    const method = options.method || "GET";
    const u = new URL(url, "http://localhost");
    const pathname = u.pathname;

    for (const [key, handler] of Object.entries(routes)) {
      const [m, p] = key.split(" ");
      if (m === method && pathname.startsWith(p)) {
        return handler(url, options);
      }
    }
    return new Response(JSON.stringify({ error: "Not found" }), { status: 404 });
  };
}

test("HandoffStateMachine: initialization and payload creation", () => {
  const payload = createHandoffPayload({
    handoffId: "h123",
    trackId: "yt_test1",
    position: 12.5,
    title: "Test Title",
    artist: "Test Artist",
    genre: "lofi",
  });

  assert.equal(payload.version, 1);
  assert.equal(payload.handoffId, "h123");
  assert.equal(payload.track, "yt_test1");
  assert.equal(payload.position, 12.5);
  assert.equal(payload.state, HANDOFF_STATES.SENDER_WAITING);
});

test("HandoffStateMachine: autoplay blocked keep-playing & position updates", async () => {
  const puts = [];
  const mockFetch = createMockFetch({
    "PUT /api/handoff/": async (url, opts) => {
      puts.push(JSON.parse(opts.body));
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
    "GET /api/handoff/": async () => {
      return new Response(JSON.stringify({ found: true, started: false }), { status: 200 });
    },
  });

  const machine = new HandoffStateMachine({
    baseUrl: "http://localhost",
    pollIntervalMs: 20,
    timeoutMs: 500,
    fetchFn: mockFetch,
  });

  const hid = machine.startHandoff({ trackId: "yt_track1", position: 5.0 });
  assert.ok(hid.length > 5);
  assert.equal(machine.state, HANDOFF_STATES.SENDER_WAITING);

  // Publish live position update
  await machine.publishPosition({ trackId: "yt_track1", position: 7.2 });
  assert.equal(puts.length, 2);
  assert.equal(puts[1].position, 7.2);

  machine.cancelHandoff();
  assert.equal(machine.state, HANDOFF_STATES.CANCELLED);
});

test("HandoffStateMachine: receiver confirmed audio flowing triggers pause callback", async () => {
  let confirmedData = null;
  let getCount = 0;

  const mockFetch = createMockFetch({
    "PUT /api/handoff/": async () => {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
    "GET /api/handoff/": async () => {
      getCount++;
      if (getCount >= 2) {
        return new Response(JSON.stringify({ found: true, started: true, track: "yt_track1", started_position: 10.5 }), { status: 200 });
      }
      return new Response(JSON.stringify({ found: true, started: false }), { status: 200 });
    },
  });

  const machine = new HandoffStateMachine({
    baseUrl: "http://localhost",
    pollIntervalMs: 10,
    timeoutMs: 1000,
    fetchFn: mockFetch,
    onConfirmed: (data) => {
      confirmedData = data;
    },
  });

  machine.startHandoff({ trackId: "yt_track1", position: 10.0 });

  // Wait for polling loop to execute
  await new Promise((r) => setTimeout(r, 50));

  assert.equal(machine.state, HANDOFF_STATES.RECEIVER_PLAYING);
  assert.ok(confirmedData !== null);
  assert.equal(confirmedData.started_position, 10.5);
});

test("HandoffStateMachine: track mismatch ignores premature start confirmation", async () => {
  let confirmed = false;

  const mockFetch = createMockFetch({
    "PUT /api/handoff/": async () => new Response(JSON.stringify({ ok: true }), { status: 200 }),
    "GET /api/handoff/": async () => {
      return new Response(JSON.stringify({ found: true, started: true, track: "yt_WRONG_TRACK" }), { status: 200 });
    },
  });

  const machine = new HandoffStateMachine({
    baseUrl: "http://localhost",
    pollIntervalMs: 10,
    timeoutMs: 200,
    fetchFn: mockFetch,
    onConfirmed: () => {
      confirmed = true;
    },
  });

  machine.startHandoff({ trackId: "yt_RIGHT_TRACK", position: 0 });
  await new Promise((r) => setTimeout(r, 40));

  assert.equal(confirmed, false);
  assert.equal(machine.state, HANDOFF_STATES.SENDER_WAITING);
  machine.cancelHandoff();
});

test("PlayabilityChecker: YouTube probe & bounded lookahead", async () => {
  const probed = [];
  const mockFetch = async (url) => {
    const u = new URL(url);
    const id = u.pathname.split("/").pop();
    probed.push(id);
    if (id === "dead1" || id === "dead2") {
      return new Response(JSON.stringify({ video_id: id, playable: false }), { status: 200 });
    }
    return new Response(JSON.stringify({ video_id: id, playable: true }), { status: 200 });
  };

  const checker = new PlayabilityChecker({ baseUrl: "http://localhost", maxLookahead: 3 });

  const queue = [{ id: "yt_dead1" }, { id: "yt_dead2" }, { id: "yt_good1" }, { id: "yt_good2" }];
  const playableIdx = await checker.settleOnPlayable(queue, 0, mockFetch);

  assert.equal(playableIdx, 2); // Landed on yt_good1
  assert.deepEqual(probed, ["dead1", "dead2", "good1"]);
});

test("MusicQueue: deduplication, queue navigation, and refill logic", () => {
  const q = new MusicQueue();
  assert.equal(q.enqueue([{ id: "t1" }, { id: "t2" }, { id: "t1" }]), 2);
  assert.equal(q.length, 2);

  assert.equal(q.next().id, "t1");
  assert.equal(q.currentIndex, 0);
  assert.equal(q.currentTrack.id, "t1");

  assert.equal(q.removeAt(0), false); // Cannot remove currently playing track
  assert.equal(q.next().id, "t2");
  assert.equal(q.currentIndex, 1);

  // Refill check: 2 items total, 0 remaining after current, idle -> should refill
  assert.equal(q.shouldRefill({ threshold: 5, inFlight: false, msSinceLastRefill: 100000 }), true);
  // If in flight -> should not refill
  assert.equal(q.shouldRefill({ threshold: 5, inFlight: true, msSinceLastRefill: 100000 }), false);
});
