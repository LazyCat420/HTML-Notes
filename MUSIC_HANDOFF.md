# Handing a track from the canvas mini player to the music-player app

Shipped 2026-08-17 (`6ce1714` here, `music-player@0b234ba` on the other side).

Clicking the song title/artist in the canvas music widget opens music-player
in a new tab, playing **the same track from the same second**, and stops
playing on the canvas so the two are not doubled up. The widget stays where it
is, so playback can be resumed locally instead.

## What was already true

The mini player was never a separate music system. It has always called
music-player's API for everything:

| What | Endpoint |
|---|---|
| Track discovery | `GET :8002/api/youtube/mix/{term}/stream?type=genre\|artist` (SSE) |
| Audio | `GET :8002/api/youtube/stream/{videoId}` |
| Failover search | `GET :8002/api/youtube/search?query=` |
| Last-resort library | `GET :8002/api/tracks` |

`MUSIC_PLAYER_URL` (`app/config.py`) is that **API**. The handoff needed a
second setting, `MUSIC_PLAYER_WEB_URL`, because opening a browser tab at
`:8002` serves JSON, not a player. It is pinned to `:3232` for the same reason
the App Hub pins it — the registry domain `music.braindeadbot.com` has no DNS
record.

## The link

```
http://10.0.0.16:3232/?track=<videoId>&t=<seconds>&title=..&artist=..&genre=..&autoplay=1
```

Metadata travels **in the URL** because the music-player backend has no
`videoId -> metadata` endpoint. Adding one would mean a scrape on every
handoff for data the sending side is already displaying.

## The three things that fail quietly

Each of these still opens a tab, so none of them is visible from the sending
side. All three are pinned by `tests/test_music_handoff.mjs`, which slices the
real `openInFullPlayer` out of `widgets.js` and runs it — a retyped copy would
keep passing after the original changed.

1. **The canvas does NOT stop on click.** It keeps playing and stops only when
   the other tab reports audio actually flowing — see the rendezvous below.
   Pausing on faith was the original design and it produced silence on BOTH
   sides.
2. **The position is read BEFORE the pause.** Reversed, the handoff still
   works and still pauses — it just always hands over `0`. Pinning a non-zero
   `t` is what catches it.
3. **The pause must not depend on `window.open`'s return.** With `noopener`
   the handle is `null` even on success, so `if (win) pause()` leaves both
   players running at once.
4. **Both template twins need the handler.** `app/widgets/factory.py` renders
   the widget and `app/static/index.js` re-renders it during self-heal, so a
   handler in only one is a click that works until the page rehydrates.

`window.open` is called synchronously inside the click, which is what keeps it
inside the user gesture and out of the popup blocker.

A **local-library** track opens the app with no `track` param: it is
identified by a filesystem path that means nothing to a deep link, so there is
nothing to hand over.

## Surviving a track the source refuses

`handleStreamError()` in `app/static/js/widgets.js`. Two details are easy to
get wrong, and `tests/test_music_dead_track_skip.mjs` pins both:

- **The skip must actively call `play()`.** A track that errors has usually
  already flipped `isPlaying` false via the `pause` listener, so
  `nextTrack()`'s own "resume if it was playing" does nothing and the widget
  walks the queue in silence. The first version of that test could not see
  this at all — its stubbed `nextTrack` supplied the resume itself, so the
  assertion passed on the harness rather than on the code.
- **`destroy()` sets `src=''`, which itself fires `error`.** Skipping on that
  would resurrect a widget the user just closed.

## The rendezvous (why the canvas knows when to stop)

Two different origins share no `localStorage` and no `BroadcastChannel`, so
the only channel is the music-player API, which both can reach:

| call | who | when |
|---|---|---|
| `PUT :8002/api/handoff/{id}` | canvas | every 700ms while waiting, parking its LIVE position |
| `GET :8002/api/handoff/{id}` | music-player | at boot, and again on the resume click |
| `POST :8002/api/handoff/{id}/started` | music-player | when audio is genuinely FLOWING (`playing`), never merely loaded |

`openInFullPlayer` mints the id, opens the tab with `&handoff=<id>`, and calls
`awaitHandoff`, which polls until `started` and then pauses. Records are
in-memory with a 5-minute TTL; losing one just means the canvas keeps playing.

**Cancellation is the sharp edge.** A deliberate play/pause, next, prev or
queue pick abandons the handoff — otherwise a late `started` would silence
music the listener had just chosen. The auto-advance behind a CDN-refused
track does NOT cancel (`nextTrack({auto:true})`), or a dead track would
sabotage a live handoff. The poll also re-checks ownership **after** its
`await`; a test caught a late response landing on a cancelled handoff.

## Verified live

Measured on the deployed stack with a **real browser autoplay policy** (the
earlier harness forced `--autoplay-policy=no-user-gesture-required` and so
could not see any of this):

```
PHASE 1 — autoplay blocked
   1.5s canvas t=5.7  playing   tab t=4.1 paused
   6.0s canvas t=10.9 playing   tab t=4.1 paused     <- canvas never went silent
PHASE 2 — listener clicks Resume
   1.5s canvas t=11.8 PAUSED    tab t=12.5 playing   <- picks up where it got to
   7.5s canvas t=11.8 paused    tab t=18.5 playing
```

`scripts/music_handoff_check.py` (live check, not CI), 11/11 against the
deployed stack: the click opens one tab on **:3232**, carrying the same track
id, the live position, and the title/artist/genre the card was showing; the
canvas widget ends up paused, still on the canvas, with its queue intact.

The receiving half is verified separately by
`music-player/scripts/deeplink_handoff_check.py` (9/9): the link plays the
requested id at ~51s, and when autoplay is refused a card naming `0:51`
appears and one click starts playback there.

## Known limits

- **Not gapless**, but no longer silent: the canvas plays until the other tab
  takes over, so the seam is a fraction of a second rather than dead air.
- **Autoplay is usually refused**, and that is now harmless. `:8035 -> :3232`
  is cross-origin, so the new tab inherits no user gesture and Chrome will not
  start audio there. The canvas keeps playing until the listener clicks the
  resume card, and only then hands over — at the position the music had
  reached by that click, not the one in the URL.
- **Most YouTube tracks are refused by the CDN right now** — in the handoff
  *and* in the widget, and not because of either. YouTube serves only the
  first ~1MB of a file to a client with no PO token and no cookie; the rest
  is 403 at every offset. Re-measured 2026-08-17 through the production path:
  **10 of 12 tracks on a live jazz mix were unplayable.** Full diagnosis,
  including what was ruled out, in `music-player/AUDIO_PIPELINE.md` item 4.
  A handoff that lands on one of these says so instead of silently playing a
  different song.

  **The widget now skips them** (`d27d05a`): a stream error advances to the
  next track and keeps playing, bounded at 8 consecutive failures so an
  all-dead queue reports rather than spinning. About one track in six still
  plays, and a genre queue holds ~100, so music plays again. Verified live —
  the widget skipped 2 dead tracks and settled on a playing one, after which
  the handoff carried a real live position rather than a parked one.

  This is a mitigation, not a fix. The fix is a populated
  `/app/cookies.txt` on the scraper (the plumbing exists; the file is 0
  bytes) or a PO token provider.
