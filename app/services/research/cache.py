"""Information Half-Life Caching & Single-Flight Request Coalescing.

Prevents duplicate upstream provider requests across concurrent turns/subscribers,
and enforces explicit TTLs based on data half-life while propagating
fresh/cached/stale provenance.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Optional, Any, Dict, Tuple, AsyncGenerator
from contextlib import asynccontextmanager

from .models import FreshnessState


# TTL definitions in seconds based on information half-life
DEFAULT_TTLS: Dict[str, float] = {
    "quote": 15.0,                  # market price & quote during trading
    "quote_closed": 120.0,          # market quote outside trading
    "intraday": 45.0,               # intraday chart aggregates
    "headlines": 180.0,             # broad market headlines
    "ticker_news": 300.0,           # ticker-specific news
    "fundamentals": 86400.0,        # 1 day
    "index_constituents": 86400.0,  # 1 day
    "metadata": 604800.0,           # 7 days (ticker identity/company profile)
    "synthesis": 300.0,             # cached answer summary
    "article_text": 604800.0,       # 7 days
    "filing": 31536000.0,           # 1 year (immutable content)
    "negative": 30.0,               # negative / error result to stop retry storms
}


class CacheEntry:
    __slots__ = ("value", "created_at", "ttl", "data_class")

    def __init__(self, value: Any, ttl: float, data_class: str = "general"):
        self.value = value
        self.created_at = time.time()
        self.ttl = ttl
        self.data_class = data_class

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def is_expired(self) -> bool:
        return self.age_seconds > self.ttl

    @property
    def freshness(self) -> FreshnessState:
        age = self.age_seconds
        if age <= self.ttl:
            return "fresh" if age <= (self.ttl * 0.25) else "cached"
        # Within 3x TTL considered stale but usable fallback
        return "stale"


class ResearchCache:
    """In-memory hot cache with TTL and stale fallback support."""

    def __init__(self):
        self._store: Dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    def make_key(self, data_class: str, entity: str = "", query: str = "",
                 geography: str = "us", time_window: str = "today") -> str:
        norm_entity = (entity or "").strip().upper()
        norm_query = (query or "").strip().lower()
        norm_geo = (geography or "us").strip().lower()
        norm_window = (time_window or "today").strip().lower()
        raw = f"{data_class}:{norm_entity}:{norm_query}:{norm_geo}:{norm_window}"
        return f"rc:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"

    async def get(self, key: str, allow_stale: bool = True) -> Tuple[Optional[Any], FreshnessState, float]:
        async with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None, "unknown", 0.0
            freshness = entry.freshness
            if entry.is_expired and not allow_stale:
                return None, "stale", entry.age_seconds
            return entry.value, freshness, entry.age_seconds

    async def set(self, key: str, value: Any, data_class: str = "general",
                  custom_ttl: Optional[float] = None) -> None:
        ttl = custom_ttl if custom_ttl is not None else DEFAULT_TTLS.get(data_class, 60.0)
        async with self._lock:
            self._store[key] = CacheEntry(value=value, ttl=ttl, data_class=data_class)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear_expired(self) -> int:
        now = time.time()
        async with self._lock:
            expired = [k for k, v in self._store.items() if (now - v.created_at) > (v.ttl * 3)]
            for k in expired:
                del self._store[k]
            return len(expired)


class SingleFlightCall:
    """Represents an active in-flight execution leader."""
    def __init__(self):
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.is_leader: bool = False
        self.value: Any = None


class SingleFlight:
    """Coordinates concurrent requests so only one execution runs per active key."""

    def __init__(self):
        self._flights: Dict[str, SingleFlightCall] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def flight(self, key: str) -> AsyncGenerator[SingleFlightCall, None]:
        call: SingleFlightCall
        async with self._lock:
            if key in self._flights:
                call = self._flights[key]
                call.is_leader = False
            else:
                call = SingleFlightCall()
                call.is_leader = True
                self._flights[key] = call

        if not call.is_leader:
            # Follower waits on leader's completion
            try:
                call.value = await call.future
            except Exception as e:
                # Re-raise leader exception for followers
                raise e
            yield call
            return

        # Leader executes and fulfills the future for all followers
        try:
            yield call
            if not call.future.done():
                call.future.set_result(call.value)
        except BaseException as e:
            if not call.future.done():
                call.future.set_exception(e)
            raise
        finally:
            async with self._lock:
                self._flights.pop(key, None)


# Global instances for the process
research_cache = ResearchCache()
single_flight = SingleFlight()
