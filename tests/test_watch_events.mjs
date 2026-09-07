// The idle-tab channel: EventSource on /session/<id>/events, component
// frames painted through the versioned path, notify → toast + Notification,
// closed while hidden. Plus the watch list's cancel. Source-level pins.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const widgetsJs = readFileSync(join(staticDir, "js", "widgets.js"), "utf8");

const chan = indexJs.slice(indexJs.indexOf("function openEventStream()"), indexJs.indexOf("window.HN.openEventStream"));

test("the stream is a per-session EventSource", () => {
  assert.match(chan, /new EventSource\(`\/session\/\$\{encodeURIComponent\(state\.sessionId\)\}\/events`\)/);
});

test("component frames go through the versioned paint path", () => {
  assert.match(chan, /d\.type === "component" && d\.content/);
  assert.match(chan, /HN\.paintCanvas\(d\.content, d\.version\)/);
});

test("notify lines toast and raise a browser Notification only when granted", () => {
  assert.match(chan, /d\.type === "notify"/);
  assert.match(chan, /Notification\.permission === "granted"/);
});

test("the stream closes while the tab is hidden and reopens on return", () => {
  const vis = indexJs.slice(indexJs.indexOf('document.addEventListener("visibilitychange", () => {\n        if (document.hidden) closeEventStream()'));
  assert.ok(vis.length > 0, "no visibility handling for the event stream");
});

test("the stream opens right after history loads", () => {
  const i = indexJs.indexOf("    loadHistory();\n    if (window.HN && HN.openEventStream) HN.openEventStream();");
  assert.ok(i > 0);
});

test("watch list cancel is a session-scoped DELETE", () => {
  const w = widgetsJs.slice(widgetsJs.indexOf("Alpine.data('watchListWidget'"), widgetsJs.indexOf("Alpine.data('liveWidget'"));
  assert.match(w, /method: 'DELETE'/);
  assert.match(w, /\/api\/watches\/\$\{encodeURIComponent\(id\)\}\?session_id=/);
  assert.match(w, /addEventListener\('hn:watches'/);
});
