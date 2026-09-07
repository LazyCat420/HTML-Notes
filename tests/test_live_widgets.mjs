// Live-widget chrome: a nested Alpine scope that re-pulls ONE widget on its
// TTL through HN.refreshWidget, paints through the versioned reconciler, and
// pauses while the tab is hidden. Source-level pins.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const widgetsJs = readFileSync(join(staticDir, "js", "widgets.js"), "utf8");

const mixin = widgetsJs.slice(widgetsJs.indexOf("Alpine.data('liveWidget'"), widgetsJs.indexOf("Alpine.data('appGridWidget'"));

test("liveWidget exists and refreshes through HN.refreshWidget, never a chat turn", () => {
  assert.ok(mixin.length > 0, "no liveWidget mixin");
  assert.match(mixin, /HN\.refreshWidget\(w\.id\)/);
  assert.ok(!mixin.includes("HN.ask("), "a refresh must not be a chat message");
});

test("liveWidget pauses while the tab is hidden and tears its timers down", () => {
  assert.match(mixin, /visibilitychange/);
  assert.match(mixin, /if \(document\.hidden\) \{ this\._disarm\(\); return; \}/);
  assert.match(mixin, /destroy\(\) \{[\s\S]*?clearInterval/);
});

test("HN.refreshWidget POSTs to the refresh route and paints through the versioned path", () => {
  const i = indexJs.indexOf("window.HN.refreshWidget = async function");
  assert.ok(i > 0);
  const body = indexJs.slice(i, i + 900);
  assert.match(body, /\/api\/widget\/\$\{encodeURIComponent\(state\.sessionId\)\}\/\$\{encodeURIComponent\(widgetId\)\}\/refresh/);
  assert.match(body, /method: "POST"/);
  assert.match(body, /HN\.paintCanvas\(data\.content, data\.version\)/);
  const p = indexJs.indexOf("window.HN.paintCanvas = function");
  assert.match(indexJs.slice(p, p + 400), /renderContent\("", content, version\)/);
});
