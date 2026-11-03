// Tailwind is compiled at BUILD time (Dockerfile css stage / `npm run build:css`)
// into app/static/tailwind.css. Nothing Tailwind runs in the browser any more:
// the cdn.tailwindcss.com JIT script was removed 2026-11-03.
//
// content: every file that writes class names (server templates in Python,
// client templates in JS, the HTML shell and the walkman card).
// safelist: classes that only exist in SAVED canvases (mined from the live DB by
// scripts/mine_tailwind_safelist.py), so old canvases keep their look.
// No theme overrides: the CDN script ran with the stock config.
const safelist = require("./tailwind.safelist.json");

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.py",
    "./app/static/**/*.{js,html}",
    "!./app/static/lib/**",
    "!./app/static/walkman/model-viewer.min.js",
    "!./app/static/tailwind.css",
  ],
  safelist,
};
