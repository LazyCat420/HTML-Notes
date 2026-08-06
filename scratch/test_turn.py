import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("."))

async def main_test():
    import app.main as main
    
    print("Testing _run_turn existence and signature...")
    assert hasattr(main, "_run_turn"), "_run_turn missing from main!"
    print("✓ _run_turn is present in app.main")
    
    # Check message router module's globals
    import app.routes.message as msg_route
    assert hasattr(msg_route, "_run_turn"), "_run_turn missing from app.routes.message!"
    print("✓ _run_turn is present in app.routes.message")

if __name__ == "__main__":
    asyncio.run(main_test())
