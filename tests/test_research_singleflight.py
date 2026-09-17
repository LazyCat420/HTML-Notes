"""Unit tests for research cache and single-flight coalescing."""
import asyncio
import pytest
from app.services.research.cache import ResearchCache, SingleFlight


@pytest.mark.asyncio
async def test_single_flight_coalescing():
    sf = SingleFlight()
    executions = 0

    async def worker(task_id: int):
        nonlocal executions
        async with sf.flight("shared-query-key") as call:
            if call.is_leader:
                await asyncio.sleep(0.05)
                executions += 1
                call.value = {"result": "data", "executions": executions}
            return call.value

    # Launch 5 concurrent calls
    results = await asyncio.gather(*[worker(i) for i in range(5)])

    assert executions == 1, f"Expected 1 execution, got {executions}"
    for r in results:
        assert r == {"result": "data", "executions": 1}


@pytest.mark.asyncio
async def test_cache_ttl_and_freshness():
    cache = ResearchCache()
    key = cache.make_key("quote", entity="NVDA", query="price")

    await cache.set(key, {"price": 125.50}, data_class="quote", custom_ttl=0.1)

    val, fresh, age = await cache.get(key)
    assert val == {"price": 125.50}
    assert fresh in ("fresh", "cached")
    assert age >= 0.0

    # Wait for expiration
    await asyncio.sleep(0.15)
    val_stale, fresh_stale, age_stale = await cache.get(key, allow_stale=True)
    assert val_stale == {"price": 125.50}
    assert fresh_stale == "stale"

    # Disallow stale
    val_none, fresh_none, _ = await cache.get(key, allow_stale=False)
    assert val_none is None
