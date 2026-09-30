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

test("music player widget templates in factory.py and index.js contain favorite heart and star rating controls", () => {
  // Check factory.py
  const factoryFrom = factoryPy.indexOf("def render_mini_music_player");
  const factoryTo = factoryPy.indexOf("def render_youtube_player", factoryFrom);
  const factoryChunk = factoryPy.slice(factoryFrom, factoryTo);

  assert.ok(factoryChunk.includes("toggleFavorite()"), "factory.py must wire toggleFavorite()");
  assert.ok(factoryChunk.includes("setRating(star)"), "factory.py must wire setRating(star)");
  assert.ok(factoryChunk.includes("isFavorite ? 'favorite' : 'favorite_border'"), "factory.py must toggle heart icon");
  assert.ok(factoryChunk.includes("[1, 2, 3, 4, 5]"), "factory.py must render 5 stars");

  // Check index.js self-heal template
  const indexFrom = indexJs.indexOf("newWidget.setAttribute('x-data', `musicPlayerWidget");
  const indexTo = indexJs.indexOf("Progress Bar & Time", indexFrom);
  const indexChunk = indexJs.slice(indexFrom, indexTo);

  assert.ok(indexChunk.includes("toggleFavorite()"), "index.js must wire toggleFavorite()");
  assert.ok(indexChunk.includes("setRating(star)"), "index.js must wire setRating(star)");
  assert.ok(indexChunk.includes("isFavorite ? 'favorite' : 'favorite_border'"), "index.js must toggle heart icon");
  assert.ok(indexChunk.includes("[1, 2, 3, 4, 5]"), "index.js must render 5 stars");
});

test("youtube player widget templates in factory.py and index.js contain thumbs up and down controls", () => {
  // Check factory.py
  const factoryFrom = factoryPy.indexOf("def render_youtube_player");
  const factoryTo = factoryPy.indexOf("def render_stock_card", factoryFrom);
  const factoryChunk = factoryPy.slice(factoryFrom, factoryTo);

  assert.ok(factoryChunk.includes("rateVideo(5)"), "factory.py must wire rateVideo(5) for thumbs up");
  assert.ok(factoryChunk.includes("rateVideo(-5)"), "factory.py must wire rateVideo(-5) for thumbs down");
  assert.ok(factoryChunk.includes("thumb_up"), "factory.py must contain thumb_up");
  assert.ok(factoryChunk.includes("thumb_down"), "factory.py must contain thumb_down");

  // Check index.js self-heal template
  const indexFrom = indexJs.indexOf("newWidget.setAttribute('x-data', `youtubePlayerWidget");
  const indexTo = indexJs.indexOf("Video Embed", indexFrom);
  const indexChunk = indexJs.slice(indexFrom, indexTo);

  assert.ok(indexChunk.includes("rateVideo(5)"), "index.js must wire rateVideo(5) for thumbs up");
  assert.ok(indexChunk.includes("rateVideo(-5)"), "index.js must wire rateVideo(-5) for thumbs down");
  assert.ok(indexChunk.includes("thumb_up"), "index.js must contain thumb_up");
  assert.ok(indexChunk.includes("thumb_down"), "index.js must contain thumb_down");
});

test("music player toggleFavorite sends POST when unfavorited and DELETE when favorited", async () => {
  const from = widgetsJs.indexOf("async toggleFavorite() {");
  assert.ok(from !== -1, "toggleFavorite missing");
  const to = widgetsJs.indexOf("async setRating(star) {", from);
  assert.ok(to !== -1, "setRating marker missing");
  const src = widgetsJs.slice(from, to);

  const calls = [];
  const fakeFetch = async (url, opts) => {
    calls.push({ url, opts: JSON.parse(JSON.stringify(opts)) });
    return { ok: true, json: async () => ({}) };
  };

  const makeWidget = new Function("fetch", `return {
    base: "http://10.0.0.16:8002",
    duration: 180,
    isFavorite: false,
    userRating: 0,
    ratingPending: false,
    currentTrack: {
      id: "abc-123",
      title: "Test Song",
      artist: "Test Artist",
      album: "Test Album",
      isYoutube: true
    },
    ${src}
  };`);

  const widget = makeWidget(fakeFetch);

  // 1. Initial toggle -> favorite (sets rating 5, calls POST)
  await widget.toggleFavorite();
  assert.equal(widget.isFavorite, true);
  assert.equal(widget.userRating, 5);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://10.0.0.16:8002/api/favorites");
  assert.equal(calls[0].opts.method, "POST");
  const postBody = JSON.parse(calls[0].opts.body);
  assert.equal(postBody.path, "youtube://abc-123");
  assert.equal(postBody.rating, 5);
  assert.equal(postBody.title, "Test Song");
  assert.equal(postBody.source, "youtube");

  // 2. Toggle again -> unfavorite (calls DELETE)
  await widget.toggleFavorite();
  assert.equal(widget.isFavorite, false);
  assert.equal(widget.userRating, 0);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].opts.method, "DELETE");
  const delBody = JSON.parse(calls[1].opts.body);
  assert.equal(delBody.path, "youtube://abc-123");
});

test("music player setRating updates star rating and sends to music-player", async () => {
  const from = widgetsJs.indexOf("async setRating(star) {");
  assert.ok(from !== -1, "setRating missing");
  const to = widgetsJs.indexOf("openInFullPlayer() {", from);
  assert.ok(to !== -1, "openInFullPlayer marker missing");
  const src = widgetsJs.slice(from, to);

  const calls = [];
  const fakeFetch = async (url, opts) => {
    calls.push({ url, opts: JSON.parse(JSON.stringify(opts)) });
    return { ok: true, json: async () => ({}) };
  };

  const makeWidget = new Function("fetch", `return {
    base: "http://10.0.0.16:8002",
    duration: 180,
    isFavorite: false,
    userRating: 0,
    ratingPending: false,
    currentTrack: {
      id: "abc-123",
      title: "Test Song",
      artist: "Test Artist",
      album: "Test Album",
      isYoutube: true
    },
    ${src}
  };`);

  const widget = makeWidget(fakeFetch);

  // Set rating to 4 stars
  await widget.setRating(4);
  assert.equal(widget.userRating, 4);
  assert.equal(widget.isFavorite, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].opts.method, "POST");
  assert.equal(JSON.parse(calls[0].opts.body).rating, 4);

  // Clicking 4 again resets to 0 (clearing rating)
  await widget.setRating(4);
  assert.equal(widget.userRating, 0);
  assert.equal(widget.isFavorite, false);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].opts.method, "DELETE");
});

test("youtubePlayerWidget rateVideo sends thumbs up/down to /api/wallgarden/rate", async () => {
  const from = widgetsJs.indexOf("async rateVideo(rating) {");
  assert.ok(from !== -1, "rateVideo missing");
  const to = widgetsJs.indexOf("extractYoutubeId(url) {", from);
  assert.ok(to !== -1, "extractYoutubeId marker missing");
  const src = widgetsJs.slice(from, to);

  const calls = [];
  const fakeFetch = async (url, opts) => {
    calls.push({ url, opts: JSON.parse(JSON.stringify(opts)) });
    return { ok: true, json: async () => ({ ok: true }) };
  };

  const makePlayer = new Function("fetch", `return {
    videoId: "yt-vid-999",
    title: "Awesome Science Video",
    userRating: 0,
    ratePending: false,
    ${src}
  };`);

  const player = makePlayer(fakeFetch);

  // 1. Thumbs Up (+5)
  await player.rateVideo(5);
  assert.equal(player.userRating, 5);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/wallgarden/rate");
  const body1 = JSON.parse(calls[0].opts.body);
  assert.equal(body1.video_id, "yt-vid-999");
  assert.equal(body1.rating, 5);

  // 2. Thumbs Down (-5)
  await player.rateVideo(-5);
  assert.equal(player.userRating, -5);
  assert.equal(calls.length, 2);
  const body2 = JSON.parse(calls[1].opts.body);
  assert.equal(body2.video_id, "yt-vid-999");
  assert.equal(body2.rating, -5);

  // 3. Toggle off thumbs down
  await player.rateVideo(-5);
  assert.equal(player.userRating, 0);
  assert.equal(calls.length, 3);
  const body3 = JSON.parse(calls[2].opts.body);
  assert.equal(body3.rating, 0);
});
