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
  const start = indexJs.indexOf("function reconcileCanvas(container, rawHtml)");
  const end = indexJs.indexOf("newWidgets.forEach(newWidget => {");
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

test("CanvasToolbar, keyboard data-ask parity, height preservation, and vendored static libs exist", () => {
  assert.match(indexJs, /window\.CanvasToolbar\s*=/);
  assert.match(indexJs, /canvas\.addEventListener\("keydown"/);
  assert.match(indexJs, /savedSize\?\.height/);
  assert.match(indexJs, /labelCounts\[baseLabel\] > 1/);

  assert.ok(!indexHtml.includes("cdn.jsdelivr.net"), "external JS CDNs must be vendored (TCK-1036)");
  assert.match(indexHtml, /src="lib\/marked\.min\.js"/);
  assert.match(indexHtml, /src="lib\/chart\.umd\.min\.js"/);
  assert.match(indexHtml, /defer src="lib\/alpine\.min\.js"/);
});
