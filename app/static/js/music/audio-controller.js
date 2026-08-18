/**
 * Audio Controller: wraps HTML5 <audio> element with event handling, error classification, and state tracking.
 */
import { PLAYBACK_STATES } from "./music-contract.js";

export class AudioController {
  constructor({
    audioElement = null,
    onPlaying = () => {},
    onPaused = () => {},
    onError = () => {},
    onEnded = () => {},
    onTimeUpdate = () => {},
  } = {}) {
    this.audio = audioElement || new Audio();
    this.playbackState = PLAYBACK_STATES.IDLE;
    this.lastError = null;

    this.onPlaying = onPlaying;
    this.onPaused = onPaused;
    this.onError = onError;
    this.onEnded = onEnded;
    this.onTimeUpdate = onTimeUpdate;

    this._bindEvents();
  }

  _bindEvents() {
    this.audio.addEventListener("playing", () => {
      this.playbackState = PLAYBACK_STATES.PLAYING;
      this.onPlaying();
    });

    this.audio.addEventListener("pause", () => {
      if (this.playbackState === PLAYBACK_STATES.PLAYING) {
        this.playbackState = PLAYBACK_STATES.PAUSED;
      }
      this.onPaused();
    });

    this.audio.addEventListener("ended", () => {
      this.playbackState = PLAYBACK_STATES.IDLE;
      this.onEnded();
    });

    this.audio.addEventListener("timeupdate", () => {
      this.onTimeUpdate(this.audio.currentTime);
    });

    this.audio.addEventListener("error", (e) => {
      this.playbackState = PLAYBACK_STATES.ERROR;
      this.lastError = this.audio.error;
      this.onError(this.audio.error, e);
    });
  }

  loadSource(src) {
    this.audio.src = src;
    this.audio.load();
    this.playbackState = PLAYBACK_STATES.LOADING;
  }

  async play() {
    try {
      await this.audio.play();
      this.playbackState = PLAYBACK_STATES.PLAYING;
    } catch (err) {
      if (err.name === "NotAllowedError") {
        this.playbackState = PLAYBACK_STATES.PAUSED;
      } else {
        this.playbackState = PLAYBACK_STATES.ERROR;
      }
      throw err;
    }
  }

  pause() {
    this.audio.pause();
    this.playbackState = PLAYBACK_STATES.PAUSED;
  }

  seek(seconds) {
    if (typeof seconds === "number" && !isNaN(seconds)) {
      this.audio.currentTime = Math.max(0, seconds);
    }
  }

  get currentTime() {
    return this.audio.currentTime || 0;
  }

  get duration() {
    return this.audio.duration || 0;
  }

  get isPaused() {
    return this.audio.paused;
  }

  destroy() {
    this.audio.pause();
    this.audio.src = "";
    this.playbackState = PLAYBACK_STATES.IDLE;
  }
}

if (typeof globalThis !== "undefined") {
  globalThis.AudioController = AudioController;
}
