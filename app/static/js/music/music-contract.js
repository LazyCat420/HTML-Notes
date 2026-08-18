/**
 * Canonical protocol contract and types for Music Handoff & Synchronization (V1).
 */
export const HANDOFF_PROTOCOL_VERSION = 1;

export const PLAYBACK_STATES = Object.freeze({
  IDLE: "idle",
  LOADING: "loading",
  PLAYING: "playing",
  PAUSED: "paused",
  PROBING: "probing",
  HANDOFF_PENDING: "handoff_pending",
  HANDOFF_COMPLETE: "handoff_complete",
  ERROR: "error",
});

export const HANDOFF_STATES = Object.freeze({
  IDLE: "idle",
  OPENING: "opening",
  SENDER_WAITING: "sender_waiting",
  RECEIVER_LOADED: "receiver_loaded",
  RECEIVER_BLOCKED: "receiver_blocked",
  RECEIVER_PLAYING: "receiver_playing",
  COMPLETED: "completed",
  CANCELLED: "cancelled",
  TIMED_OUT: "timed_out",
  FAILED: "failed",
});

export function createHandoffPayload({
  handoffId,
  trackId,
  position = 0,
  title = "",
  artist = "",
  genre = "",
  state = HANDOFF_STATES.SENDER_WAITING,
}) {
  return {
    version: HANDOFF_PROTOCOL_VERSION,
    handoffId,
    track: trackId,
    position: typeof position === "number" ? Math.max(0, position) : 0,
    title,
    artist,
    genre,
    state,
    updatedAt: Date.now(),
  };
}

if (typeof globalThis !== "undefined") {
  globalThis.MusicContract = {
    HANDOFF_PROTOCOL_VERSION,
    PLAYBACK_STATES,
    HANDOFF_STATES,
    createHandoffPayload,
  };
}
