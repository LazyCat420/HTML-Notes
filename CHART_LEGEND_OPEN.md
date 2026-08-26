# Chart Legend → Stock Widget (shipped 2026-08-26, `9695069`)

Clicking a ticker in a stock comparison chart's legend (trending / compare)
opens that ticker's own stock widget, instead of Chart.js's default
hide-the-series toggle. Shift-click keeps the toggle.

## Mechanism

- **The gate is the series label shape, not the widget id.** Every stock
  comparison chart — fast-path `stock-trending`, router `stock-compare`, and
  the agent's `compare_symbols` charts — gets its datasets from the one shared
  builder (`build_stock_compare_config`), which labels each series
  `SYM  +x.x%`. The hydrator in `app/static/index.js`
  (`renderDynamicComponents`, the `language-chart` block) enables the custom
  legend `onClick` only when EVERY dataset label matches
  `/^[A-Z][A-Z0-9.\-]{0,9}\s+[+\-−]?\d+(\.\d+)?%$/`, so a rainfall/GDP
  multi-series chart keeps the default legend. Widget-id gating would have
  missed the agent lane (`chart-<hex>` ids).
- **Injected at hydration, both render paths covered by one seam.** A function
  can't ride in the baked JSON config block, and the hidden `<pre>` config is
  the only thing the server round-trips — so the handler is attached where the
  client converts the block to a live Chart.js canvas. Initial render and
  self-heal re-render both pass through that converter, so there is no
  factory.py twin to keep in sync for this.
- **`window.HN.ask(text)`** (new, defined next to `sendChatMessage`) is the
  programmatic chat entry: echoes to history, pushes onto `chatQueue`, drains.
  Any widget chrome can reuse it. It clears `state.focusWidgetId` first —
  the click that triggered it landed INSIDE the chart widget, and the focus
  tracker's capture-phase pointerdown listener has already stamped that widget
  as focused, which would otherwise invite the server to edit the chart in
  place instead of spawning the asked-for stock card.
- The click sends `"<SYM> stock"`, which the tier-2 router resolves
  deterministically to the single-stock widget (`stock-<sym>`) in ~4s
  (measured 3.8s server-side). There is no zero-LLM fast lane for single
  tickers; the router path was judged fast enough.

## Evidence (live, deployed container)

`scripts`-less one-off: `legend_click_check.py` (scratchpad) drove the real
app at `:8035` with headless Chromium (youtube-wallgarden/.venv playwright):

1. Asked "top 5 trending stocks this month" → `stock-trending-7c615ddd`
   hydrated with labels `ANF +50.7% / META / NVDA / XPON / RVMD`.
2. Clicked the real legend hitbox (`chart.legend.legendHitBoxes[0]`, canvas
   coords) → `stock-anf` widget appeared (widgets 1 → 2).
3. `getDatasetMeta(0).hidden` stayed falsy — the default toggle was
   suppressed, the ANF line never left the chart.
4. `HN.ask('NVDA stock')` called directly also spawned `stock-nvda` —
   the queue path works independent of the legend.

Verification trap hit on the way: the first test's "a stock widget appeared"
predicate matched the *trending* widget itself (`id^=stock`, and the hidden
config block contains the ticker text), so it passed instantly at 1 → 1
widgets — a green that proved nothing. The fixed predicate excludes
`stock-trending*` ids and requires a NEW widget.

## Not verified (by-inspection only)

- Shift-click toggle preservation (code path calls the captured
  `Chart.defaults.plugins.legend.onClick`).
- Pointer-cursor hint on legend hover (`onHover`/`onLeave`).
- Behavior on `stock-compare` / agent `compare_symbols` charts — same label
  builder, so the gate matches, but only the trending chart was driven live.

## Pre-existing open item (not from this change)

Page load logs `Failed to load models — TypeError: Failed to fetch` from
`fetchModels` (`index.js` `?v=afa4675353`, line ~3112): the model dropdown's
source endpoint is failing. Untouched by this work; needs its own look.
