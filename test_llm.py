import asyncio
import os
from app.agents.llm_client import call_llm

async def main():
    try:
        res = await call_llm([{"role": "user", "content": "Hello!"}], temperature=0.7, max_tokens=50)
        print("Success! Response:", res)
    except Exception as e:
        print("Error:", str(e))

if __name__ == "__main__":
    asyncio.run(main())
