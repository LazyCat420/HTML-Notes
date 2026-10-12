// A new music ask must CHANGE the music, not stop it.
//
// User report 2026-10-11: "when searching for music if there is already music
// playing it should only change the music playing, not stop the music and
// have silence". A new ask swaps the singleton music widget: the old
// component's destroy() paused its audio at once, and the new widget then
// spent 5-20 s (minutes on a cold genre) discovering its mix in silence.
//
// Now the outgoing widget PARKS its audible player; the replacement adopts it
// and stops it only when its own first track is actually playing. A user
// dismissal still stops everything at once.
//
// These tests run the real musicPlayerWidget from widgets.js (fake Alpine,
// Audio and EventSource) — not a copy and not a regex over the source.
//
// Run: `node --test tests/test_music_swap_keeps_playing.mjs`.

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const widgetsJs = readFileSync(join(here, "..", "app", "static", "js", "widgets.js"), "utf8");

class FakeAudio {
  constructor() { this.listeners = {}; this.paused = true; this._src = ""; this.volume = 1; this.currentTime = 0; }
  get src() { return this._src; }
  set src(v) { this._src = v; if (!v) this.paused = true; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  emit(t) { (this.listeners[t] || []).forEach(f => f({})); }
  play() { this.paused = false; this.emit("play"); return Promise.resolve(); }
  pause() { if (!this.paused) { this.paused = true; this.emit("pause"); } }
}

const streams = [];
class FakeEventSource {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; streams.push(this); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  fire(t, data) { (this.listeners[t] || []).forEach(f => f({ data: JSON.stringify(data) })); }
  close() { this.closed = true; }
}

function loadFactory() {
  const registry = {};
  let onInit = null;
  const fakeDocument = {
    addEventListener(ev, cb) { if (ev === "alpine:init") onInit = cb; },
    createElement() { return {}; },
    head: { appendChild() {} },
  };
  const fakeAlpine = { data(name, factory) { registry[name] = factory; } };
  const store = new Map();
  const fakeWindow = {
    location: { hostname: "nas", href: "http://nas/" },
    open() { return null; },
  };
  const env = {
    document: fakeDocument, Alpine: fakeAlpine, window: fakeWindow,
    Audio: FakeAudio, EventSource: FakeEventSource,
    localStorage: { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
    fetch: async () => ({ ok: false, json: async () => ({}) }),
  };
  const run = new Function(...Object.keys(env), widgetsJs);
  run(...Object.values(env));
  onInit();
  return { factory: registry.musicPlayerWidget, window: fakeWindow };
}

async function mount(factory, genre) {
  const w = factory({ genre, autoplay: true, base: "http://svc" });
  w.$el = { dataset: {} };
  await w.init();
  return w;
}

const track = (id) => ({ id, title: `T ${id}`, artist: "A" });

async function playingWidget(factory, genre = "jazz") {
  const w = await mount(factory, genre);
  streams.at(-1).fire("tracks", { tracks: [track("j1"), track("j2")] });
  assert.equal(w.audio.paused, false, "precondition: first widget is playing");
  w.audio.emit("playing");
  return w;
}

test("a new music ask keeps the old music playing until the new track is audible", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { factory } = loadFactory();
    const oldW = await playingWidget(factory);
    const oldAudio = oldW.audio;

    oldW.destroy();                                  // the canvas swap tears it down
    const newW = await mount(factory, "darkwave");   // ...and mounts the replacement
    mock.timers.tick(10000);                         // discovery takes a while

    assert.equal(oldAudio.paused, false, "old music must keep playing during discovery");
    assert.ok(newW.carry, "the replacement adopted the playing music");

    streams.at(-1).fire("tracks", { tracks: [track("d1")] });
    assert.equal(oldAudio.paused, false, "a LOADING new track is not yet sound — no gap");
    newW.audio.emit("playing");                       // new track audible
    assert.equal(oldAudio.paused, true, "old music stops once the new track plays");
    assert.equal(newW.carry, null);
  } finally {
    mock.timers.reset();
  }
});

test("if the new search finds nothing, the old music keeps playing", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { factory } = loadFactory();
    const oldW = await playingWidget(factory);
    const oldAudio = oldW.audio;
    oldW.destroy();
    const newW = await mount(factory, "nonexistent genre zz");
    newW.error = "Couldn't find it";
    mock.timers.tick(120000);
    assert.equal(oldAudio.paused, false);
    // ...and the new widget's play button controls what the user hears.
    newW.playPause();
    assert.equal(oldAudio.paused, true);
  } finally {
    mock.timers.reset();
  }
});

test("closing the music widget still stops the sound immediately", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { factory, window } = loadFactory();
    const w = await playingWidget(factory);
    const audio = w.audio;
    w.$el.dataset.userDismissed = "1";   // WidgetManager.dismiss marks the element
    w.destroy();
    assert.equal(audio.paused, true);
    assert.equal(window.__hnMusicCarry ?? null, null, "nothing parked for a dismissal");
  } finally {
    mock.timers.reset();
  }
});

test("WidgetManager.dismiss marks the element so the player knows it is a close", () => {
  const indexJs = readFileSync(join(here, "..", "app", "static", "index.js"), "utf8");
  const body = indexJs.slice(indexJs.indexOf("    dismiss(widgetElement) {"));
  const markAt = body.indexOf("userDismissed = '1'");
  assert.ok(markAt > 0 && markAt < body.indexOf("teardownWidget(widgetElement)"),
    "the mark must be set BEFORE teardown runs destroy()");
});

test("a removal with no replacement stops the parked music shortly after", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { factory, window } = loadFactory();
    const w = await playingWidget(factory);
    const audio = w.audio;
    w.destroy();
    assert.equal(audio.paused, false, "parked for a possible successor");
    mock.timers.tick(2500);
    assert.equal(audio.paused, true, "nobody adopted it: it was a removal, so stop");
    assert.equal(window.__hnMusicCarry ?? null, null);
  } finally {
    mock.timers.reset();
  }
});

test("two quick asks in a row hand the same music along instead of dropping it", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { factory } = loadFactory();
    const first = await playingWidget(factory);
    const audio = first.audio;
    first.destroy();
    const second = await mount(factory, "funk");   // still discovering...
    second.destroy();                               // ...when a third ask lands
    const third = await mount(factory, "house");
    mock.timers.tick(5000);
    assert.equal(audio.paused, false, "the original music survives both swaps");
    streams.at(-1).fire("tracks", { tracks: [track("h1")] });
    third.audio.emit("playing");
    assert.equal(audio.paused, true);
  } finally {
    mock.timers.reset();
  }
});

test("closing the new widget while it is still searching also silences the inherited music", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const { factory } = loadFactory();
    const first = await playingWidget(factory);
    const audio = first.audio;
    first.destroy();
    const second = await mount(factory, "funk");
    second.$el.dataset.userDismissed = "1";
    second.destroy();
    assert.equal(audio.paused, true);
  } finally {
    mock.timers.reset();
  }
});
