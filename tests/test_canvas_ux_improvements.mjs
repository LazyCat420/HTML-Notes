// Tests for Wave 3 (TCK-1021..1031), Wave 4 (TCK-1036), and Wave 5 (I1/I2/I3).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const indexHtml = readFileSync(join(staticDir, "index.html"), "utf8");

test("teardownWidget runs destroy() at most once per node/Alpine instance (TCK-1025)", () => {
  const from = indexJs.indexOf("function teardownWidget(el)");
  const to = indexJs.indexOf("window.teardownWidget = teardownWidget;");
  const src = indexJs.slice(from, to);
  let calls = 0;
  const fakeData = { destroy() { calls += 1; } };
  const fakeEl = {
    querySelectorAll: () => [],
  };
  const fn = new Function("window", `${src}; return teardownWidget;`);
  const teardown = fn({ Alpine: { $data: () => fakeData } });
  teardown(fakeEl);
  teardown(fakeEl);
  assert.equal(calls, 1, "destroy() must run once even when teardownWidget fires twice");
});

test("WidgetManager scopes dismissed widgets by session, caps at 100, and supports Undo stack (TCK-1022/1029/I1)", () => {
  const from = indexJs.indexOf("window.WidgetManager = {");
  const to = indexJs.indexOf("window.WidgetResizer = {", from);
  const src = indexJs.slice(from, to);
  const store = new Map();
  const localStorageMock = {
    getItem: k => store.get(k) ?? null,
    setItem: (k, v) => store.set(k, String(v)),
  };
  const windowMock = { HN: { getSessionId: () => "sess-alpha" } };
  const docMock = { getElementById: () => null, querySelector: () => null };
  const fn = new Function("window", "localStorage", "document", "teardownWidget", "fetch", `${src}; return window.WidgetManager;`);
  const wm = fn(windowMock, localStorageMock, docMock, () => {}, null);

  for (let i = 0; i < 110; i++) {
    wm.dismiss({
      id: `w-${i}`,
      dataset: {},
      classList: { contains: () => false, remove() {}, add() {} },
      getAttribute: a => (a === "data-widget-title" ? `Title ${i}` : "note"),
      outerHTML: `<div id="w-${i}"></div>`,
      closest: () => null,
      addEventListener() {},
      remove() {},
    }, { skipServer: true });
  }
  assert.equal(wm.getDismissed().length, 100, "dismissed list must cap at 100 entries per session");
  assert.equal(wm.isDismissed("w-109"), true);
  assert.equal(wm.isDismissed("w-0"), false);
  assert.equal(wm.closedStack.length, 10, "closedStack must retain at most 10 entries");
  assert.equal(store.has("dismissed_widgets:sess-alpha"), true);

  windowMock.HN.getSessionId = () => "sess-beta";
  assert.equal(wm.isDismissed("w-109"), false, "session isolation must keep sess-beta clean");
});

test("reconcileCanvas skips dismissed widget ids so SSE frames never resurrect closed cards", () => {
  const start = indexJs.indexOf("function reconcileCanvas(container, rawHtml");
  const end = indexJs.indexOf("newWidgets.forEach(newWidget => {", start);
  assert.ok(start > 0 && end > start);
  const loopHead = indexJs.slice(end, end + 260);
  assert.match(loopHead, /WidgetManager\.isDismissed\(id\)\)\s*return;/);
});

test("Clear History stops turns, closes EventSource, and clears snapshots (TCK-1023)", () => {
  const start = indexJs.indexOf("// Clear history listener");
  const end = indexJs.indexOf("widget_order", start);
  const block = end > start ? indexJs.slice(start, end) : indexJs.slice(start, start + 2200);
  assert.match(block, /stopEverything\(\);/);
  assert.match(block, /closeEventStream\(\);/);
  assert.match(block, /widgetSourceSnapshots\.clear\(\);/);
  assert.match(block, /lastRenderedComponentHtml = "";/);
  assert.match(block, /openEventStream\(\);/);
});

test("component handler no longer fights top-scroll with scrollToBottom (TCK-1024)", () => {
  const s = indexJs.indexOf('} else if (data.type === "component") {');
  const e = indexJs.indexOf('} else if (data.type === "tool_call") {', s);
  const compBranch = indexJs.slice(s, e);
  assert.ok(!compBranch.includes("scrollToBottom()"), "component branch must not call scrollToBottom()");
});

test("self-heal uses data-widget-type instead of id.includes('player') substring heuristic (TCK-1021)", () => {
  const s = indexJs.indexOf("// ─── POST-PROCESS WIDGET CONSOLE / CONTROLS");
  const e = indexJs.indexOf("function initCanvasWidgets(", s);
  const postProcess = indexJs.slice(s, e);
  assert.ok(!postProcess.includes("id.includes('player')"), "must not guess music widget from 'player' in id");
  assert.ok(!postProcess.includes("id.includes('clock')"), "must not guess clock from id substring");
  assert.ok(!postProcess.includes("id.includes('video')"), "must not guess youtube from id substring");
  assert.match(postProcess, /getAttribute\('data-widget-type'\)/);
});

test("runChatTurn cleans data-provisional in finally, distinguishes dropped streams, and attaches Retry (TCK-1027/I3)", () => {
  const s = indexJs.indexOf("async function runChatTurn(text, controller");
  const e = indexJs.indexOf("function scrollToBottom()", s);
  const src = indexJs.slice(s, e);
  assert.match(src, /let sawDoneEvent = false;/);
  assert.match(src, /Connection lost before response finished\./);
  assert.match(src, /finally\s*\{[\s\S]*?removeAttribute\(['"]data-provisional['"]\)/);
  assert.match(src, /turn-retry-btn/);
});

test("CanvasToolbar filters by query and type pill, manages Closed tray, and vendored static libs exist", () => {
  const from = indexJs.indexOf("window.CanvasToolbar = (function () {");
  const to = indexJs.indexOf("window.CanvasToolbar.init();", from);
  const src = indexJs.slice(from, to);

  function makeEl(tag, attrs = {}) {
    const classes = new Set((attrs.className || "").split(/\s+/).filter(Boolean));
    const listeners = {};
    const children = [];
    const el = {
      tagName: tag.toUpperCase(),
      dataset: {},
      attributes: new Map(Object.entries(attrs)),
      children,
      hidden: false,
      value: attrs.value || "",
      textContent: attrs.textContent || "",
      classList: {
        toggle: (c, force) => {
          if (force === undefined) { classes.has(c) ? classes.delete(c) : classes.add(c); return classes.has(c); }
          force ? classes.add(c) : classes.delete(c);
          return force;
        },
        contains: c => classes.has(c),
      },
      set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
      get className() { return Array.from(classes).join(" "); },
      getAttribute: k => el.attributes.get(k) ?? null,
      setAttribute: (k, v) => el.attributes.set(k, String(v)),
      hasAttribute: k => el.attributes.has(k),
      appendChild: c => { children.push(c); return c; },
      append: (...cs) => children.push(...cs),
      addEventListener: (ev, fn) => { (listeners[ev] ||= []).push(fn); },
      dispatch: (ev, eObj = {}) => (listeners[ev] || []).forEach(fn => fn({ target: el, stopPropagation() {}, preventDefault() {}, ...eObj })),
      contains: target => target === el || children.some(c => c === target || c.contains?.(target)),
      focus: () => { docMock.activeElement = el; },
      blur: () => { if (docMock.activeElement === el) docMock.activeElement = null; },
    };
    return el;
  }

  const card1 = makeEl("div", { "data-widget-type": "clock", "data-widget-title": "World Clock", textContent: "12:00 UTC" });
  const card2 = makeEl("div", { "data-widget-type": "stock_card", "data-widget-title": "NVDA Quote", textContent: "NVDA 140.00" });
  const grid = { querySelectorAll: () => [card1, card2] };
  const byId = {
    "canvas-toolbar": makeEl("div"),
    "canvas-search-input": makeEl("input"),
    "canvas-search-clear": makeEl("button"),
    "canvas-type-pills": makeEl("div"),
    "canvas-closed-btn": makeEl("button"),
    "closed-widgets-popover": makeEl("div"),
  };
  const docListeners = {};
  let restoredId = null;
  const windowMock = {
    WidgetManager: { closedStack: [{ id: "w-old", title: "Old Note", widget_type: "note" }], restore: id => { restoredId = id; } },
  };
  const docMock = {
    activeElement: null,
    getElementById: id => byId[id] || null,
    querySelector: sel => (sel === "#dashboard-grid" ? grid : null),
    createElement: tag => makeEl(tag),
    addEventListener: (ev, fn) => { (docListeners[ev] ||= []).push(fn); },
  };
  const fn = new Function("window", "document", "requestAnimationFrame", `${src}; return window.CanvasToolbar;`);
  const toolbar = fn(windowMock, docMock, cb => cb());
  toolbar.init();

  assert.equal(byId["canvas-toolbar"].hidden, false);
  assert.equal(byId["canvas-closed-btn"].textContent, "Closed (1)");
  toolbar.setQuery("nvda");
  assert.equal(card1.classList.contains("is-filtered-out"), true);
  assert.equal(card2.classList.contains("is-filtered-out"), false);
  toolbar.setQuery("");
  toolbar.setType("clock");
  assert.equal(card1.classList.contains("is-filtered-out"), false);
  assert.equal(card2.classList.contains("is-filtered-out"), true);

  byId["canvas-closed-btn"].dispatch("click");
  assert.equal(byId["closed-widgets-popover"].hidden, false);
  const restoreBtn = byId["closed-widgets-popover"].children[0].children[1];
  restoreBtn.dispatch("click");
  assert.equal(restoredId, "w-old");

  assert.ok(!indexHtml.includes("cdn.jsdelivr.net"), "external JS CDNs must be vendored (TCK-1036)");
  assert.match(indexHtml, /src="lib\/marked\.min\.js"/);
  assert.match(indexHtml, /src="lib\/chart\.umd\.min\.js"/);
  assert.match(indexHtml, /defer src="lib\/alpine\.min\.js"/);
});

test("keyboard data-ask handler enforces link/music/input guards (claude R1-F5)", () => {
  const from = indexJs.indexOf('canvas.addEventListener("keydown", (e) => {');
  const to = indexJs.indexOf("window.addEventListener(\"message\"", from);
  const keyBlock = indexJs.slice(from, to);
  let handler = null;
  const asks = [];
  const canvasMock = {
    contains: () => true,
    addEventListener: (ev, fn) => { if (ev === "keydown") handler = fn; },
  };
  const run = new Function("canvas", "window", "HN", keyBlock);
  const hnMock = { ask: (t, o) => asks.push([t, o]) };
  run(canvasMock, { HN: hnMock }, hnMock);
  assert.equal(typeof handler, "function");

  const askEl = { getAttribute: () => "Drill down", hasAttribute: () => false };
  // Link target must NOT trigger HN.ask unless inside .hn-ask-chip
  handler({
    key: "Enter",
    preventDefault() { throw new Error("should not preventDefault on a[href]"); },
    stopPropagation() {},
    target: { closest: s => (s === "a[href]" ? {} : s === "[data-ask]" ? askEl : null), matches: () => false },
  });
  assert.equal(asks.length, 0);

  // Input control inside [data-ask] must NOT trigger HN.ask on Space
  handler({
    key: " ",
    preventDefault() { throw new Error("should not hijack input Space"); },
    stopPropagation() {},
    target: { closest: s => (s === "[data-ask]" ? askEl : null), matches: s => s.includes("input") },
  });
  assert.equal(asks.length, 0);

  // Focusable [data-ask] non-link/non-input element MUST trigger HN.ask
  let prevented = false;
  handler({
    key: "Enter",
    preventDefault() { prevented = true; },
    stopPropagation() {},
    target: { closest: s => (s === "[data-ask]" ? askEl : null), matches: () => false },
  });
  assert.equal(prevented, true);
  assert.deepEqual(asks, [["Drill down", { keepFocus: false }]]);
});

test("dismiss migrates legacy storage, merges on empty server stack, clears pre-turn ids, and !res.ok has Retry", () => {
  const from = indexJs.indexOf("window.WidgetManager = {");
  const to = indexJs.indexOf("window.WidgetResizer = {", from);
  const src = indexJs.slice(from, to);
  const store = new Map([["dismissed_widgets", JSON.stringify(["legacy-card-1"])]]);
  const localStorageMock = {
    getItem: k => store.get(k) ?? null,
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
  };
  const windowMock = { HN: { getSessionId: () => "sess-mig" } };
  const docMock = { getElementById: () => null, querySelector: () => null };
  const fn = new Function("window", "localStorage", "document", "teardownWidget", "fetch", `${src}; return window.WidgetManager;`);
  const wm = fn(windowMock, localStorageMock, docMock, () => {}, null);

  assert.equal(wm.isDismissed("legacy-card-1"), true, "legacy dismissed_widgets must migrate");
  assert.equal(store.has("dismissed_widgets"), false, "legacy dismissed_widgets key must be removed");

  wm._dismissedTimes["pre-turn-id"] = 1000;
  wm._dismissedTimes["mid-turn-id"] = 3000;
  wm._saveDismissed(["pre-turn-id", "mid-turn-id"]);
  assert.equal(wm.unDismissIfBefore("pre-turn-id", 2000), true);
  assert.equal(wm.isDismissed("pre-turn-id"), false);
  assert.equal(wm.unDismissIfBefore("mid-turn-id", 2000), false);
  assert.equal(wm.isDismissed("mid-turn-id"), true);

  assert.match(indexJs, /if\s*\(!res\.ok\)\s*\{[\s\S]*?retryText:\s*text,\s*retryOpts:\s*turnOpts/);
  assert.match(indexJs, /addEventListener\("keydown",[\s\S]*?e\.target\.closest\?\.\("a\[href\]"\)[\s\S]*?e\.target\.matches\?\.\("input, textarea, select, button"\)/);
});
