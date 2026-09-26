// Tests for:
// 1. Ghost audio prevention: watchdog timer stored on instance and cleared in closeStream() / destroy()
// 2. Teardown helper teardownWidget() in index.js calling destroy() on removed/replaced widgets
// 3. Music player logging [MusicPlayer] 🎵 Now Playing: on playback
// 4. Record play event to POST /api/music/history and localStorage caching
// 5. Tabbed queue/history UI support in widgets.js and factory.py
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "app", "static");
const widgetsJs = readFileSync(join(staticDir, "js", "widgets.js"), "utf8");
const indexJs = readFileSync(join(staticDir, "index.js"), "utf8");
const factoryPy = readFileSync(join(here, "..", "app", "widgets", "factory.py"), "utf8");

test("music player clears watchdog timer on closeStream and destroy", () => {
  assert.ok(
    widgetsJs.includes("this.watchdogTimer"),
    "watchdogTimer must be bound to `this` on music player instance"
  );
  assert.ok(
    widgetsJs.includes("clearTimeout(this.watchdogTimer)"),
    "closeStream() must clear watchdogTimer to prevent orphaned failover"
  );
});

test("index.js invokes destroy() on widgets when removed or replaced", () => {
  assert.ok(
    indexJs.includes("teardownWidget") || indexJs.includes("destroy("),
    "index.js must have teardownWidget to clean up Alpine widget state"
  );
  assert.ok(
    indexJs.includes("teardownWidget(existing)"),
    "reconcileCanvas must call teardownWidget(existing) before removal/replacement"
  );
});

test("music player logs '🎵 Now Playing' and dispatches play event to /api/music/history", () => {
  assert.ok(
    widgetsJs.includes("[MusicPlayer] 🎵 Now Playing:"),
    "music player must log '🎵 Now Playing:' with title and artist"
  );
  assert.ok(
    widgetsJs.includes("/api/music/history"),
    "music player must POST to /api/music/history upon playback"
  );
  assert.ok(
    widgetsJs.includes("hn_music_history"),
    "music player must cache played tracks in localStorage['hn_music_history']"
  );
});

test("music player supports activeTab ('queue' | 'history') and renders history tab", () => {
  assert.ok(
    widgetsJs.includes("activeTab"),
    "musicPlayerWidget must maintain activeTab state"
  );
  assert.ok(
    factoryPy.includes("activeTab") && factoryPy.includes("History"),
    "factory.py template must render the History tab toggle"
  );
});
