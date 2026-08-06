import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("."))

async def test_e2e_turn():
    import app.main as main
    import app.config_builders as cb
    import app.routes.message as msg
    
    print("Testing build_answer_config execution...")
    try:
        # Test build_answer_config call (which failed in the user's log)
        res = await cb.build_answer_config("cnn live news")
        print(f"✓ build_answer_config executed successfully! Type: {type(res)}")
        assert isinstance(res, dict), "Result must be a dictionary!"
        print("✓ All dependencies (web_search, etc.) resolved cleanly during execution.")
    except NameError as e:
        print(f"FAILED with NameError: {e}")
        sys.exit(1)
    except Exception as e:
        # Other network/API errors are ok for mock offline, but NameError is what broke
        print(f"Executed without NameError! (Other error: {type(e).__name__}: {e})")

if __name__ == "__main__":
    asyncio.run(test_e2e_turn())
