import asyncio
from app.config_builders import build_composition_plan

async def test_nvda_composition():
    msg = "news articles for NVDA and the chart please"
    plan = await build_composition_plan(msg)
    print(f"Plan for {msg!r}: {plan}")
    assert len(plan) >= 2, f"Expected at least 2 modalities in plan, got: {plan}"
    types = [p['type'] for p in plan]
    print(f"Planned modality types: {types}")
    assert any(t in ('stock', 'chart') for t in types), f"Plan missing stock/chart: {types}"
    assert any(t in ('news', 'stock_news', 'answer') for t in types), f"Plan missing news/articles: {types}"
    print("✓ E2E Composition test for NVDA articles + chart passed!")

if __name__ == "__main__":
    asyncio.run(test_nvda_composition())
