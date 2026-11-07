// The CRT cabinet is one object: 0.600 x 0.330 m (aspect 1.8182), drawn edge
// to edge in app/static/crt/player.html. So the widget box must stay at the
// cabinet's own aspect — height DERIVED from width, never stored — and the
// cabinet's own bezel (the card's drag target) is the only resize grip. These
// tests run the real youtubePlayerWidget factory from widgets.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mountCrt, fromCard } from "./_crt_widget_harness.mjs";

const ASPECT = 0.6 / 0.33;

test("height is the width over the cabinet's 1.8182 aspect", async () => {
  const { w } = await mountCrt(732);
  assert.equal(w.tvBaseH, Math.round(732 / ASPECT), "732 px wide cabinet must be 403 px tall");
  assert.equal(w.tvHeight, 403, "tvHeight is the binding the template renders");
});

test("a rect read mid entry animation does not shrink the cabinet", async () => {
  // The widget fades in scaled to ~0.98, so getBoundingClientRect reports ~717
  // for a 732 px card. adopt() must read the layout width (clientWidth).
  const { w } = await mountCrt(732);
  w.$el.getBoundingClientRect = () => ({ width: 717, height: 394 });
  w.tvResizeListener();
  assert.equal(w.tvBaseH, 403, "the layout width, not the scaled rect");
});

test("a video id is tuned into the card with a zero-based channel", async () => {
  const { w, posted } = await mountCrt(732, { videoId: "abc123DEF45", title: "Test Channel" });
  const tune = posted.find(m => m.action === "tune");
  assert.deepEqual(tune, { type: "crt", action: "tune", video: "abc123DEF45",
                           title: "Test Channel", channel: 0 });
});

test("changing the channel increments the tune channel", async () => {
  const { w, posted } = await mountCrt(732, { videoId: "aaaaaaaaaaa", title: "T", candidates: ["bbbbbbbbbbb"] });
  w.playById("bbbbbbbbbbb");
  const tunes = posted.filter(m => m.action === "tune");
  assert.equal(tunes.length, 2);
  assert.equal(tunes[1].channel, 1, "second tune is channel 1 — the static burst is a channel change");
  assert.equal(tunes[1].video, "bbbbbbbbbbb");
});

test("the card's ready message re-sends the current tune", async () => {
  // A fresh mount: the initial tune goes out before the card has a listener,
  // so 'ready' is what actually starts playback.
  const ctx = await mountCrt(732, { videoId: "abc123DEF45", title: "T" });
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "ready" });
  assert.equal(ctx.posted.length, 1);
  assert.equal(ctx.posted[0].action, "tune");
  assert.equal(ctx.posted[0].video, "abc123DEF45");
});

test("messages from a foreign frame or missing source are ignored", async () => {
  const ctx = await mountCrt(732, { videoId: "abc123DEF45", title: "T", candidates: ["bbbbbbbbbbb"] });
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "channel-up" }, { other: "frame" });
  fromCard(ctx, { type: "crt", action: "channel-up" }, null);
  fromCard(ctx, { type: "crt", action: "channel-up" }, undefined);
  assert.equal(ctx.posted.filter(m => m.action === "tune").length, 0, "a foreign or missing source must not steer the TV");
});

test("ready mid-search does not tune a raw query:... string, and restores custom volume", async () => {
  const ctx = await mountCrt(732);
  ctx.w.videoId = "query:lofi beats";
  ctx.w.crtVolume = 55;
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "ready" });
  assert.equal(ctx.posted.filter(m => m.action === "tune").length, 0, "must not tune an un-resolved query string");
  assert.deepEqual(ctx.posted, [{ type: "crt", action: "state", volume: 55 }]);
});

test("an embed error falls back to the next candidate", async () => {
  const ctx = await mountCrt(732, { videoId: "abc123DEF45", title: "T", candidates: ["bbbbbbbbbbb"] });
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "embed-error", code: 101 });
  await new Promise(r => setTimeout(r, 0));
  const tune = ctx.posted.find(m => m.action === "tune");
  assert.ok(tune, "the fallback candidate must be tuned");
  assert.equal(tune.video, "bbbbbbbbbbb");
  assert.equal(tune.channel, 1);
});

test("a dead channel puts NO SIGNAL on the tube with the failed channel's watch url", async () => {
  const ctx = await mountCrt(732, { videoId: "abc123DEF45", title: "T", candidates: ["bbbbbbbbbbb"] });
  fromCard(ctx, { type: "crt", action: "channel-up" });
  await new Promise(r => setTimeout(r, 0));
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "embed-error", code: 150 });
  await new Promise(r => setTimeout(r, 0));
  const err = ctx.posted.find(m => m.action === "error");
  assert.ok(err, "the card must be told to draw the no-signal screen");
  assert.equal(err.watchUrl, "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    "watchUrl must point to the channel that failed, not always channel 0");
  assert.equal(ctx.w.error, "This video blocks embedding.");
});

test("the channel knob advances to an unattempted candidate and wraps around without dead-ending", async () => {
  const ctx = await mountCrt(732, { videoId: "abc123DEF45", title: "T", candidates: ["bbbbbbbbbbb"] });
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "channel-up" });
  await new Promise(r => setTimeout(r, 0));
  assert.equal(ctx.posted.find(m => m.action === "tune").video, "bbbbbbbbbbb");
  // All candidates tried: next click wraps back to the first channel instead of dead-ending
  ctx.posted.length = 0;
  const fetchesBefore = ctx.fetchCalls.length;
  fromCard(ctx, { type: "crt", action: "channel-up" });
  await new Promise(r => setTimeout(r, 0));
  assert.equal(ctx.posted.find(m => m.action === "tune").video, "abc123DEF45");
  assert.equal(ctx.fetchCalls.length, fetchesBefore + 1, "at most one re-search fetch");
});

test("the knobs' settings are remembered, not acted on", async () => {
  const ctx = await mountCrt(732);
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "volume", volume: 42 });
  fromCard(ctx, { type: "crt", action: "power", playing: false });
  assert.equal(ctx.w.crtVolume, 42);
  assert.equal(ctx.w.crtPowerOn, false);
  assert.equal(ctx.posted.length, 0, "the card handles volume/power locally; parent posts nothing back");
});

test("dragging the bezel sets a column span and saves only cols", async () => {
  const ctx = await mountCrt(732);
  fromCard(ctx, { type: "crt", action: "resize", phase: "start", x: 700, y: 200 });
  fromCard(ctx, { type: "crt", action: "resize", phase: "move", dx: 340, dy: 0, x: 1040, y: 200 });
  assert.equal(ctx.el.style.gridColumn, "span 3", "732 + 340 px at 340+20 px per column");
  fromCard(ctx, { type: "crt", action: "resize", phase: "end", dx: 340, dy: 0, x: 1040, y: 200 });
  assert.deepEqual(ctx.resizer.saved, [["yt-1", { cols: 3 }]], "a free height would crop the cabinet");
});

test("a stationary click on the bezel is not a resize", async () => {
  const ctx = await mountCrt(732);
  fromCard(ctx, { type: "crt", action: "resize", phase: "start", x: 300, y: 200 });
  fromCard(ctx, { type: "crt", action: "resize", phase: "end", dx: 1, dy: 1, x: 301, y: 201 });
  assert.equal(ctx.resizer.saved.length, 0);
  assert.equal(ctx.el.style.gridColumn, undefined);
});

test("dragging out and returning within 3 px of start still reconciles the final span", async () => {
  const ctx = await mountCrt(732);
  fromCard(ctx, { type: "crt", action: "resize", phase: "start", x: 700, y: 200 });
  fromCard(ctx, { type: "crt", action: "resize", phase: "move", dx: 340, dy: 0, x: 1040, y: 200, moved: true });
  assert.equal(ctx.el.style.gridColumn, "span 3");
  fromCard(ctx, { type: "crt", action: "resize", phase: "end", dx: 1, dy: 0, x: 701, y: 200, moved: true });
  assert.equal(ctx.el.style.gridColumn, "span 2", "returning to start restores the 2-column span");
  assert.deepEqual(ctx.resizer.saved, [["yt-1", { cols: 2 }]]);
});

test("a vertical bezel drag grows the cabinet along its aspect", async () => {
  const ctx = await mountCrt(732);
  fromCard(ctx, { type: "crt", action: "resize", phase: "start", x: 300, y: 380 });
  // 100 px down on the bottom bezel = 100 * 1.818 px of width = +182 px
  fromCard(ctx, { type: "crt", action: "resize", phase: "move", dx: 0, dy: 100, x: 300, y: 480 });
  assert.equal(ctx.el.style.gridColumn, "span 3", "914 px / 360 px per column → 3");
});

test("a top/left bezel drag outward grows the widget", async () => {
  const ctx = await mountCrt(732);
  fromCard(ctx, { type: "crt", action: "resize", phase: "start", x: 10, y: 10 });
  fromCard(ctx, { type: "crt", action: "resize", phase: "move", dx: -340, dy: 0, x: -330, y: 10 });
  assert.equal(ctx.el.style.gridColumn, "span 3");
});

test("a reset drops the saved size but never strips the derived height", async () => {
  const ctx = await mountCrt(732);
  ctx.el.style.gridColumn = "span 3";
  ctx.el.style.height = "403px";
  fromCard(ctx, { type: "crt", action: "resize", phase: "reset" });
  assert.deepEqual(ctx.resizer.saved, [["yt-1", null]]);
  assert.equal(ctx.el.style.gridColumn, undefined);
  assert.equal(ctx.el.style.height, "403px",
    "the Alpine :style binding owns the height; stripping it drops the cabinet to auto");
});

test("destroy removes the card listener", async () => {
  const ctx = await mountCrt(732);
  ctx.w.destroy();
  ctx.posted.length = 0;
  fromCard(ctx, { type: "crt", action: "channel-up" });
  assert.equal(ctx.posted.length, 0);
});
