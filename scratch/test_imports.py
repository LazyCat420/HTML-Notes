import sys
import os

sys.path.insert(0, os.path.abspath("."))

def test_imports():
    import app.main as main
    import app.config_builders as cb
    import app.canvas_manager as cm
    import app.routes.message as msg
    
    symbols_to_check = [
        (cb, "web_search"),
        (cb, "get_weather"),
        (cb, "stock_snapshot"),
        (cb, "search_youtube_videos"),
        (cb, "fast_llm_json"),
        (cm, "_run_turn"),
        (cm, "_iter_canvas_widgets"),
        (cm, "_score_widget_for_query"),
        (msg, "_run_turn"),
        (msg, "build_answer_config"),
        (msg, "build_news_config"),
        (msg, "build_map_config"),
        (msg, "web_search"),
    ]
    
    missing = []
    for mod, sym in symbols_to_check:
        if not hasattr(mod, sym):
            missing.append(f"{mod.__name__}.{sym}")
            
    if missing:
        print(f"FAILED: Missing cross-module symbols: {missing}")
        sys.exit(1)
        
    print("SUCCESS: All cross-module symbols resolved across all layers!")

if __name__ == "__main__":
    test_imports()
