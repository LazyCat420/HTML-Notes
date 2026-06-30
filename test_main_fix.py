import httpx
import asyncio

async def main():
    PRISM_URL = "http://10.0.0.16:7777"
    model_name = ""
    provider_val = ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{PRISM_URL}/config?includeLocal=true")
            if resp.status_code == 200:
                data = resp.json()
                models_map = data.get("textToText", {}).get("models", {})
                for provider_id, models in models_map.items():
                    if models:
                        model_name = models[0].get("name")
                        provider_val = provider_id
                        break
    except Exception as e:
        print(f"Failed to fetch dynamic fallback model: {e}")
    print(f"Model: {model_name}, Provider: {provider_val}")

asyncio.run(main())
