/**
 * Music Handoff State Machine: manages cross-tab rendezvous lifecycle.
 */
import { HANDOFF_STATES, createHandoffPayload } from "./music-contract.js";

export class HandoffStateMachine {
  constructor({
    baseUrl = "",
    pollIntervalMs = 700,
    timeoutMs = 60000,
    fetchFn = globalThis.fetch,
    onConfirmed = () => {},
  } = {}) {
    this.baseUrl = baseUrl;
    this.pollIntervalMs = pollIntervalMs;
    this.timeoutMs = timeoutMs;
    this.fetchFn = fetchFn;
    this.onConfirmed = onConfirmed;

    this.state = HANDOFF_STATES.IDLE;
    this.handoffId = null;
    this.expectedTrackId = null;
    this.pollTimer = null;
    this.startTime = 0;
  }

  generateHandoffId() {
    return "h_" + Math.random().toString(36).slice(2, 11) + "_" + Date.now().toString(36);
  }

  startHandoff({ trackId, position = 0, title = "", artist = "", genre = "" }) {
    this.cancelHandoff();
    this.handoffId = this.generateHandoffId();
    this.expectedTrackId = trackId;
    this.state = HANDOFF_STATES.SENDER_WAITING;
    this.startTime = Date.now();

    // Initial PUT
    this.publishPosition({ trackId, position, title, artist, genre });

    // Start polling loop
    this.pollTimer = setInterval(() => {
      this.pollStatus();
    }, this.pollIntervalMs);

    return this.handoffId;
  }

  async publishPosition({ trackId, position = 0, title = "", artist = "", genre = "" }) {
    if (!this.handoffId || this.state !== HANDOFF_STATES.SENDER_WAITING) return;

    try {
      const payload = createHandoffPayload({
        handoffId: this.handoffId,
        trackId,
        position,
        title,
        artist,
        genre,
        state: HANDOFF_STATES.SENDER_WAITING,
      });

      await this.fetchFn(`${this.baseUrl}/api/handoff/${encodeURIComponent(this.handoffId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      // Fail safe: network error keeps sender playing
    }
  }

  async pollStatus() {
    if (!this.handoffId || this.state !== HANDOFF_STATES.SENDER_WAITING) return;

    if (Date.now() - this.startTime > this.timeoutMs) {
      this.state = HANDOFF_STATES.TIMED_OUT;
      this.cancelHandoff();
      return;
    }

    try {
      const res = await this.fetchFn(`${this.baseUrl}/api/handoff/${encodeURIComponent(this.handoffId)}`);
      if (!res.ok) return;

      const data = await res.json();
      if (data && data.started === true) {
        // Confirm track match if provided
        if (data.track && data.track !== this.expectedTrackId) {
          return;
        }

        this.state = HANDOFF_STATES.RECEIVER_PLAYING;
        this.stopPolling();
        this.onConfirmed(data);
      }
    } catch (err) {
      // Fail safe: network error continues waiting
    }
  }

  cancelHandoff() {
    this.stopPolling();
    if (this.state === HANDOFF_STATES.SENDER_WAITING) {
      this.state = HANDOFF_STATES.CANCELLED;
    }
    this.handoffId = null;
    this.expectedTrackId = null;
  }

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }
}

if (typeof globalThis !== "undefined") {
  globalThis.HandoffStateMachine = HandoffStateMachine;
}
