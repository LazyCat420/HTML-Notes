/**
 * Playability manager: pre-play probing, probe cache, and dead track pruning.
 */
export class PlayabilityChecker {
  constructor({ baseUrl = "", ttlMs = 1800000, maxLookahead = 3 } = {}) {
    this.baseUrl = baseUrl;
    this.ttlMs = ttlMs;
    this.maxLookahead = maxLookahead;
    this.cache = new Map(); // videoId -> { playable: boolean, timestamp: number }
  }

  isYouTubeId(id) {
    return typeof id === "string" && (id.startsWith("yt_") || /^[A-Za-z0-9_-]{11}$/.test(id));
  }

  cleanId(id) {
    if (typeof id !== "string") return "";
    return id.replace(/^yt_/, "");
  }

  getCached(id) {
    const clean = this.cleanId(id);
    const entry = this.cache.get(clean);
    if (entry && Date.now() - entry.timestamp < this.ttlMs) {
      return entry.playable;
    }
    return null;
  }

  setCached(id, playable) {
    const clean = this.cleanId(id);
    this.cache.set(clean, { playable, timestamp: Date.now() });
  }

  async checkPlayable(id, fetchFn = globalThis.fetch) {
    if (!this.isYouTubeId(id)) {
      return true; // Local tracks bypass YouTube probing
    }

    const clean = this.cleanId(id);
    const cached = this.getCached(clean);
    if (cached !== null) {
      return cached;
    }

    try {
      const url = `${this.baseUrl}/api/youtube/playable/${encodeURIComponent(clean)}`;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      const res = await fetchFn(url, { signal: controller.signal });
      clearTimeout(timer);

      if (!res.ok) {
        // Fail open on HTTP error to prevent silent drop
        return true;
      }
      const data = await res.json();
      const playable = Boolean(data && data.playable !== false);
      this.setCached(clean, playable);
      return playable;
    } catch {
      // Fail open on network/timeout errors
      return true;
    }
  }

  async settleOnPlayable(queue, currentIndex = 0, fetchFn = globalThis.fetch) {
    let index = currentIndex;
    let probedCount = 0;

    while (index >= 0 && index < queue.length && probedCount < this.maxLookahead) {
      const track = queue[index];
      if (!track || !track.id) break;

      const ok = await this.checkPlayable(track.id, fetchFn);
      if (ok) {
        return index;
      }
      probedCount++;
      index++;
    }

    return index < queue.length ? index : -1;
  }
}

if (typeof globalThis !== "undefined") {
  globalThis.PlayabilityChecker = PlayabilityChecker;
}
