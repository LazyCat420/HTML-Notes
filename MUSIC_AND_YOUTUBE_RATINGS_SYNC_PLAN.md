# Plan: Bidirectional Music Player & YouTube Wallgarden Rating Integration

**Target Repositories:**
- [`HTML-Notes`](file:///home/lazycat/github/projects/sun/HTML-Notes) (Owner: `LazyCat420`)
- [`music-player`](file:///home/lazycat/github/projects/sun/music-player) (Owner: `LazyCat420`)
- [`youtube-wallgarden`](file:///home/lazycat/github/projects/sun/youtube-wallgarden) (Owner: `LazyCat420`)

**Plan Date:** 2026-09-23  
**Status:** PROPOSED (Brainstorming / Plan Mode — No Implementation Until Approved)  
**Standard:** Verified-Claim Plan Methodology (VCPM) & Evidence-Driven Blueprint

---

## 1. Problem Statement

Currently, when users listen to music via the `mini_music_player` widget or watch YouTube videos via the `youtube_player` widget inside `HTML-Notes`:
1. The `mini_music_player` widget displays tracks discovered from `music-player`, but offers **no UI affordance to like or rate** songs (1–5 stars or favorites). Song interactions on the canvas do not feed back into `music-player`'s `favorites` or `listening_history` tables. As a result, the two applications do not feed each other's recommendation signals.
2. The `youtube_player` widget embeds and plays YouTube streams, but offers **no UI affordance to like or rate** videos. Likes are not transmitted to `youtube-wallgarden`'s MongoDB-backed sync service (`/sync/global`), which leaves Wallgarden's taste profile, liked video registry, and learned ontology graph unaware of videos enjoyed on the canvas.

The goal is to create a seamless, bidirectional feedback loop between `HTML-Notes` and both companion applications so that curation on the canvas enriches the standalone apps and vice-versa.

---

## 2. Requirements Matrix & Acceptance Criteria

| ID | Requirement | Acceptance Criteria | Verification Method |
|---|---|---|---|
| **REQ-M1** | Music Widget Favorite Toggle | Clicking Heart icon on `mini_music_player` sends `POST :8002/api/favorites` with track metadata (`path: "youtube://" + id`, `title`, `artist`, `rating: 5`), toggling active heart UI. | Playwright / Unit test asserting mock/live endpoint receives payload. |
| **REQ-M2** | Music Widget Star Rating | Selecting 1–5 stars sends `POST :8002/api/favorites` (or `/api/tracks/metadata`) with the rating integer. Rating 0 removes from favorites (`DELETE :8002/api/favorites`). | Unit test in `tests/test_music_ratings_sync.mjs`. |
| **REQ-M3** | Music Initial State Hydration | When a track loads in `mini_music_player`, the widget queries `GET :8002/api/favorites/check` (with path/youtube_id) and displays existing favorite/rating status. | Live API test & mock component test. |
| **REQ-M4** | Music Template Twin Parity | Both `factory.py` (`render_mini_music_player`) and `index.js` (self-heal template) contain identical like/rate markup and bindings. | Automated AST / regex parity test in `tests/test_music_player_fixes.mjs`. |
| **REQ-Y1** | YouTube Widget Like Button | Clicking Thumbs Up on `youtube_player` sends rating `r: 5` and video metadata (`id`, `title`, `thumbnailUrl`, timestamp) to Wallgarden sync. | API test against `/api/wallgarden/rate`. |
| **REQ-Y2** | Wallgarden Sync Ingestion | Wallgarden's sync service records the rating under `ratings[videoId]` in MongoDB document `_id = "global"` with per-key LWW timestamp. | Verification against `http://10.0.0.16:8007/sync/global`. |
| **REQ-Y3** | YouTube Template Twin Parity | Both `factory.py` (`render_youtube_player`) and `index.js` (self-heal template) contain identical like button markup and bindings. | Automated test checking `factory.py` and `index.js`. |
| **REQ-Y4** | Proxy Endpoint Resilience | `HTML-Notes` backend exposes `POST /api/wallgarden/rate` so client-side widgets do not require direct LAN cross-origin MongoDB or CORS assumptions. | Integration test in `tests/test_wallgarden_proxy.py`. |

---

## 3. Claim Classification & Evidence Base

All factual claims must be verifiable per the Verified-Claim Plan Methodology (≥80% Verified/Testable, 0 Unverifiable).

| Claim ID | Classification | Statement | Primary Evidence Source |
|---|---|---|---|
| **CLAIM-01** | `VERIFIED FACT` | `music-player` backend exposes `POST /api/favorites` and `DELETE /api/favorites` storing `path`, `title`, `artist`, `rating`, `youtube_id` in SQLite table `favorites`. | [`music-player/apps/api/app/routers/favorites.py:48-73`](file:///home/lazycat/github/projects/sun/music-player/apps/api/app/routers/favorites.py#L48-L73) and [`database.py:81-94`](file:///home/lazycat/github/projects/sun/music-player/apps/api/app/database.py#L81-L94). |
| **CLAIM-02** | `VERIFIED FACT` | `music-player` exposes `GET /api/favorites/check?path=...` returning `{"is_favorite": boolean, "path": str}`. | [`music-player/apps/api/app/routers/favorites.py:75-80`](file:///home/lazycat/github/projects/sun/music-player/apps/api/app/routers/favorites.py#L75-L80). |
| **CLAIM-03** | `VERIFIED FACT` | `music-player` CORS allows requests from `http://10.0.0.16:8035` and `http://localhost:8035`. | [`music-player/apps/api/app/main.py:78-80`](file:///home/lazycat/github/projects/sun/music-player/apps/api/app/main.py#L78-L80). |
| **CLAIM-04** | `VERIFIED FACT` | `music-player` is running live at `http://10.0.0.16:8002` and currently holds 337 favorites. | Live execution test: `curl -s http://10.0.0.16:8002/api/favorites/stats` returned `{"total": 337, ...}`. |
| **CLAIM-05** | `VERIFIED FACT` | `youtube-wallgarden` runs a Python sync service backed by MongoDB `wallgarden.state` (`_id = "global"`). | [`youtube-wallgarden/sync-service/main.py:32-48`](file:///home/lazycat/github/projects/sun/youtube-wallgarden/sync-service/main.py#L32-L48). |
| **CLAIM-06** | `VERIFIED FACT` | `youtube-wallgarden` exposes `GET/PUT/POST /sync/global` with permissive CORS `allow_origins=["*"]`. | [`youtube-wallgarden/sync-service/main.py:54-59`](file:///home/lazycat/github/projects/sun/youtube-wallgarden/sync-service/main.py#L54-L59). |
| **CLAIM-07** | `VERIFIED FACT` | `youtube-wallgarden` live sync service is reachable at `http://10.0.0.16:8007/sync/global`. | Live execution test: `curl -s http://10.0.0.16:8007/sync/global` returned valid JSON state with existing video ratings. |
| **CLAIM-08** | `VERIFIED FACT` | In `youtube-wallgarden`, ratings with `r: 5` update `state.likedVideos` and immediately increment affinity weights in `ontologyGraph`. | [`youtube-wallgarden/app/app.js:2036-2062`](file:///home/lazycat/github/projects/sun/youtube-wallgarden/app/app.js#L2036-L2062). |
| **CLAIM-09** | `VERIFIED FACT` | `HTML-Notes` defines `mini_music_player` in `app/widgets/factory.py:1259` with a self-heal twin in `app/static/index.js:3141`. | [`HTML-Notes/app/widgets/factory.py:1259`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py#L1259), [`HTML-Notes/app/static/index.js:3141`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L3141). |
| **CLAIM-10** | `VERIFIED FACT` | `HTML-Notes` defines `youtube_player` in `app/widgets/factory.py:1374` with a self-heal twin in `app/static/index.js:3279`. | [`HTML-Notes/app/widgets/factory.py:1374`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py#L1374), [`HTML-Notes/app/static/index.js:3279`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js#L3279). |
| **CLAIM-11** | `VERIFIED FACT` | All 3 repositories (`HTML-Notes`, `music-player`, `youtube-wallgarden`) have origin URLs pointing to `LazyCat420` GitHub profile. None belong to Rodrigo Barraza. | `git remote -v` output confirmed all three repos are owned by `LazyCat420`. |
| **CLAIM-12** | `TESTABLE CLAIM` | Adding like/rate controls into `mini_music_player` and `youtube_player` won't trigger DOM hijacking by `delegateAsks` or canvas event handlers. | Covered by existing click boundary tests in `HTML-Notes/tests/test_music_player_fixes.mjs`. |
| **ASSUMPTION-01** | `ASSUMPTION` | User prefers a simple Heart toggle for quick liking in the music player, plus optional star rating (1–5) or rating dropdown, rather than cluttering the compact 280px card. | Validation: Ask user preference in follow-up questions. |
| **ASSUMPTION-02** | `ASSUMPTION` | A backend proxy in `HTML-Notes` (`/api/wallgarden/rate`) is preferable to direct browser-to-8007 fetches because it keeps URL configuration centralized in `.env`/`config.py`. | Validation: Backend proxy avoids exposing internal port 8007 if client is behind a reverse proxy. |

---

## 4. Architectural Design & Data Flow

```mermaid
flowchart TD
    subgraph HTML_Notes["HTML-Notes (:8035)"]
        subgraph MusicWidget["mini_music_player"]
            M_UI["Track Card UI\n(Heart & 1-5 Stars)"]
            M_Alpine["musicPlayerWidget (widgets.js)\nisFavorite, userRating, toggleFavorite()"]
        end

        subgraph YouTubeWidget["youtube_player"]
            Y_UI["Video Header UI\n(Thumbs Up / Like)"]
            Y_Alpine["youtubePlayerWidget (widgets.js)\nisLiked, toggleLike()"]
        end

        API_Proxy["HTML-Notes API (api.py)\nPOST /api/wallgarden/rate\nGET /api/wallgarden/rating/{id}"]
    end

    subgraph MusicPlayer["music-player (:8002 / :3232)"]
        MP_Favs["POST /api/favorites\nDELETE /api/favorites\nGET /api/favorites/check"]
        MP_DB[("SQLite music_library.db\n- favorites table\n- listening_history")]
        MP_UI["Music Player Web UI\n(FAVS tab & endless mix)"]
    end

    subgraph Wallgarden["youtube-wallgarden (:8007)"]
        WG_Sync["FastAPI Sync Service\nPUT /sync/global"]
        WG_Mongo[("MongoDB wallgarden.state\n_id = 'global'")]
        WG_Engine["Wallgarden App\n- Liked Videos\n- Ontology Graph (topic affinity)\n- Smart Feed Curation"]
    end

    %% Flows
    M_UI -->|Click Like / Rate| M_Alpine
    M_Alpine -->|POST /api/favorites\npath=youtube://id| MP_Favs
    MP_Favs -->|Write record| MP_DB
    MP_DB -->|Powers| MP_UI
    MP_Favs -.->|Check on track change| M_Alpine

    Y_UI -->|Click Like| Y_Alpine
    Y_Alpine -->|POST /api/wallgarden/rate| API_Proxy
    API_Proxy -->|PUT /sync/global\nfields.ratings[videoId]| WG_Sync
    WG_Sync -->|LWW Merge| WG_Mongo
    WG_Mongo -->|Polls every 45s / on boot| WG_Engine
    WG_Engine -.->|suggest_videos action| HTML_Notes
```

### 4.1 Music Player Integration Details (Aligned with User Choice)

- **UI Controls**:
  - **Quick Favorite Heart Button**: Clicking the Heart toggles favorite status (rating 5 by default). When active, it fills with a vibrant red/pink glow (`font-variation-settings: 'FILL' 1`).
  - **Compact 5-Star Rating Bar**: An inline or compact 5-star rating row allowing precise rating (1 to 5 stars). Hover states highlight candidate rating, and clicking an already-active star resets rating to 0 (clearing favorite).
- **Path Format Compatibility**:
  - In `music-player`, YouTube tracks in the frontend use `path = "youtube://" + videoId`, `youtube_id = videoId`, `source = "youtube"`.
  - In `HTML-Notes`, `asTrack(v)` sets `path = v.id`. When dispatching to `music-player`, the widget normalizes:
    `const trackPath = track.isYoutube ? `youtube://${track.id}` : track.path;`
  - Payload to `POST ${this.base}/api/favorites`:
    ```json
    {
      "path": "youtube://oAkLSJNr5zY",
      "title": "Song Title",
      "artist": "Artist Name",
      "album": "",
      "source": "youtube",
      "youtube_id": "oAkLSJNr5zY",
      "rating": 5,
      "duration": 214
    }
    ```
  - Payload to `DELETE ${this.base}/api/favorites` (or rating 0):
    ```json
    {
      "path": "youtube://oAkLSJNr5zY"
    }
    ```
- **Initial State Check**:
  - In `music-player/apps/api/app/routers/favorites.py`, update `check_favorite(path: str)` to return both `is_favorite` and `rating`:
    ```python
    @router.get("/check")
    def check_favorite(path: str):
        fav = db.get_favorite_by_path(path)  # returns dict with rating or None
        return {
            "is_favorite": fav is not None,
            "rating": fav.get("rating", 0) if fav else 0,
            "path": path
        }
    ```
  - When `currentTrack` changes in `musicPlayerWidget`, call `checkFavoriteStatus()` to update the heart and active stars without blocking audio playback.

### 4.2 YouTube Wallgarden Integration Details (Aligned with User Choice)

- **UI Controls**:
  - **Thumbs Up (+5)**: Marks video as liked in Wallgarden (`r: 5`), adds to `state.likedVideos`, increases positive topic affinity in Wallgarden's ontology graph.
  - **Thumbs Down (-5)**: Marks video as disliked in Wallgarden (`r: -5`), applies downvote penalty in Wallgarden's recommendation scorer, and adds negative affinity to the video's topics so similar spam/unwanted videos are filtered out.
- **Rating Sync Payload Contract**:
  - In `HTML-Notes`, add `WALLGARDEN_URL = os.getenv("WALLGARDEN_URL", "http://10.0.0.16:8007")` in [`app/config.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/config.py).
  - In `app/routes/api.py`, implement `POST /api/wallgarden/rate`:
    ```python
    @router.post("/api/wallgarden/rate")
    async def api_wallgarden_rate(req: WallgardenRateRequest):
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        payload = {
            "fields": {
                "ratings": {
                    req.video_id: {
                        "r": req.rating,  # 5 for like, -5 for dislike, 0 for clear
                        "t": ts,
                        "v": {
                            "id": req.video_id,
                            "title": req.title,
                            "channelName": req.channel or "YouTube Curation",
                            "thumbnailUrl": f"https://i.ytimg.com/vi/{req.video_id}/hqdefault.jpg",
                            "published": ts
                        }
                    }
                }
            }
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.put(f"{WALLGARDEN_URL}/sync/global", json=payload)
            return {"ok": res.is_success, "status": res.status_code}
    ```
- **Wallgarden Reaction**:
  - Wallgarden sync service writes directly to MongoDB `wallgarden.state`.
  - Any open Wallgarden browser tab polls `/sync/global` every 45s (and flushes on boot).
  - Once ingested:
    - If `r == 5`: `state.videoRatings[videoId] = 5`, video added to `state.likedVideos`, `graphProcessRating(..., +1)` raises topic affinity.
    - If `r == -5`: `state.videoRatings[videoId] = -5`, `graphProcessRating(..., -1)` penalizes topic affinity and helps trigger topic/channel parole burns.
    - Subsequent `youtube-wallgarden.suggest_videos` actions called from `HTML-Notes` are now strictly guided by these feedback signals.

---

## 5. Concrete Implementation Steps

### Step 1: Music Player Backend Enhancement (`music-player`)
- **File**: [`music-player/apps/api/app/database.py`](file:///home/lazycat/github/projects/sun/music-player/apps/api/app/database.py)
  - Add `get_favorite(path: str) -> Optional[Dict]` returning `{ "path", "rating", "title", "artist", ... }`.
- **File**: [`music-player/apps/api/app/routers/favorites.py`](file:///home/lazycat/github/projects/sun/music-player/apps/api/app/routers/favorites.py)
  - Update `GET /api/favorites/check` to include `"rating": fav.get("rating", 0)`.
- **Verification**: Run `curl -s "http://10.0.0.16:8002/api/favorites/check?path=..."` to verify JSON shape.

### Step 2: HTML-Notes Backend Wallgarden Proxy (`HTML-Notes`)
- **File**: [`HTML-Notes/app/config.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/config.py)
  - Add `WALLGARDEN_URL = os.getenv("WALLGARDEN_URL", "http://10.0.0.16:8007")`.
- **File**: [`HTML-Notes/app/routes/api.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/routes/api.py)
  - Add `POST /api/wallgarden/rate` and `GET /api/wallgarden/rating/{video_id}` endpoints.
- **Verification**: Add unit tests in `tests/test_wallgarden_proxy.py` testing successful forward, bad payload, and upstream timeout handling.

### Step 3: Music Player Widget UI & Logic (`HTML-Notes`)
- **File**: [`HTML-Notes/app/widgets/factory.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py) (`render_mini_music_player`)
  - In the Track Info row (next to title/artist), add Heart like button (`@click.stop="toggleFavorite()"`) with dynamic fill and tooltip.
  - Add 5-star rating widget (`@click.stop="setRating(star)"`) or expandable star rating pill.
- **File**: [`HTML-Notes/app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js)
  - Mirror the identical HTML markup in the self-heal template (lines 3141–3200).
- **File**: [`HTML-Notes/app/static/js/widgets.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/js/widgets.js) (`musicPlayerWidget`)
  - Add properties: `isFavorite: false`, `userRating: 0`, `ratingPending: false`.
  - Add methods: `toggleFavorite()`, `setRating(val)`, `checkFavoriteStatus()`.
  - Trigger `checkFavoriteStatus()` inside `loadTrack()` whenever the active track changes.
- **Verification**: Run `node --test tests/test_music_player_fixes.mjs` and new test `tests/test_music_ratings_sync.mjs`.

### Step 4: YouTube Player Widget UI & Logic (`HTML-Notes`)
- **File**: [`HTML-Notes/app/widgets/factory.py`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/widgets/factory.py) (`render_youtube_player`)
  - In Title Bar, add a Thumbs Up Like button (`@click.stop="toggleLike()"`) next to the title with tooltip `"Like video (syncs to Wallgarden)"`.
- **File**: [`HTML-Notes/app/static/index.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js)
  - Mirror the identical HTML markup in the self-heal template (lines 3279–3320).
- **File**: [`HTML-Notes/app/static/js/widgets.js`](file:///home/lazycat/github/projects/sun/HTML-Notes/app/static/js/widgets.js) (`youtubePlayerWidget`)
  - Add properties: `isLiked: false`, `likePending: false`.
  - Add methods: `toggleLike()`, `checkLikeStatus()`.
  - In `playById()`, reset and call `checkLikeStatus()`.
- **Verification**: Run unit tests asserting template parity and rating payload construction.

### Step 5: Test Parity, Documentation, & Git Integration
- Update documentation chapters in `HTML-Notes/docs/` detailing the new feedback loops.
- Run full test suite using `testrun -- pytest` and `node --test`.
- Create clean worktrees in `HTML-Notes` and `music-player`, validate, commit, merge to main, and redeploy containers via deploy-kit.

---

## 6. Risks, Assumptions, & Mitigations

| Risk / Assumption | Impact | Mitigation Strategy |
|---|---|---|
| **R1: Network latency / offline container** | If `music-player` (:8002) or `wallgarden` (:8007) is slow or restarting, widget UI could stutter or lag. | Perform all network sync asynchronously in the background. Use optimistic UI updates in Alpine so the button responds immediately (<50ms). |
| **R2: Self-heal template drift** | If `factory.py` is updated but `index.js` is missed, canvas rehydration strips the like buttons. | Enforce template equality through automated regex test in `tests/test_music_player_fixes.mjs`. |
| **R3: Click bubbling / canvas drag conflict** | Clicking a star or like button might trigger canvas widget dragging or `openInFullPlayer` navigation. | Explicitly use `@click.stop` on all interactive buttons and ensure `delegateAsks` ignores widget rating actions. |
| **R4: Video metadata completeness** | At initial embed, only `videoId` might be known (channel or full title might be empty until iframe/API resolves). | Provide fallback metadata (`title: title || "YouTube Video"`, `channelName: "YouTube Curation"`) so MongoDB schema receives valid fields. |

---

## 7. Verification & saboteur Plan (6 Layers)

1. **Artifact Layer**: Inspect `git diff` to ensure zero static passwords or secrets are introduced (Pre-commit secret verification gate).
2. **Unit Layer**:
   - `music-player`: Test `db.get_favorite(path)` and `/api/favorites/check`.
   - `HTML-Notes`: Test template parity between `factory.py` and `index.js` for both widgets.
3. **Integration Layer**:
   - Invoke `/api/wallgarden/rate` with a test video ID and verify `http://10.0.0.16:8007/sync/global` reflects the updated LWW record.
   - Invoke `POST :8002/api/favorites` and confirm the row is created in SQLite `favorites`.
4. **Workflow Layer**:
   - Click like on a song in `mini_music_player` -> open `http://10.0.0.16:3232` -> confirm track appears in the "FAVS" tab.
   - Click like on a video in `youtube_player` -> open `http://10.0.0.16:8007` -> confirm video appears in "Liked Videos".
5. **Operational Layer**:
   - Validate container health and responsiveness on NAS ports 8035, 8002, and 8007.

---

## 8. Rollback Plan

- If any regressions occur in `HTML-Notes` or `music-player`, the changes are fully modular and isolated to the widget templates and proxy endpoints:
  - Revert the worktree commits cleanly (`git revert HEAD`).
  - Redeploy containers using `npm run deploy` inside the respective directories.
  - Stored canvas widget state remains unaffected because widget layout IDs and base configs remain backwards compatible.

---

*Plan prepared per workspace guidelines. No implementation has been started. Ready for user feedback and clarification.*
