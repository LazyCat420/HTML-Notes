# HTML-Notes: Canvas Feed (Newest on Top) & Widget Auto-Resize System

## 1. Problem Statement & Motivation
Currently in `HTML-Notes`, when an agent turn executes and generates new widget boxes, they are appended to the bottom of `#dashboard-grid`. Furthermore, factory widgets have rigid fixed height classes (`h-[380px]`, `h-[420px]`, `h-[560px]`) and fixed wide column spans (`col-span-2`), while CSS grid enforces a 340px minimum column track.

As a result:
1. **Offscreen Spawning**: New data cards arrive below the fold. The user is forced to scroll down or manually dismiss/close existing widgets to see the agent's fresh answer.
2. **Space Inefficiency**: A widget containing only a 2-line response consumes 380px+ of height, wasting massive vertical canvas space.
3. **Low Card Capacity**: Only 1–2 widgets fit on screen before vertical scrolling becomes necessary.

This plan specifies two coordinated systems:
1. **Reverse-Chronological Canvas Feed**: New widgets and in-flight activity turn envelopes always appear at the **top** (index 0) of the canvas. Older widgets naturally slide down as a living feed. The canvas automatically maintains visibility of the newest content without manual scrolling.
2. **Adaptive Widget Auto-Resize System**: Widgets dynamically auto-size based on content volume and total canvas density, allowing 4–8+ widget boxes to fit simultaneously on screen without clutter.

---

## 2. Evidence & Baseline Verification (Verified Facts)

All claims below are grounded in primary code inspection:
- `[CONFIRMED]` **Frontend Reconcile Append**: `app/static/index.js` lines 1305 and 1356 call `grid.appendChild(newWidget)` for new widgets.
- `[CONFIRMED]` **Layout Order Demotion**: `app/static/index.js` lines 283–288 in `WidgetLayout.apply(grid)` assign `r = Infinity` to any widget ID not present in `localStorage['widget_order']`, sorting brand-new widgets to the bottom after all saved cards.
- `[CONFIRMED]` **Envelope Bottom Append**: `app/static/index.js` line 1878 in `createTurnEnvelope()` calls `g.appendChild(node)`, placing the progress card at the bottom.
- `[CONFIRMED]` **Server-Side Snapshot Append**: `app/routes/message.py` lines 168, 436, 536, 1871, 2433, 2523, 2794, 3032, and 3086 use `.append(node)` on `target` / `grid`, saving bottom-appended snapshots to the database.
- `[CONFIRMED]` **Rigid Column Track**: `app/static/index.css` line 143 defines `grid-template-columns: repeat(auto-fill, minmax(340px, 1fr))`, restricting standard desktop viewports to 2 columns.
- `[CONFIRMED]` **Hardcoded Widget Heights & Spans**: `app/widgets/factory.py` bakes classes such as `col-span-2`, `h-[380px]`, `h-[420px]`, and `h-[460px]` directly into HTML templates.
- `[CONFIRMED]` **Cache Busting Requirement**: `tests/test_envelope.mjs` lines 143–154 assert that query versions in `index.html` must be incremented whenever `index.js`, `index.css`, or `hud-theme.css` are modified.

---

## 3. Design Decisions & Trade-Offs

### Decision 1: Placement Strategy for New Widgets & Turn Envelopes
- **Options Considered**:
  - *Option A*: CSS `order` property or `flex-direction: column-reverse`.
    - *Drawback*: CSS Grid masonry (`grid-auto-flow: row dense` + 1px track row spans) is coupled to DOM tree order. Changing CSS order breaks the masonry row packing and causes visual overlap.
  - *Option B (Recommended)*: **True DOM Prepend (`grid.prepend`) at both client and server layers**.
    - In `reconcileCanvas`, new widgets prepend to the top of `#dashboard-grid`.
    - In `createTurnEnvelope`, the in-flight envelope prepends to the top of `#dashboard-grid`.
    - In `WidgetLayout.apply`, unranked/new widgets receive top priority (`rank < 0` or prepended to order).
    - In `message.py`, server-side soup insertions use `target.insert(0, node)`.
    - *Benefit*: Preserves native CSS grid masonry flow, avoids layout stutter, persists cleanly in canvas serialization, and keeps DOM order identical to visual order.

### Decision 2: Auto-Resize & Density Architecture
- **Options Considered**:
  - *Option A*: Pure CSS media queries.
    - *Drawback*: Media queries only respond to viewport width, not to the number of widgets on the canvas or individual card content height.
  - *Option B (Recommended)*: **Three-Layer Hybrid Auto-Resize System**:
    1. **Content-Hugging Bounds (CSS + Factory)**:
       - Replace rigid fixed heights (`h-[380px]`, `h-[420px]`) with flexible bounding (`min-h-[160px]`, `max-h-[440px]`, `height: auto`).
       - Masonry layout (`spanFor`) automatically measures exact client heights (`getBoundingClientRect().height`) and assigns exact 1px grid-row spans. Short cards (e.g. 2 tasks or a brief summary) immediately shrink from 380px to ~180px.
    2. **Adaptive Canvas Density (JS Observer)**:
       - A lightweight density manager (`CanvasDensity`) monitors count of active `.widget-container` elements in `#dashboard-grid`.
       - When count ≤ 2: `density-spacious` (minmax 320px column tracks, default 2-col spans allowed).
       - When count ≥ 3: `density-compact` (minmax 260px column tracks, automatic multi-column tiling 3–4 wide, standard cards collapse from 2 cols to 1 col unless media/wide).
    3. **Feed-Aging Progressive Compression ("Hero on Top, Compact Downward")**:
       - The newest card (index 0) remains in full hero mode.
       - Cards aged down the feed (index ≥ 2) adopt a compact height ceiling (~200–220px) with internal scrollbar and a clean 1-click expand button (`⤢`), ensuring 6–10 cards fit in the viewport simultaneously.
    4. **Manual User Override Immunity**:
       - Any widget manually resized by the user with `WidgetResizer` (drag handle) retains its explicit dimensions in `localStorage['widget_sizes']`. Double-clicking the resize handle resets it back to automatic adaptive sizing.

---

## 4. User Review Required & Open Questions

> [!IMPORTANT]
> Please review the following design decisions:

1. **Follow-Up Edits (In-Place vs. Bump to Top)**:
   - When the user asks a follow-up about an existing widget (e.g., "add milk to that checklist" or "change that chart to 30 days"):
     - **Option 1 (In-Place Update)**: The existing card stays at its current feed position and glitches/updates in place.
     - **Option 2 (Bump to Top)**: The updated widget moves to the very top of the feed as the newest active item.
     - *Which behavior do you prefer for follow-up edits?*

2. **Older Widget Compression Threshold**:
   - For auto-resizing older widgets that slide down the feed:
     - Should widgets older than index 2 automatically compress to a compact height (~200px) with an expand button, or should all widgets simply hug their natural content height without forced height caps?

3. **Multi-Widget Batch Order**:
   - If a single agent turn outputs 2 widgets (e.g., a news card AND a chart):
     - They will both pop up at the top as a pair `[Widget 1, Widget 2]`, above the previous turn's widgets `[Old Widget 1, Old Widget 2]`. Does this match your expectation?

---

## 5. Detailed Implementation Steps

### Phase 1: Reverse-Chronological Canvas Feed (Newest on Top)

#### 1.1 In-Flight Turn Envelope at Top
- **Target**: [`app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L1850-L1880)
- **Change**: In `createTurnEnvelope(text)`:
  - Replace `g.appendChild(node)` with `g.prepend(node)`.
  - Ensure the flight animation originates from `#chat-input` and targets the top-left slot of `#dashboard-grid`.
  - Call `elements.liveCanvas.scrollTo({ top: 0, behavior: 'smooth' })` when envelope appears.

#### 1.2 Frontend Reconcile Prepend
- **Target**: [`app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L1300-L1370)
- **Change**: In `reconcileCanvas(container, rawHtml)`:
  - When new widgets arrive without existing matches:
    - Prepend new widgets to the top of `grid` (`grid.prepend(...)`).
    - If multiple new widgets arrive in a batch, prepend them as a group maintaining their intra-batch sequence before older cards.
  - Automatically scroll `#live-canvas` to top (`top: 0`) when a new widget enters.

#### 1.3 WidgetLayout Order Priority for New Arrivals
- **Target**: [`app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L255-L290)
- **Change**: In `WidgetLayout.apply(grid)`:
  - Replace `r: rank.has(el.id) ? rank.get(el.id) : Infinity` with `r: rank.has(el.id) ? rank.get(el.id) : (-kids.length + i)`.
  - This guarantees any brand-new widget sorts to the **front** of the grid instead of the end.
  - In `WidgetLayout.capture(grid)`, reading child elements from top to bottom captures the newest items at index 0.

#### 1.4 Server-Side Widget Insertion Prepend
- **Target**: [`app/routes/message.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/message.py#L150-L540)
- **Change**: In all widget placement handlers:
  - Line 168: `target.insert(0, node)`
  - Line 436: `(grid or soup).insert(0, node)`
  - Line 536: For batch router placement, insert at front in proper sequence.
  - Lines 1871, 2433, 2523, 2794, 3032, 3086: Use `.insert(0, ...)` instead of `.append(...)`.
  - Ensures database-persisted canvas snapshots match client feed ordering.

---

### Phase 2: Adaptive Widget Auto-Resize System

#### 2.1 Content-Hugging Height & Adaptive Spans
- **Target**: [`app/widgets/factory.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py#L450-L750) & [`app/static/index.css`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.css#L140-L290)
- **Change**:
  - Replace hardcoded fixed height classes (`h-[380px]`, `h-[420px]`, `h-[460px]`) on standard text, checklist, data, and weather cards with flexible classes: `min-h-[160px] max-h-[460px] h-auto`.
  - Keep media player widgets (YouTube player, Cytoscape graph) with appropriate aspect ratios.
  - In `index.css`, provide `.dashboard-grid > .widget-container` with content-fit rules and custom scrollbars for inner overflow.

#### 2.2 Adaptive Canvas Density Manager
- **Target**: [`app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js)
- **Change**: Introduce `CanvasDensity` controller:
  - Measures total active widgets in `#dashboard-grid`.
  - When widget count ≥ 3:
    - Applies `.density-compact` to `#dashboard-grid`.
    - Dynamic column tracks: `grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))`.
    - Auto-converts standard `col-span-2` cards to single column unless user manually resized them.
  - Integrates with `window.__masonryLayout` to trigger immediate reflow.

#### 2.3 Feed Aging & Downward Compression
- **Target**: [`app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js) & [`app/static/index.css`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.css)
- **Change**:
  - Widgets at index ≥ 2 down the feed receive `.is-feed-aged`.
  - CSS limits `.is-feed-aged` height to ~200px with a subtle gradient fade and an expand icon `⤢` on the header.
  - Clicking `⤢` toggles full expansion.
  - When a new turn adds a card on top, previously active cards transition smoothly to compact mode.

#### 2.4 Asset Cache-Busting & Test Updates
- **Target**: [`app/static/index.html`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.html#L9-L27) & [`tests/test_envelope.mjs`](file:///home/lazycat/github/projects/sun/HTML-Notes/tests/test_envelope.mjs#L143-L154)
- **Change**:
  - Bump `index.js?v=3.3`, `index.css?v=3.2`, `hud-theme.css?v=1.2`.
  - Update `tests/test_envelope.mjs` version assertions to match.

---

## 6. Verification Plan & Testable Claims

### Automated Tests
1. **Pytest Widget & Canvas Suite**:
   - Command: `.venv/bin/pytest tests/test_widgets.py tests/test_canvas_context.py tests/test_edge_case_fixes.py`
   - Validates that widget generation and canvas serialization remain intact.
2. **Node Envelope & Hygiene Suite**:
   - Command: `node --test tests/test_envelope.mjs`
   - Validates envelope stripping, cache-busting version bumps, and masonry integration.
3. **New Prepend & Density Unit Tests**:
   - Create `tests/test_feed_order.py`:
     - Test that sequential widget insertions into canvas place newest widget at index 0 of `#dashboard-grid`.
     - Test that batch widget placement preserves relative order at top.
   - Create `tests/test_canvas_density.mjs`:
     - Test that `WidgetLayout.apply` sorts unranked new widgets before existing widgets.
     - Test that `CanvasDensity` assigns `.density-compact` when widget count ≥ 3.

### Manual Verification Workflow
1. Start local development instance: `npm run dev` or run in test environment.
2. Open Canvas in browser.
3. Ask the agent a query (e.g., "What is the weather in Tokyo?"). Confirm the in-flight envelope pops up at the very top.
4. Confirm the resulting widget box appears at the top.
5. Ask a second query (e.g., "Give me a checklist for morning routine").
6. Confirm the checklist pops up on **top** of the weather widget, pushing the weather widget down.
7. Observe that no scrolling down was required.
8. Add 2–3 more widgets and verify that the auto-resize system adjusts columns and heights so all boxes fit comfortably on screen.

---

## 7. Rollback & Safety Plan
- Git worktree will be used for all implementation work.
- If any layout regression occurs, restoring `index.js`, `index.css`, `index.html`, and `message.py` from git returns the canvas to the append-at-bottom baseline.
- `localStorage['widget_order']` and `localStorage['widget_sizes']` remain backward compatible; invalid entries fall back to defaults.
