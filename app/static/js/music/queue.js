/**
 * Queue manager: deduplication, index movement, track removal, and refill threshold checking.
 */
export class MusicQueue {
  constructor() {
    this.queue = [];
    this.currentIndex = -1;
    this.seenIds = new Set();
  }

  get currentTrack() {
    if (this.currentIndex >= 0 && this.currentIndex < this.queue.length) {
      return this.queue[this.currentIndex];
    }
    return null;
  }

  get upcoming() {
    return this.queue.slice(this.currentIndex + 1);
  }

  get length() {
    return this.queue.length;
  }

  enqueue(items) {
    if (!Array.isArray(items)) return 0;
    const fresh = [];
    for (const v of items) {
      if (v && v.id && !this.seenIds.has(v.id)) {
        this.seenIds.add(v.id);
        fresh.push(v);
      }
    }
    this.queue.push(...fresh);
    return fresh.length;
  }

  removeAt(index) {
    if (index === this.currentIndex || index < 0 || index >= this.queue.length) {
      return false; // Cannot remove playing track or out-of-bounds index
    }
    this.queue.splice(index, 1);
    if (index < this.currentIndex) {
      this.currentIndex--;
    }
    return true;
  }

  next() {
    if (this.currentIndex + 1 < this.queue.length) {
      this.currentIndex++;
      return this.currentTrack;
    }
    return null;
  }

  previous() {
    if (this.currentIndex > 0) {
      this.currentIndex--;
      return this.currentTrack;
    }
    return null;
  }

  shouldRefill({ threshold = 5, inFlight = false, msSinceLastRefill = 0, minIntervalMs = 90000 }) {
    const remaining = this.queue.length - (this.currentIndex + 1);
    if (remaining > threshold || inFlight || msSinceLastRefill < minIntervalMs) {
      return false;
    }
    return true;
  }

  clear() {
    this.queue = [];
    this.currentIndex = -1;
    this.seenIds.clear();
  }
}
