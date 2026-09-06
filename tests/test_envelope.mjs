// The in-flight envelope: the card a slow agent turn holds on the canvas.
//
// Two things are being guarded here, and they fail in very different ways.
//
// 1. HUMANIZATION. The envelope's one detail line is the only place a user
//    learns what the agent is doing. Printing the raw MCP name there
//    ("mcp__lazy-tool-service__html_notes_web_search") is not a progress
//    report — it is the thing that made the UI feel opaque in the first place.
//
// 2. CANVAS HYGIENE, which is the dangerous one because it fails SILENTLY and
//    PERMANENTLY. The envelope is a client-only node living in #dashboard-grid.
//    getCleanedCanvasHtml() serializes that grid and the server ADOPTS the
//    result as the canonical canvas — so an envelope that leaks into it becomes
//    a card that loads forever and survives every reload. This is the same
//    class of bug the existing `data-provisional` strip guards against, and the
//    only reason it is a test rather than a comment is that nothing about the
//    symptom points back at this function.
//
// Run: `node --test tests/`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const indexHtml = readFileSync(join(staticDir, "index.html"), "utf8");
const indexCss = readFileSync(join(staticDir, "index.css"), "utf8");

// ─── humanizeTool, lifted out of index.js and run for real ──────────────────
// Sliced rather than duplicated: a copy would keep passing after the original
// was changed, which is the failure mode a test like this exists to prevent.
function extract(startMarker, endMarker) {
  const from = indexJs.indexOf(startMarker);
  assert.ok(from !== -1, `could not find ${startMarker} in index.js`);
  // Search PAST the start marker: the two markers can share a prefix
  // ("function getCleanedCanvasHtml()" / "function "), and an end found at
  // `from` would silently yield an empty slice that matches nothing.
  const to = indexJs.indexOf(endMarker, from + startMarker.length);
  assert.ok(to !== -1, `could not find ${endMarker} after ${startMarker}`);
  return indexJs.slice(from, to);
}

const humanizeSrc = extract("const TOOL_LABELS = {", "// One envelope per turn");
const humanizeTool = new Function(`${humanizeSrc}; return humanizeTool;`)();

test("a search reads as a search, with what it is searching for", () => {
  assert.equal(
    humanizeTool("mcp__lazy-tool-service__html_notes_web_search",
                 { query: "nvidia q3 earnings" }),
    "searching: nvidia q3 earnings"
  );
});

test("a page read names the site, not the whole URL", () => {
  // The URL is mostly tracking noise; the site is the informative part.
  assert.equal(
    humanizeTool("mcp__lazy-tool-service__html_notes_read_page",
                 { url: "https://www.reuters.com/markets/some/very/long/path?x=1" }),
    "reading: reuters.com"
  );
});

test("the raw MCP name never reaches the card", () => {
  for (const tool of [
    "mcp__lazy-tool-service__html_notes_web_search",
    "mcp__lazy-tool-service__canvas_add_widget",
    "mcp__lazy-tool-service__some_tool_added_next_year",
  ]) {
    const out = humanizeTool(tool, { query: "x" });
    assert.doesNotMatch(out, /mcp__/, `leaked the MCP prefix for ${tool}`);
    assert.doesNotMatch(out, /_/, `leaked an underscore identifier for ${tool}`);
  }
});

test("an unknown tool still says something readable", () => {
  assert.equal(humanizeTool("mcp__lazy-tool-service__html_notes_do_a_thing", {}),
               "do a thing");
});

test("a missing or malformed arg degrades to the verb alone", () => {
  const t = "mcp__lazy-tool-service__html_notes_web_search";
  assert.equal(humanizeTool(t, {}), "searching");
  assert.equal(humanizeTool(t, null), "searching");
  assert.equal(humanizeTool(t, { query: "   " }), "searching");
  // A non-URL in a url slot must not throw — args come off the wire.
  assert.equal(
    humanizeTool("mcp__lazy-tool-service__html_notes_read_page", { url: "not a url" }),
    "reading: not a url"
  );
});

test("a long argument is truncated, so one line stays one line", () => {
  const out = humanizeTool("mcp__lazy-tool-service__html_notes_web_search",
                           { query: "n".repeat(400) });
  assert.ok(out.length < 70, `detail line too long: ${out.length} chars`);
  assert.match(out, /…$/);
});

// ─── canvas hygiene ─────────────────────────────────────────────────────────

test("the envelope is stripped before the canvas is sent to the server", () => {
  const cleaner = extract("function getCleanedCanvasHtml()", "const MAX_CONCURRENT_TURNS");
  assert.match(
    cleaner,
    /querySelectorAll\('\[data-turn-envelope\]'\)[\s\S]{0,60}\.remove\(\)/,
    "getCleanedCanvasHtml must REMOVE envelope nodes — the server adopts this " +
    "HTML as canonical, so a leaked envelope loads forever"
  );
});

test("reconcileCanvas cannot delete a live envelope", () => {
  const reconcile = extract("function reconcileCanvas(", "let changed = false;");
  assert.match(
    reconcile,
    /hasAttribute\('data-turn-envelope'\)[\s\S]{0,20}return/,
    "the server-removal sweep must skip envelopes: they are client-only, so " +
    "every id they carry is 'not in the server's set' by construction"
  );
});

test("the envelope stays out of the widget classes", () => {
  // A .widget-container would put a transient card in front of every sweep that
  // assumes a real widget — the saved layout order, the snapshot seeder, the
  // close-button injector and the random-id assigner.
  const builder = extract("node.className = ", "node.setAttribute");
  assert.doesNotMatch(builder, /widget-container|glass-card|canvas-widget/);
});

test("masonry knows about the envelope, since nothing else will span it", () => {
  // The one widget behaviour it does need. Without this the card collapses to a
  // 1px grid row and the widgets below ride up over it.
  assert.match(
    indexHtml,
    /grid\.querySelectorAll\('[^']*\.turn-envelope[^']*'\)\.forEach\(spanFor\)/,
    "index.html's masonry layout() must include .turn-envelope"
  );
});

test("changed front-end assets are cache-busted", () => {
  // index.html's own comment warns that browsers otherwise keep running the
  // cached copy. The CSS carries the envelope's entire appearance, so a stale
  // stylesheet renders it as an unstyled div rather than not at all.
  const js = indexHtml.match(/index\.js\?v=([\d.]+)/);
  const css = indexHtml.match(/index\.css\?v=([\d.]+)/);
  const hud = indexHtml.match(/hud-theme\.css\?v=([\d.]+)/);
  assert.ok(js && css && hud, "all three assets must carry a version query");
  assert.ok(parseFloat(js[1]) >= 3.3, `index.js still pinned at v${js[1]}`);
  assert.ok(parseFloat(css[1]) >= 3.2, `index.css still pinned at v${css[1]}`);
  assert.ok(parseFloat(hud[1]) >= 1.2, `hud-theme.css still pinned at v${hud[1]}`);
});

test("every animation the envelope uses is disabled under reduced motion", () => {
  const block = indexCss.slice(indexCss.lastIndexOf("@media (prefers-reduced-motion: reduce)"));
  for (const cls of ["is-arriving", "is-delivered", "turn-envelope-stamp"]) {
    assert.ok(block.includes(cls), `${cls} is not disabled under reduced motion`);
  }
  // With no animation there is no animationend, so the fold-away must not rely
  // on one to stop showing — the JS timeout removes the node a beat later.
  assert.match(block, /\.turn-envelope\.is-delivered\s*\{\s*opacity:\s*0/);
});
