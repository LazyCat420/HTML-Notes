import sys
import os

# Set up path
sys.path.insert(0, os.path.abspath("."))

def test_imports():
    import app.main as main
    
    required_symbols = [
        "_run_turn",
        "_score_widget_for_query",
        "_iter_canvas_widgets",
        "build_stock_compare_config",
        "search_youtube_videos",
        "_scrape",
        "stock_snapshot",
        "geocode_location",
        "_load_blocklists"
    ]
    
    missing = []
    for sym in required_symbols:
        if not hasattr(main, sym):
            missing.append(sym)
            
    if missing:
        print(f"FAILED: Missing symbols in main module: {missing}")
        sys.exit(1)
    else:
        print("SUCCESS: All required symbols found in app.main!")

if __name__ == "__main__":
    test_imports()
