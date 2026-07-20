# Handoff — 2026-07-20 (research reliability + glitch reveal + image framing)

## Glitch reveal on in-place updates

A follow-up rewriting a card now scrambles the text into random glyphs and
resolves the new answer out of the noise. The outgoing wording is captured BEFORE
the node swap; old and new rarely align, so the un-animated region reads as
jumble rather than a stale answer — which is the intended effect.

Text NODES are rewritten in place, nothing is wrapped. That removes an entire
hazard class: the earlier span version had to be unwrapped afterwards because the
server adopts the client canvas as canonical and leftover markup would have been
baked in and changed the widget's `data-sig`. With nothing added there is nothing
to leak. `finishGlitches()` is called from `getCleanedCanvasHtml`, so a request
sent mid-glitch can never ship noise as the widget's real content.

Character count is preserved every frame and whitespace never scrambles, so the
text holds its shape and the card never reflows.

Tuning knobs at the top of the block: `GLITCH_MIN_MS` / `GLITCH_MAX_MS` (550-1500),
`GLITCH_MS_PER_CHAR`, and `GLITCH_CHAR_LIFE` (0.28 — smaller is a crisper
left-to-right wipe, larger boils the whole card at once).

Verified live: 821 `characterData` mutations during one follow-up, samples like
`'[ab0} =% 2026 expertsr views,g hegbestrsandalse orthiking...'`, and no glyph
runs left in the settled text. `scripts/glitch_check.py`.

## Hero image framing

A fixed `h-32` letterbox cropped most photos to an unreadable strip — a shot of
footwear came through as a band of ankles. The replacement (`aspect-video` +
`max-h-52` + `object-cover object-top`) did not fix it: on a wide card the two
constraints collide into a ~3.4:1 strip, and cover then discards everything
outside it — a waterproof-hat product shot rendered as white space plus the
bottom sliver of a brim.

**There is no crop anchor that is correct for arbitrary hero photos.** Both
failures came from the same root cause: a full-width band has to pick a HEIGHT
for a photo whose aspect ratio is unknown, and every such guess either crops the
subject (`object-cover`) or strands it in letterbox slack (`object-contain`).

So the band is gone. The image is now an **article figure** — a bordered box
floated beside the prose, the way a figure sits in a Wikipedia or news article.
Fixing the WIDTH and letting height follow from `h-auto` means no height is ever
guessed and nothing is ever cropped. `max-height: 16rem` is only a safety stop
for a tall panorama; `object-contain` engages just in that rare case.

The float lives in `index.css` under a **container query**, not in a Tailwind
class. Tailwind's `sm:` keys off the VIEWPORT, but a card's width comes from its
grid span — with a fixed 192px figure a narrow card squeezed the prose into a
two-word ribbon and ran list rows under the picture. Below `30rem` of *card*
width the figure goes full-width on top instead, as news sites do on mobile.

Two subtleties worth keeping:
- The figure must be rendered INSIDE `.answer-prose`, not as a sibling. A float
  only wraps line boxes in its own formatting context; as a sibling the prose
  div would overlap it instead of flowing around it.
- The items list is `space-y-1`, deliberately NOT `flex flex-col`. A flex
  container avoids floats as one rigid block, which squeezed the entire list
  into a narrow column for its full height. As plain blocks each `<li>` avoids
  the float on its own — short rows beside the figure, full-width past it.
- Captions render only when the caller passes `image_caption`/`caption`.
  Defaulting to the title printed the card header twice.

Same no-crop reasoning applied to `image` widget tiles and `products` media (the
latter keeps `p-2` so price/badge chips don't sit on the product). Item thumbs
stay `object-cover`: at `w-14 h-14` they're avatars, where filling the box is
right. Cards with a photo are **460px** (down from 560 — a floated figure shares
rows with the text instead of displacing them).

Layout harness: `scratch/shot_card.py` renders the variants against the real
stylesheet and asserts no crop / no text overlap / float-vs-stack per width.
Note the overlap check must compare against `Range.getClientRects()` line boxes,
not block boxes — a paragraph's block box legitimately extends under a float.

## Widget identity, reuse and safe removal

Symptom: "traffic to san jose" → "traffic to sf" → "san ramon to san jose" made
THREE traffic widgets, and "close the san jose to sf map" closed a different one.
Three independent defects, all of which look like "the agent is dumb".

**1. Widgets were unidentifiable.** A widget root carried an opaque id
(`traffic-c20bc01b`), Tailwind classes and `data-sig` — nothing else. The title
existed only as `<h3>` prose. `generate_widget_html` now stamps
`data-widget-type` / `-title` / `-subtitle` on every widget, and
`_classify_canvas_widget` reads the stamp FIRST. This matters most for
`iframe_app`, which has no type class and no `x-data`: a directions map
("San Jose → SF") classified as `custom`, so the one widget the user called "the
map" was the one inventory line that didn't say map. `_summarize_canvas_for_history`
was already reading `data-widget-type` — it had just always fallen back to
`"widget"` because nothing emitted it.

**2. `canvas_read_dom` was blind.** It selected `.glass-card` and read
`.glass-card-title` — a convention NO factory widget uses (they are
`.widget-container` + bare `<h3>`), so it matched nothing and returned
`component_count: 0` on a full canvas. It also never returned widget ids, so the
agent could not construct a `#id` selector even on a hit. Its own tool schema
advertised "each with its widget_id, type, and what it shows" — the doc promised
what the server never implemented, the same seam class as
[[html-notes-prompt-server-seams]]. Now reuses `_iter_canvas_widgets` and returns
`id`/`selector`/`type`/`title`.

**3. `canvas_modify_dom` never verified what it destroyed.** `select_one(sel)` +
`decompose()` on whatever string the model produced, then `{"success": true}`
regardless of what died — while the ADD path validates ids through
`_resolve_agent_widget_id`. A remove matching several widgets is now refused and
returns the candidate ids; a successful one reports and logs `affected`
{id,type,title}. The live tool schema's `css_selector` examples actively taught
the bad habit (`.glass-card:first-child`, `h3`) and are now id-first.

**4. Reuse could not span the type fork.** `build_traffic_widget` returns `map`
when geocoding succeeds and `iframe_app` when it misses or the ask is "from A to
B". EVERY reuse signal (`SINGLETON_WIDGET_TYPES`, `_widget_on_canvas`,
`find_reuse_target`) is keyed on widget_type, so two traffic asks landing on
different sides of that fork could not see each other. Added
`find_existing_widget_by_id_prefix` + `SINGLETON_ROLE_PREFIXES` — identity by
semantic ROLE, which survives the type change — wired in as P3 of
`_resolve_widget_target` AND into `spawn_widget_stream`'s chain.

**Kept narrow deliberately:** a shared id prefix means "same slot on the canvas",
which is true for traffic/map/weather and NOT for e.g. `answer-` — widening it
would merge two unrelated cards.

**Testing note that mattered.** The first fix patched only the fast path and
looked right; live testing showed the canvas still growing 1757 → 3138 bytes
because these asks route through **tier2-local (the router)**, which has its own
id-resolution chain. Unit tests would not have caught it. Verify by driving
`/session/message` and counting distinct `traffic-<hex>` ids in the SSE stream —
four consecutive asks must all report the SAME id. `scratch/widget_targeting_check.py`
reproduces the canvas from the session logs.

## Clickable links in agent prose

`_md_inline` linkified `[label](url)` but never autolinked bare URLs — and bare
URLs are what agents actually write ("source: https://x.com/y"). Markdown proper
doesn't autolink either, so this read as correct until you tried to click one.

Anchors are **parked as placeholders** the moment they're built, before the
bare-URL pass runs. Without that, the autolinker matches the URL inside the
`href` the link pass just wrote and nests an `<a>` inside an attribute.

Trailing-punctuation rules, each earned from a wrong render:
- `.`/`,`/`;` go back to the prose, not into the href.
- A `)` is kept only when the URL opened its own paren, so
  `en.wikipedia.org/wiki/Capsicum_(genus)` survives but `(see https://y.com/b)`
  gives its paren back.
- Entities are stripped BEFORE punctuation: `esc()` turns a closing quote into
  `&quot;`, and taking `;` off as punctuation first leaves a mangled `&quot`.
- `&amp;` is never stripped — it's a legitimate query-string separator. Do not
  `esc()` the url again when building the anchor either; the whole string was
  escaped at the top of the function, so a second pass yields `&amp;amp;`.

Chat history was a separate seam: `marked` emits no `target`, and that sanitize
config didn't allow one, so those links navigated the whole app away in-tab and
lost the canvas. Now allowed AND set post-sanitize (decorating markup DOMPurify
has already cleared, never smuggling attributes past it).

Verified through the REAL DOMPurify build and the shipped canvas config —
`scratch/check_links.py` reads `CANVAS_DOMPURIFY_CONFIG` out of `index.js` so it
tests what ships, not a copy. `target` is in `ADD_ATTR`; DOMPurify's defaults
cover `href`/`rel` but NOT `target`, so removing it silently downgrades every
canvas link to in-tab.

## Item images: a first-class gap, not a side effect

Image population used to happen only inside description repair — it ran on the
subset of items that were ALSO missing a summary, and only when a quality gap had
already fired. `_data_card_quality_gap` had no concept of images at all, so a card
with good prose and zero pictures returned `""` (fine) and rendered as a column of
monogram letters.

Now any linked item without a picture gets one: real `og:image` via `_enrich_news`,
and the site's **favicon** as a guaranteed floor (`_favicon_for` — this existed but
was buried in the Google-News RSS path; it generalises to any URL). A favicon
identifies the source at a glance and beats a monogram letter. Coverage on a test
card went 0/3 → 3/3.

The probe passes `snippet: "x"` deliberately: `_enrich_news` only fills falsy
fields, so a pre-filled snippet keeps the pass strictly about images and stops it
overwriting descriptions the card already has. Pinned by a test.

Client-side, a dead `og:image` now retries once against the source's favicon
before falling back to a placeholder. Two competing broken-image handlers had to
be reconciled: the capture-phase gate on `#live-canvas` and `applyImageFallbacks`'
per-image bubble listener. The bubble one `replaceWith`s the node, which destroyed
the element the capture gate had just re-pointed at the favicon — so it now defers
while a retry is in flight. The gate's selector also still named `.hero-image`,
removed in the figure rework; it's `.data-card-figure` now.

## Traffic overlay: intent discarded, then re-inferred

The TomTom key, the tile proxy, `map_document_html` and the Leaflet layer were
ALL working — verified by pulling real tiles through the live proxy (green/red
flow pixels, HTTP 200). Traffic still never rendered.

`build_traffic_widget` re-derived intent with `re.search(r'\btraffic\b', msg)`.
But the router classifies "map of traffic in the east bay" as `type='traffic'`
and passes `query='east bay'` — the trigger word is stripped INTO the type. The
grep then missed, so a routed traffic ask fell through to the plain Google
directions embed and no tiles were ever requested.

Proven in the deployed container: `'traffic in oakland'` → overlay True, but
`'east bay'` (what the router actually passes) → `iframe_app`, overlay False.

Fix: `build_traffic_widget(msg, force_traffic=False)`. Both callers that already
established intent now assert it — the router branch (`wtype` IS the intent) and
the fast path (gated by `TRAFFIC_MAP_RE`).

**The general shape, worth watching for elsewhere in the router:** a caller
classifies intent, encodes it in the widget TYPE, discards the words that
carried it, and the callee tries to recover it from the leftover text. Any
`build_*_widget` that greps its message for a keyword the router already
consumed has this bug latently. Debug it by testing the callee with the string
the ROUTER passes, not the string the user typed — they are not the same.

**Open / not fixed:** the TomTom key is logged in plaintext on every tile fetch,
because httpx logs full request URLs at INFO and the key is a query param. The
proxy exists precisely so the key never reaches the browser — it lands in the
container logs instead. Also `_extract_directions_place` geocodes "the east bay"
to "East Bay Park"; the overlay is correct but centred on the wrong place.

## Earlier: typewriter reveal (REPLACED by the glitch above)

A follow-up that rewrites a card now prints the new wording in (~450-1400ms,
scaled to length) instead of swapping it instantly. Words are wrapped and faded
left-to-right; the text holds its final box from frame one so the masonry grid
below never jumps.

**Three things this cost, all worth knowing:**

1. **A fixed 260-word cap silently skipped the feature.** A real research
   data_card carries ~480 animatable words, so the reveal never ran on exactly
   the cards it exists for. Count a REAL widget before picking a bound.
2. **Running it synchronously inside `reconcileCanvas` does not work.** 488 words
   were wrapped and zero ever revealed; rAF was healthy (1025→1219 ticks), but
   the work after that loop (`WidgetLayout.apply`, `renderDynamicComponents`,
   Alpine init) replaces the node, so the reveal animated a detached element.
   Fix: defer two frames and re-find the widget BY ID.
3. **Skip live widgets by STRUCTURE, not by name.** Enumerating component names
   was wrong within minutes — the music player is `musicPlayerWidget`, not the
   `miniMusicPlayer` its widget_type suggests. A static card carries
   `x-data="{}"`, a live one carries a component call.

**The spans MUST NOT leak.** The server adopts the client canvas as canonical, so
a stray `span.tw-word` would be baked in permanently and would change the
widget's `data-sig`, making an unchanged widget look changed on every future
diff. Unwrapped on completion, on a safety timeout, AND stripped in
`getCleanedCanvasHtml`. `scripts/typewriter_check.py` asserts the DOM is
byte-identical to the server HTML afterwards — that is the property that matters,
not "does it animate".

Verified live: 500 words wrapped, 500 revealed, 0 left over, same widget id,
`data-sig` changed. `scripts/live_followup_check.py` drives the real app.

---

# Handoff — 2026-07-20 (research reliability)

**Current:** html-notes `main@79667f6`, deployed to synology 2026-07-20T03:32:36Z.
Suite **272 passing**. Research path healthy: 79 MCP tools, web search reachable.

## Headline

**Web search had been returning zero results for every query.** Everything people
were reporting about research asks — 18-call loops, multi-minute turns, narration
instead of a widget, an empty canvas — was downstream of that one fact.

| metric | before | after |
|---|---|---|
| real widget rendered | **1/3** | **5/5** |
| tool calls per turn | 18 | 3 |
| identical repeats | 17 | 1 |
| turn time | 280s+ | median **44s** |

Measured with `scripts/render_rate.py` (5 runs, same question, both times).

## Root cause

DuckDuckGo is unreachable from the NAS. From inside the container:

| host | result |
|---|---|
| google.com | HTTP 200 |
| en.wikipedia.org | HTTP 200 |
| **duckduckgo.com** | **ConnectTimeout** |
| **lite.duckduckgo.com** | **ConnectTimeout** |

Outbound is fine — DDG specifically is blocked. `web_search` tried `ddg-lite` then
`ddg-collector`, **both DuckDuckGo**, so it failed closed. The tool then told the
model *"Retry with a shorter, simpler query"* — so it retried, 10-18 times a turn.

**The repeat loop was the model doing exactly what the tool asked.** A loop guard
built first would have capped the retries and left research quietly broken.

## What shipped

| | Fix |
|---|---|
| **P0** | Brave Search API as the PRIMARY engine (`BRAVE_SEARCH_API_KEY` was already in the vault). Both DDG engines stay behind it, so this self-heals if DDG returns. Response normalized to the existing `{title,url,snippet}` shape — no call site changed. `<strong>` highlight markup stripped, ~1 req/s spacing for the free tier. |
| **P1** | `web_search_ex` returns `(results, all_engines_failed)`. An engine that answers at all — even with zero hits — proves the backend is alive. On a real outage the tool returns `is_error` and says **DO NOT RETRY**; a genuine miss allows exactly one reword. Boot check and `/health/app` both probe search. |
| **P2** | Runaway guard: 4 identical calls or 12 research calls ends the turn and drops to the fallback card. Keyed on `(tool, canonical_args)`. |
| **P5** | MCP self-heal: boot reconnects when the dependency check fails; a watchdog re-checks every 5 min (`MCP_WATCHDOG_SECONDS`). |
| — | `scripts/render_rate.py`, `scripts/sse_probe.py` |

## Deliberately NOT built, with evidence

- **P3 (prompt recency / nudge)** — its own gate was "only if still failing". Render
  rate is 5/5, so the narration behaviour was a *symptom of having no data*, not a
  prompting problem. Building it would have been cargo-cult.
- **P4b/c (buffer research results into the fallback card)** — **P4a is verified
  true**: `tool_execution` events DO carry `tool.result` (prism's own
  `BenchmarkService.ts:445-453` reads it that way from the standard agentic path).
  So it is feasible. But the fallback now fires ~never, so this optimizes a path
  that doesn't run. Revisit if the fallback starts appearing in logs.

## Gotchas

- **`GET /mcp-servers` is SCOPED BY HEADERS.** Without `x-project`/`x-username` it
  returns `[]` for ANY state. That is what made a single un-dialled connection look
  like an empty registry and sent an hour of debugging the wrong way. The real
  failure shape was `connected: false, toolCount: 0, lastError: null` — fixed by
  one `POST /mcp-servers/<id>/connect`. `/reconnect`, `/refresh`, `/reload` are 404.
- **Brave: API ≠ scraping.** Memory said "Brave is CAPTCHA-walled" — that was about
  scraping `search.brave.com`. `api.search.brave.com` is a keyed API, unrelated,
  and works fine.
- **`asyncio`/`time` are imported around line 1850**, well below the search code at
  ~280. A module-level `asyncio.Lock()` up there NameErrors on import; create it
  lazily.
- **Don't patch `m.asyncio.sleep` with a lambda that calls `asyncio.sleep`** — same
  module object, so it calls itself. Capture the real one first.
- **Use `asyncio.run` in tests, not `get_event_loop().run_until_complete`** — the
  latter inherits a closed loop from earlier async tests, so tests pass alone and
  fail in the suite.
- **`render_rate.py`'s "repeats" counts by tool NAME**, so legitimate deep research
  (5 different `read_page` URLs) shows as repeats. The runaway guard keys on
  **args**, which is why it correctly does not fire there.

## Known characteristics (not bugs)

A harder question ("best budget espresso machine") runs 9-19 tool calls and
90-180s — one search plus several `read_page` calls on different URLs. It renders
**3/3 REAL**. Slower, but that is real research, and the guard correctly leaves it
alone.

## Still open

- **Prism forces its core tools** (`search_web`, `read_url`, `create_artifact`) past
  our `enabledTools`, because `coreToolsLocked` defaults true and prism's
  `registerCustom` never reads it for a CUSTOM agent. Only fixable prism-side —
  parity with the lazy-agent fork (`AgentPersonaRegistry.ts:139-144`). **Rod's
  codebase, his call.**
- **Tailwind CDN self-hosting** — vendor the JIT runtime to `/static`. A static
  build stays off the table: `create_widget` renders model-authored `htmlContent`,
  so the agent invents utility classes at runtime and a content scan would purge
  them. Wants its own visual pass over all 14 widget types.
