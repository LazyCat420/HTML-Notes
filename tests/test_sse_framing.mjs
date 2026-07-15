// Regression guard for "widgets don't pop up unless I refresh".
//
// The server emits the `component` SSE event as ONE `data: {...}\n\n` line whose
// `content` is the ENTIRE canvas HTML (app/main.py `render_component_event`,
// many KB). The browser's ReadableStream reader returns arbitrary byte chunks,
// so that one line is routinely split across two reader.read() results.
//
// The old client loop split each raw chunk on "\n" and JSON.parse'd each
// `data:` line immediately, with no buffer across reads. A half-arrived
// component line failed to parse (swallowed as a "partial chunk"), its
// continuation didn't start with "data: " so it was ignored too, and the widget
// silently never painted until a reload repainted it from history.
//
// This test extracts the buffering contract that index.js's stream loop must
// uphold and proves it survives every chunk boundary. Run: `node --test tests/`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { TextEncoder } from "node:util";

const here = dirname(fileURLToPath(import.meta.url));
const indexJs = readFileSync(join(here, "..", "app", "static", "index.js"), "utf8");

// The real stream loop in index.js must (a) accumulate reads into a buffer and
// (b) only process complete, newline-terminated lines. If someone reverts to the
// per-chunk `chunk.split("\n")` approach, these guards fail loudly.
test("index.js buffers SSE reads across chunk boundaries", () => {
  assert.match(indexJs, /let buffer = "";/, "stream loop must keep a cross-read buffer");
  assert.match(indexJs, /buffer\s*\+=\s*decoder\.decode/, "each read must append to the buffer, not be parsed standalone");
  assert.match(indexJs, /buffer\.indexOf\("\\n"\)/, "must slice complete lines out of the buffer by newline");
  assert.doesNotMatch(
    indexJs,
    /const chunk = decoder\.decode\([^)]*\);\s*\n\s*const lines = chunk\.split\("\\n"\)/,
    "must NOT go back to splitting each raw read chunk directly"
  );
});

// Behavioral proof: a buffered parser with the same contract recovers a large
// component line at every chunk size; the old per-chunk parser drops it.
const canvasHtml =
  '<div id="dashboard-grid" class="dashboard-grid">' +
  '<div class="widget-container" id="widget-stock-nvda" data-sig="abc123">' +
  "<span x-text='price'></span>".repeat(200) +
  "</div></div>";

const fullStream =
  'data: {"type":"chunk","content":"Here you go."}\n\n' +
  "data: " + JSON.stringify({ type: "component", content: canvasHtml, version: 1700000000001 }) + "\n\n" +
  'data: {"type":"done"}\n\n';

function makeReader(bytes, chunkSize) {
  const buf = new TextEncoder().encode(bytes);
  let pos = 0;
  return {
    async read() {
      if (pos >= buf.length) return { value: undefined, done: true };
      const value = buf.slice(pos, pos + chunkSize);
      pos += chunkSize;
      return { value, done: false };
    },
  };
}

// Mirror of the fixed loop's framing logic (the part under test).
async function bufferedParse(reader) {
  const decoder = new TextDecoder("utf-8");
  const painted = [];
  let done = false;
  let buffer = "";
  const processLine = (line) => {
    if (!line.startsWith("data: ")) return;
    try {
      const data = JSON.parse(line.substring(6));
      if (data.type === "component") painted.push(data);
    } catch { /* only reached on genuinely malformed complete lines */ }
  };
  while (!done) {
    const { value, done: rd } = await reader.read();
    done = rd;
    if (value) {
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 1);
        processLine(line.endsWith("\r") ? line.slice(0, -1) : line);
      }
    }
  }
  if (buffer.trim()) processLine(buffer.trim());
  return painted;
}

// The old, broken loop, kept here only to document what regressing looks like.
async function perChunkParse(reader) {
  const decoder = new TextDecoder("utf-8");
  const painted = [];
  let done = false;
  while (!done) {
    const { value, done: rd } = await reader.read();
    done = rd;
    if (value) {
      const chunk = decoder.decode(value, { stream: true });
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.substring(6));
            if (data.type === "component") painted.push(data);
          } catch { /* dropped */ }
        }
      }
    }
  }
  return painted;
}

test("buffered parser paints exactly one component at every chunk size", async () => {
  for (const size of [1, 7, 64, 512, 4096, 100000]) {
    const painted = await bufferedParse(makeReader(fullStream, size));
    assert.equal(painted.length, 1, `chunkSize=${size} must yield one component`);
    assert.equal(painted[0].content, canvasHtml, `chunkSize=${size} must recover full canvas`);
    assert.equal(painted[0].version, 1700000000001, `chunkSize=${size} must preserve version`);
  }
});

test("the old per-chunk parser drops the component when the line is split (the bug)", async () => {
  const dropped = await perChunkParse(makeReader(fullStream, 4096)); // splits the big line
  assert.equal(dropped.length, 0, "confirms the pre-fix failure mode");
  const whole = await perChunkParse(makeReader(fullStream, 100000)); // whole stream in one read
  assert.equal(whole.length, 1, "and confirms it only worked when the stream fit in one read");
});
