# Corpus-first market news + compact mini player — 2026-10-09

Commits `b0f3c6b` (corpus) and `5de43ed` (player), pushed and deployed to the NAS (`main@5de43ed`, health 200).

## Corpus-first news

Follow-up to the article-bodies wave: stop re-scraping what the network
already collected. `app/services/news_corpus.py` reads trading-service's
MongoDB store (`trading_bot.news_articles`) — scraper-service keeps full
bodies in `summary` (~4k median), quality-gated (`quality_status: ok`),
ticker-attributed.

- `build_news_card` adds corpus tasks alongside the external fan-out:
  general asks get watchlist-ticker rows (badged `Your watchlist`) plus
  broad-coverage rows; topic asks get keyword rows.
- Corpus items ride the same `summary` channel the editor consumes, so
  the finance summariser gets full bodies, not 400-char snippets.
- Strictly read-only and fail-open: any Mongo error returns `[]` and the
  card behaves exactly as before. `MONGO_URI` / `MONGO_PASSWORD` come
  from the staged deploy env (`deploy-kit/.env.deploy`); no credentials
  in the repo (the secret-scan hook enforced this — the first draft with
  a literal DSN was rejected and rewritten).
- Tests: `tests/test_news_corpus.py` (3 green).

## Compact mini music player

Card shrinks 280→184 px (384 px with the queue open) and spans 3 columns
on large screens. Title marquees on overflow; volume collapses to a mute
icon that expands on hover; progress + transport + times on one row.
`factory.py` and the `index.js` rehydration path were changed in lockstep
(the rehydrate regex now strips any static `h-[Npx]`).

## Verified

- Deployed container serves the new `index.js` (marquee/compact/hover
  markers present); server-side `render_mini_music_player` produces the
  compact markup (heights, `music-widget-root`, `lg:col-span-3`,
  one-row controls), and the widget paints in the live deployed canvas.
- Full test suite: 1295 passed, 22 failed — **all 22 reproduce on a
  pristine `HEAD~2` copy** (notes-vault 401/AttributeError, themes,
  trending `KeyError: 'universe'`, dynamic-routing
  `NameError: build_answer_config`). Pre-existing at the tip, unrelated
  to these commits; triaged by running the failing subset on a hardlink
  copy with the diff reverted. Needs its own pass later.

## Deploy

`npm run deploy` → `main@5de43ed → synology`; `GET :8035/` → 200.

## Follow-up (user feedback): narrower card, thumbs hug title

Commit `1304cbe`, deployed (`main@1304cbe → synology`). The 👍/👎 sat at
the far right edge because the title block flex-grew. Title block now
caps at 55% width with no grow, so the thumbs sit next to the song name;
the forced `lg:col-span-3` is dropped back to `col-span-2` (both the
factory and the rehydration path). Verified on the deployed canvas:
injected 420px-wide widget shows a 12px gap between title block and
thumbs; served `index.js` contains the hug markup.
