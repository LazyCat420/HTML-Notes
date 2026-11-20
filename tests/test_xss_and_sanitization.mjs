// Regression pins for TCK-1013, TCK-1014, TCK-1016 (client XSS & CSRF headers).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const indexJs = readFileSync(join(here, "..", "app", "static", "index.js"), "utf8");

test("DOMPurify Alpine hook is scoped inside sanitizeCanvasHtml try/finally", () => {
  const hookMatches = indexJs.match(/DOMPurify\.addHook\s*\(/g) || [];
  assert.equal(hookMatches.length, 1, "expected exactly one DOMPurify.addHook call");

  const start = indexJs.indexOf("function sanitizeCanvasHtml(dirty)");
  const end = indexJs.indexOf("function sanitizeChatHtml(dirty)");
  assert.ok(start > 0 && end > start, "sanitizeCanvasHtml/sanitizeChatHtml functions missing");

  const canvasFn = indexJs.slice(start, end);
  assert.match(canvasFn, /try\s*\{[\s\S]*?DOMPurify\.sanitize\([\s\S]*?\}\s*finally\s*\{[\s\S]*?DOMPurify\.removeHooks\(\s*['"]uponSanitizeAttribute['"]\s*\)/);

  const chatStart = end;
  const chatBlock = indexJs.slice(chatStart, chatStart + 300);
  assert.ok(!chatBlock.includes("addHook"), "sanitizeChatHtml must not install Alpine hooks");
});

test("chat & canvas parsers avoid un-sanitized detached div.innerHTML execution", () => {
  const wtStart = indexJs.indexOf("function widgetTypes(html)");
  const wtBlock = indexJs.slice(wtStart, wtStart + 400);
  assert.match(wtBlock, /new DOMParser\(\)\.parseFromString/);
  assert.ok(!wtBlock.includes(".innerHTML"), "widgetTypes must not assign innerHTML");

  const fmtStart = indexJs.indexOf("function formatAssistantChatBubble(");
  const fmtBlock = indexJs.slice(fmtStart, fmtStart + 1200);
  assert.match(fmtBlock, /new DOMParser\(\)\.parseFromString/);
  assert.match(fmtBlock, /sanitizeChatHtml\(/);
  assert.ok(!fmtBlock.includes("temp.innerHTML ="), "must not parse raw assistant HTML with detached div.innerHTML");
});

test("addLogStep builds DOM with textContent/createElement instead of innerHTML", () => {
  const stepStart = indexJs.indexOf("function setStepContent(");
  const stepEnd = indexJs.indexOf("HN.turn(text);", stepStart);
  assert.ok(stepStart > 0 && stepEnd > stepStart);
  const logHelpers = indexJs.slice(stepStart, stepEnd);
  assert.match(logHelpers, /\.textContent\s*=/);
  assert.match(logHelpers, /createElement\(["']strong["']\)/);
  assert.ok(!logHelpers.includes("innerHTML"), "addLogStep/setStepContent must never write innerHTML");
});

test("Forget-me client sends X-HN-Client header on DELETE /user/memory", () => {
  const idx = indexJs.indexOf('fetch("/user/memory"');
  assert.ok(idx > 0);
  const call = indexJs.slice(idx, idx + 140);
  assert.match(call, /"X-HN-Client":\s*"1"/);
});
