---
part: Canvas and widgets
status: shipped
type: record
updated: 2026-11-03
---

# Tailwind is compiled at build time; nothing Tailwind runs in the browser (2026-11-03)

**Ticket.** The console warned `cdn.tailwindcss.com should not be used in production` on every load. `app/static/index.html` loaded Tailwind's runtime JIT script (about 3 MB), which restyled the DOM in the browser.

**What changed**

- The CDN `<script>` is gone. `app/static/tailwind.css` (about 47 KB minified) is built by the Tailwind CLI (`tailwindcss@3.4.17`, the same v3 engine as the CDN, stock config, Preflight included):
  - in the Docker image: `css` stage in `Dockerfile`;
  - locally: `npm run build:css` (also runs before `npm run dev` / `npm start`).
- The file is not committed (`.gitignore`), and `node_modules/` is dev-only.
- `tailwind.css` is linked as the **last** stylesheet in `<head>`. A headless probe of the live page measured that the CDN appended its `<style>` after `hud-theme.css`, so the cascade order is unchanged. It is cache-busted like `index.css` (`_CACHE_BUSTED_ASSETS`).
- `tailwind.config.js`:
  - `content` = `app/**/*.py` plus `app/static/**/*.{js,html}`;
  - `safelist` = `tailwind.safelist.json`: 771 class tokens mined from the canvases saved in the live `notes.db` by `scripts/mine_tailwind_safelist.py` (read-only). That covers 308 classes older templates and agent cards used that the current source no longer has.
- Install hygiene:
  - `npm install --save-exact --before=2026-10-28 --ignore-scripts`, so every transitive package predates the 5-day rule;
  - `npm audit` reports 0 vulnerabilities;
  - the Docker stage uses `npm ci --ignore-scripts` from the lockfile.

**Evidence**

| Check | Result |
|---|---|
| Screenshot diff, CDN vs compiled: 10 freshly rendered widgets (news card, stock up/down, weather, checklist, clock, KPI, table, timeline, music) | 0 changed pixels (1400×900 full page) |
| Same for a saved canvas from the live DB (2026-10-30, live-video + news card + old music player) | 0 changed pixels |
| Control: same page with no Tailwind at all | 81,013 changed pixels, so the harness does detect missing styles |
| `tests/test_tailwind_build.py`: every Tailwind utility emitted by the real `render_widget` for every widget type (including f-string-built classes) is in the production build | green; red when the build's content globs drop `factory.py` (mutation check) |
| Same file: no Tailwind `<script>` in `index.html`, `tailwind.css` is the last stylesheet | green; red on the old `index.html` |

**When you add a widget class.** Classes written literally in `app/**/*.py` or `app/static/**/*.js|html` are picked up by the next build automatically. A class assembled at runtime from pieces (`f"text-{tone}-400"`) is caught by `test_tailwind_build.py`; spell it out somewhere in source or add it to the safelist.

**Refresh the safelist** after pulling a newer copy of the live DB: `.venv/bin/python scripts/mine_tailwind_safelist.py <notes.db copy>`.
