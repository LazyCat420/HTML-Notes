// Shared fake browser for running the REAL youtubePlayerWidget from widgets.js
// in node. The CRT card is a separate document: it posts ready / embed-error /
// channel-up / volume / power / resize, and the widget posts tune / error back.
// This harness gives the widget a card window that records what it posts and a
// window event bus it can be driven through.

import { loadFactory } from "./_music_widget_harness.mjs";

export function crtElement(width, height = Math.round(width / (0.6 / 0.33))) {
  const classes = new Set(["widget-container", "youtube-player"]);
  const grid = { id: "dashboard-grid" };
  // A real CSSStyleDeclaration treats style.gridColumn and
  // removeProperty('grid-column') as the same property; a plain object does
  // not, so normalise both spellings.
  const style = {};
  const camel = k => k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  style.removeProperty = k => { delete style[k]; delete style[camel(k)]; };
  return {
    id: "yt-1",
    style,
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      contains: c => classes.has(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
    },
    clientWidth: width,
    getBoundingClientRect: () => ({ width, height }),
    closest: sel => (sel === ".dashboard-grid" ? grid : null),
    querySelector: () => null,
    classes,
  };
}

export function resizerStub() {
  const saved = [];
  return {
    MIN_COLS: 1,
    MAX_COLS: 4,
    metrics: () => ({ colWidth: 340, gap: 20, trackCount: 4 }),
    saveSize: (id, size) => saved.push([id, size]),
    saved,
  };
}

// Mount a youtubePlayerWidget at `width` with a recording card window.
export async function mountCrt(width, { videoId = "", title = "Test Channel", candidates = [], query = "" } = {}) {
  const { factory, window: fakeWindow, fetchCalls, store } = loadFactory("youtubePlayerWidget");
  const posted = [];
  const cardWindow = { postMessage: msg => posted.push(msg) };
  const el = crtElement(width);
  const w = factory(videoId, title, candidates, query);
  w.$el = el;
  w.$refs = { crt: { contentWindow: cardWindow } };
  const resizer = resizerStub();
  fakeWindow.WidgetResizer = resizer;
  await w.init();
  return { w, el, posted, cardWindow, window: fakeWindow, resizer, fetchCalls, store };
}

// Deliver a message from the card. `source` defaults to the card's window; pass
// a different object (including null/undefined) to simulate a foreign frame.
export function fromCard(ctx, data, ...rest) {
  const source = rest.length > 0 ? rest[0] : ctx.cardWindow;
  ctx.window.emit("message", { data, source });
}
