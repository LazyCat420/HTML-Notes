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

1. **The position is read BEFORE the pause.** Reversed, the handoff still
   works and still pauses — it just always hands over `0`. Pinning a non-zero
   `t` is what catches it.
2. **The pause must not depend on `window.open`'s return.** With `noopener`
   the handle is `null` even on success, so `if (win) pause()` leaves both
   players running at once.
3. **Both template twins need the handler.** `app/widgets/factory.py` renders
   the widget and `app/static/index.js` re-renders it during self-heal, so a
   handler in only one is a click that works until the page rehydrates.

`window.open` is called synchronously inside the click, which is what keeps it
inside the user gesture and out of the popup blocker.

A **local-library** track opens the app with no `track` param: it is
identified by a filesystem path that means nothing to a deep link, so there is
nothing to hand over.

## Verified live

`scripts/music_handoff_check.py` (live check, not CI), 11/11 against the
deployed stack: the click opens one tab on **:3232**, carrying the same track
id, the live position, and the title/artist/genre the card was showing; the
canvas widget ends up paused, still on the canvas, with its queue intact.

The receiving half is verified separately by
`music-player/scripts/deeplink_handoff_check.py` (9/9): the link plays the
requested id at ~51s, and when autoplay is refused a card naming `0:51`
appears and one click starts playback there.

## Known limits

- **Not gapless.** The new tab needs a second or two to load and buffer. The
  position is exact; the silence between tabs is not removable.
- **Autoplay may be refused.** `:8035 -> :3232` is cross-origin, so the new
  tab inherits no user gesture. That path is handled on the music-player side
  with a one-click resume card that lands on the same second.
- **~30% of YouTube tracks will not stream at all right now**, in the handoff
  *and* in the widget. This is not a handoff bug — the scraper/CDN 403s the
  open-ended `Range: bytes=0-` that every `<audio>` element opens with, while
  closed ranges succeed. Measured 2026-08-17 on a 10-track jazz mix: 7
  playable, 3 dead (`K8mvBkmbMMg`, `GrQGKHZ5KmE`, `SBkZ4WCpo7s`). Details and
  history in `music-player/AUDIO_PIPELINE.md`; a handoff that lands on one of
  these now says so instead of silently playing a different song.
