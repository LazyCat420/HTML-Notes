# Music Handoff & Synchronization Operational Guide

Operational manual, compatibility contract, environment configuration, and runbook for cross-tab audio synchronization between **HTML-Notes** and **music-player**.

## Architecture & Communication Flow

```
+--------------------------+                         +--------------------------+
|  HTML-Notes Widget       |                         |  music-player Web App    |
|  (Origin: :8035)         |                         |  (Origin: :3232)         |
+------------+-------------+                         +------------+-------------+
             |                                                    |
             | 1. User clicks title / external player icon        |
             |--------------------------------------------------->| 2. Deep-link opens tab
             |                                                    |    with handoffId
             | 3. PUT /api/handoff/{id} (publishes live pos)      |
             |--------------------------------------------------->|
             |                                                    | 4. Restores track/pos
             |                                                    |    (Blocked by autoplay)
             | 5. GET /api/handoff/{id} (polls status)            |
             |<---------------------------------------------------|
             |                                                    | 6. User clicks Resume
             |                                                    |    Audio flows!
             |                                                    | 7. POST /api/handoff/{id}/started
             |                                                    |<---------------------
             | 8. Polling sees started=True                       |
             |    Sender pauses canvas audio.                     |
             |                                                    |
```

## Environment Configuration

| Variable Name | Purpose | Example / Deployed Value |
|---|---|---|
| `MUSIC_PLAYER_URL` | Base URL for `music-player` backend REST API | `http://10.0.0.16:3232` |
| `MUSIC_PLAYER_WEB_URL` | Base URL for `music-player` frontend Web UI | `http://10.0.0.16:3232` |

> [!IMPORTANT]
> **API vs Web URL Distinction**: `MUSIC_PLAYER_URL` is consumed by background `fetch`/`EventSource` calls (API endpoints). `MUSIC_PLAYER_WEB_URL` is used for user-facing `window.open()` deep links. Pointing `MUSIC_PLAYER_WEB_URL` at the API URL causes Chrome to render raw JSON instead of the player UI.

## Endpoint Contract (Protocol V1)

### 1. `PUT /api/handoff/{handoff_id}`
- **Sender Payload**:
  ```json
  {
    "version": 1,
    "handoffId": "h_9x2k1_l01a",
    "track": "yt_dQw4w9WgXcQ",
    "position": 14.5,
    "title": "Never Gonna Give You Up",
    "artist": "Rick Astley",
    "genre": "pop",
    "state": "sender_waiting",
    "updatedAt": 1755500000
  }
  ```
- **Response**: `200 OK` with updated handoff record.

### 2. `GET /api/handoff/{handoff_id}`
- **Response**:
  ```json
  {
    "found": true,
    "started": false,
    "track": "yt_dQw4w9WgXcQ",
    "position": 14.5,
    "started_position": null
  }
  ```

### 3. `POST /api/handoff/{handoff_id}/started`
- **Receiver Payload**:
  ```json
  {
    "position": 14.8
  }
  ```
- **Response**: `200 OK` with `started: true`.

### 4. `GET /api/youtube/playable/{video_id}`
- **Response**:
  ```json
  {
    "video_id": "dQw4w9WgXcQ",
    "playable": true,
    "cached": true
  }
  ```

## In-Memory Handoff State & Deployment Topology

- `music-player` stores handoff records in an in-memory Python dictionary with a 300-second TTL.
- **Single-Instance Assumption**: A single `music-player` container replica handles handoff state. If `music-player` restarts during an active handoff, the record is lost, and the sender widget fails safe (keeps playing audio until manual pause).

## Runbook: "Widget opened player but audio did not transfer"

1. **Verify `started` POST firing**: Open DevTools Network tab on `music-player` tab. Confirm `POST /api/handoff/{id}/started` returned `200 OK`.
2. **Verify CORS headers**: Ensure `music-player` returns `Access-Control-Allow-Origin: *` or permits `HTML-Notes` origin (`:8035`).
3. **Verify Track ID Matching**: If `HTML-Notes` changed tracks while the handoff was in-flight, it ignores confirmation for old track IDs.
4. **Check YouTube Stream Playability**: If YouTube refuses the CDN stream, `music-player` will trigger dead-track auto-skip or failover.
