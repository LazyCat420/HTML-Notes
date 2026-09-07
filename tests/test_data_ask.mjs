// The canvas as an input device: any element carrying data-ask="<utterance>"
// is a one-click follow-up. ONE capture-phase handler on #live-canvas turns
// the click into HN.ask(); a sandboxed map iframe reaches the same entry via
// postMessage({type:'hn-ask'}). Source-level pins, like test_envelope.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const indexCss = readFileSync(join(staticDir, "index.css"), "utf8");

test("one delegated capture-phase click handler resolves [data-ask]", () => {
  const i = indexJs.indexOf('closest?.("[data-ask]")');
  assert.ok(i > 0, "no [data-ask] resolver");
  const around = indexJs.slice(Math.max(0, i - 600), i + 900);
  assert.match(around, /addEventListener\("click",[\s\S]*?,\s*true\)/, "must be capture phase");
  assert.match(around, /HN\.ask\(/, "the click must go through HN.ask");
  assert.match(around, /preventDefault\(\)/, "a data-ask on a link must not navigate");
});

test("HN.ask takes options and only clears focus when not asked to keep it", () => {
  assert.match(indexJs, /window\.HN\.ask = function \(text, opts\)/);
  assert.match(indexJs, /if \(!opts\.keepFocus\) state\.focusWidgetId = null;/);
});

test("the sandboxed-iframe ask is accepted only from a canvas frame with an opaque origin", () => {
  const i = indexJs.indexOf('"hn-ask"');
  assert.ok(i > 0, "no hn-ask message listener");
  const around = indexJs.slice(Math.max(0, i - 400), i + 800);
  assert.match(around, /e\.origin !== "null"/, "must require the opaque sandbox origin");
  assert.match(around, /contentWindow === e\.source/, "must require a canvas iframe as the source");
});

test("Alpine-bound :data-ask survives DOMPurify", () => {
  const cfg = indexJs.slice(indexJs.indexOf("CANVAS_DOMPURIFY_CONFIG = {"), indexJs.indexOf("FORCE_BODY: true"));
  assert.ok(cfg.includes("':data-ask'"), "':data-ask' must be in ADD_ATTR");
});

test("asks look clickable", () => {
  assert.match(indexCss, /\[data-ask\]\s*\{[^}]*cursor:\s*pointer/);
});
