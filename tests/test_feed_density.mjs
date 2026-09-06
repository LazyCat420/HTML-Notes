import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const indexCss = readFileSync(join(staticDir, "index.css"), "utf8");

test("WidgetLayout sorts unranked new widgets before known ones", () => {
  // Extract WidgetLayout code
  const from = indexJs.indexOf("window.WidgetLayout = {");
  assert.ok(from !== -1, "could not find WidgetLayout in index.js");
  const to = indexJs.indexOf('document.addEventListener("DOMContentLoaded"', from);
  assert.ok(to !== -1, "could not find end of WidgetLayout before DOMContentLoaded");
  const wlSrc = indexJs.slice(from, to);

  // Mock environment
  const savedOrder = ["w-saved-1", "w-saved-2"];
  const windowMock = {
    WidgetLayout: null
  };
  const localStorageMock = {
    getItem: (key) => JSON.stringify(savedOrder),
    setItem: () => {}
  };

  const fn = new Function("window", "localStorage", `${wlSrc}; window.WidgetLayout = window.WidgetLayout;`);
  fn(windowMock, localStorageMock);

  // Mock DOM grid with 1 brand new widget (w-new-0) and 2 existing widgets
  const kids = [
    { id: "w-new-0" },
    { id: "w-saved-1" },
    { id: "w-saved-2" }
  ];
  const appended = [];
  const gridMock = {
    querySelectorAll: () => kids,
    appendChild: (el) => { appended.push(el.id); }
  };

  windowMock.WidgetLayout.apply(gridMock);
  assert.deepEqual(appended, ["w-new-0", "w-saved-1", "w-saved-2"],
    "New/unranked widgets must land at the top of the feed ahead of saved widgets");
});

test("index.css defines density-compact and feed-aged rules", () => {
  assert.match(indexCss, /\.dashboard-grid\.density-compact\s*\{/, "Must define .dashboard-grid.density-compact");
  assert.match(indexCss, /\.widget-container\.is-feed-aged/, "Must define .is-feed-aged styling");
  assert.match(indexCss, /\.widget-expand-toggle/, "Must define .widget-expand-toggle button");
});

test("index.js getCleanedCanvasHtml removes density and feed-aging classes", () => {
  assert.match(indexJs, /savedGrid\.classList\.remove\('density-compact'\)/, "Must strip density-compact from grid");
  assert.match(indexJs, /classList\.remove\('is-feed-aged', 'is-expanded'\)/, "Must strip is-feed-aged and is-expanded");
  assert.match(indexJs, /querySelectorAll\('\.widget-expand-toggle'\)/, "Must strip widget-expand-toggle");
});

test("turn envelope prepends to the top of the grid", () => {
  assert.match(indexJs, /g\.prepend\(node\);/, "Turn envelope must prepend to top of grid");
});
