// The walkman deck is a 0.300 x 0.150 m object drawn edge to edge in its
// iframe, so the widget box must stay at the device's own 2:1 aspect: height
// is DERIVED from width, never stored. A saved height (from the retired corner
// grip) made the stage taller than 2:1, which shrinks the camera's horizontal
// span below the 0.300 m body and crops the deck on both sides. These tests run
// the real musicPlayerWidget factory from widgets.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadFactory } from "./_music_widget_harness.mjs";

function makeEl(width) {
  const classes = new Set(["widget-container", "music-widget-root"]);
  const grid = { id: "dashboard-grid" };
  const style = {};
  style.removeProperty = k => { delete style[k]; };
  return {
    id: "music-1",
    style,
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      contains: c => classes.has(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
    },
    getBoundingClientRect: () => ({ width, height: Math.round(width / 2) }),
    closest: sel => (sel === ".dashboard-grid" ? grid : null),
    querySelector: () => null,
    appendChild() {},
    classes,
  };
}

async function mountAt(width, { saved } = {}) {
  const { factory, window: fakeWindow, store } = loadFactory();
  fakeWindow.dispatchEvent = () => {};
  if (saved) store.set("widget_sizes", JSON.stringify(saved));
  const w = factory({ genre: "", autoplay: false });
  w.$el = makeEl(width);
  await w.init();
  w.closeStream();
  return { w, window: fakeWindow };
}

function resizerStub() {
  const saved = [];
  return {
    MIN_COLS: 1,
    MAX_COLS: 4,
    metrics: () => ({ colWidth: 340, gap: 20, trackCount: 4 }),
    saveSize: (id, size) => saved.push([id, size]),
    saved,
  };
}

test("height is the width over 2 (the device's own aspect)", async () => {
  const { w } = await mountAt(732);
  assert.equal(w.walkmanBaseH, 366, "732 px wide card must be 366 px tall");
  assert.equal(w.walkmanHeight, 366);
});

test("a height saved by the old corner grip is ignored, not applied", async () => {
  // 520 px on a 732 px card is a 1.4:1 stage: the old behaviour, which cropped
  // the deck. The width alone decides now.
  const { w } = await mountAt(732, { saved: { "music-1": { cols: 2, height: 520 } } });
  assert.equal(w.walkmanBaseH, 366, "stored height must NOT win over width/2");
});

test("the library drawer adds its own band under a 2:1 stage", async () => {
  const { w } = await mountAt(732);
  w.walkmanOpen = true;
  assert.equal(w.walkmanHeight, 366 + 148, "drawer band is added, the stage stays 2:1");
});

test("a width change re-derives the height", async () => {
  const { w } = await mountAt(732);
  w.$el.getBoundingClientRect = () => ({ width: 400, height: 200 });
  w.walkmanResizeListener();
  assert.equal(w.walkmanBaseH, 200);
});

test("bezel drag snaps the width to grid columns and stores only the columns", async () => {
  const { w, window: fakeWindow } = await mountAt(732);
  const stub = resizerStub();
  fakeWindow.WidgetResizer = stub;

  w.walkmanResize({ phase: "start", dx: 0, dy: 0 });
  assert.ok(w.$el.classes.has("is-resizing-bezel"), "drag state must be marked");

  // 200 px to the right on a 732 px card: (732+200+20)/360 = 2.64 → 3 columns.
  w.walkmanResize({ phase: "move", dx: 200, dy: 0 });
  assert.equal(w.$el.style.gridColumn, "span 3");

  w.walkmanResize({ phase: "end", dx: 200, dy: 0 });
  assert.deepEqual(stub.saved.at(-1), ["music-1", { cols: 3 }],
                   "only columns are stored; the height follows the width");
  assert.ok(!w.$el.classes.has("is-resizing-bezel"), "drag state must be cleared");
});

test("vertical drag also drives the width (2:1 body: 1 px tall = 2 px wide)", async () => {
  const { w, window: fakeWindow } = await mountAt(732);
  fakeWindow.WidgetResizer = resizerStub();
  w.walkmanResize({ phase: "start", dx: 0, dy: 0 });
  // 150 px down = 300 px wider: (732+300+20)/360 = 2.92 → 3 columns.
  w.walkmanResize({ phase: "move", dx: 0, dy: 150 });
  assert.equal(w.$el.style.gridColumn, "span 3");
});

test("a small drag below half a column keeps the current width", async () => {
  const { w, window: fakeWindow } = await mountAt(732);
  fakeWindow.WidgetResizer = resizerStub();
  w.walkmanResize({ phase: "start", dx: 0, dy: 0 });
  w.walkmanResize({ phase: "move", dx: -30, dy: 0 }); // 702 → 2.006 → 2 columns
  assert.equal(w.$el.style.gridColumn, "span 2");
});

test("a drag never goes below one column or past the grid's track count", async () => {
  const { w, window: fakeWindow } = await mountAt(732);
  const stub = resizerStub();
  stub.metrics = () => ({ colWidth: 340, gap: 20, trackCount: 2 });
  fakeWindow.WidgetResizer = stub;
  w.walkmanResize({ phase: "start", dx: 0, dy: 0 });
  w.walkmanResize({ phase: "move", dx: 2000, dy: 0 });
  assert.equal(w.$el.style.gridColumn, "span 2", "clamped to the grid's 2 tracks");
  w.walkmanResize({ phase: "move", dx: -4000, dy: 0 });
  assert.equal(w.$el.style.gridColumn, "span 1", "clamped to one column");
});

test("double-click reset drops the saved size", async () => {
  const { w, window: fakeWindow } = await mountAt(732);
  const stub = resizerStub();
  fakeWindow.WidgetResizer = stub;
  w.walkmanResize({ phase: "reset" });
  assert.deepEqual(stub.saved.at(-1), ["music-1", null]);
});

test("resize messages are handled without a state push per pointer move", async () => {
  const { w } = await mountAt(732);
  let pushes = 0;
  w.pushWalkmanState = () => { pushes++; };
  const f = { contentWindow: {} };
  w.$refs = { walkman: f };
  w.walkmanResize = () => {};
  w.onWalkmanMessage({ data: { type: "walkman", action: "resize", phase: "move" },
                       source: f.contentWindow });
  assert.equal(pushes, 0, "a bezel drag fires at pointer rate; it must not re-push state");
});
