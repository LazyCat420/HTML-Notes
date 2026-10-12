// Shared fake browser for running the REAL musicPlayerWidget from widgets.js
// in node: fake Alpine (captures the factory), Audio, EventSource,
// localStorage and a recording fetch. Used by the music widget behaviour tests.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const widgetsJs = readFileSync(join(here, "..", "app", "static", "js", "widgets.js"), "utf8");

export class FakeAudio {
  constructor() { this.listeners = {}; this.paused = true; this._src = ""; this.volume = 1; this.currentTime = 0; }
  get src() { return this._src; }
  set src(v) { this._src = v; if (!v) this.paused = true; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  emit(t) { (this.listeners[t] || []).forEach(f => f({})); }
  play() { this.paused = false; this.emit("play"); return Promise.resolve(); }
  pause() { if (!this.paused) { this.paused = true; this.emit("pause"); } }
}

export const streams = [];
export class FakeEventSource {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; streams.push(this); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  fire(t, data) { (this.listeners[t] || []).forEach(f => f({ data: JSON.stringify(data) })); }
  close() { this.closed = true; }
}

export function loadFactory() {
  const registry = {};
  let onInit = null;
  const fakeDocument = {
    addEventListener(ev, cb) { if (ev === "alpine:init") onInit = cb; },
    createElement() { return {}; },
    head: { appendChild() {} },
  };
  const fakeAlpine = { data(name, factory) { registry[name] = factory; } };
  const store = new Map();
  const fetchCalls = [];
  const fakeWindow = {
    location: { hostname: "nas", href: "http://nas/" },
    open() { return null; },
  };
  const env = {
    document: fakeDocument, Alpine: fakeAlpine, window: fakeWindow,
    Audio: FakeAudio, EventSource: FakeEventSource,
    localStorage: { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
    fetch: async (url, opts = {}) => {
      fetchCalls.push({ url, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null });
      return { ok: false, json: async () => ({}) };
    },
  };
  const run = new Function(...Object.keys(env), widgetsJs);
  run(...Object.values(env));
  onInit();
  return { factory: registry.musicPlayerWidget, window: fakeWindow, fetchCalls, store };
}

export async function mount(factory, genre) {
  const w = factory({ genre, autoplay: true, base: "http://svc" });
  w.$el = { dataset: {} };
  await w.init();
  return w;
}

