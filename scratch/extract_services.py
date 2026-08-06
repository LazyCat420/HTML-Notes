import ast
import os

def get_ast_node_text(lines, node):
    start = node.lineno - 1
    if hasattr(node, "decorator_list") and node.decorator_list:
        start = node.decorator_list[0].lineno - 1
    end = node.end_lineno
    return start, end

def main():
    with open("app/main.py", "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.splitlines(True)
    
    tree = ast.parse(content)
    
    categorized_fns = {
        "services/search.py": {
            "_scrape", "_backfill_snippets", "_search_brave_api", "_search_duckduckgo",
            "_search_scraper_ddg", "web_search_ex", "web_search", "_norm_title_key",
            "_gdelt_news", "_google_news_rss", "_is_generic_news_thumb", "_enrich_news",
            "_shared_news_search", "news_search", "read_web_page", "_looks_like_junk_page",
            "_is_public_http_url", "_fetch_news_fetch_news_page_text", "api_search"
        },
        "services/finance.py": {
            "_sma", "_rsi", "_annualized_volatility", "_yahoo_fundamentals", "_yahoo_chart",
            "stock_snapshot", "_extract_compare_tickers", "_trend_kind", "_range_from_message",
            "_trending_symbols", "_universe_from_message", "_index_constituents", "stock_news",
            "_finnews_articles", "_merge_news", "fetch_fx_rates", "_stock_video_commentary",
            "_fundamentals_lines", "_dexs_snapshot", "_crypto_snapshot", "_gt_chart_for_contract",
            "_resolve_ticker", "_usd"
        },
        "services/location.py": {
            "geocode_location", "get_weather", "_wmo", "extract_location",
            "extract_trip_destination", "geocode_place", "geocode_nominatim",
            "poi_query_has_location", "anchor_places_query", "is_deictic_place",
            "_extract_directions_place", "google_places_search", "_emoji_for_place_type"
        },
        "services/sports.py": {
            "resolve_league", "_competitor", "sports_scores"
        },
        "services/youtube_helpers.py": {
            "extract_youtube_query", "clean_video_query", "pick_best_video",
            "pick_varied_video", "_load_blocklists", "block_video", "block_channel",
            "filter_blocked_videos", "_shown_video_ids", "_remember_current_video",
            "_split_video_subject_topic", "_creator_evidence", "_topic_in_title",
            "_recency_video_pick"
        },
        "llm.py": {
            "fast_llm_json", "ground_query", "_fast_multimodal_json", "route_with_llm"
        },
        "utils.py": {
            "cache_tool_result", "get_cached_tool_result", "cached_stock_symbols",
            "_graceful_fallback_config", "_data_card_quality_gap", "_linky_items",
            "_bare_items", "_synthesize_answer_from_items", "_favicon_for", "_items_of",
            "_items_missing_images", "_backfill_item_images", "_ensure_data_card_quality",
            "_tool_repeat_key", "_summarize_tool_args", "_phase_for_tool",
            "_strip_agent_narration", "_text_answer_card_config", "pick_theme",
            "_clean_fact", "capture_user_facts", "_user_facts_prompt",
            "_strip_citation_markers", "_noop_dict", "_fmt_usd", "_image_url_loads",
            "_fetch_secret", "_note_slug", "_note_path", "_yaml_frontmatter",
            "_parse_frontmatter", "tts_synthesize", "_round"
        }
    }

    fn_to_file = {}
    for filename, fn_list in categorized_fns.items():
        for fn in fn_list:
            fn_to_file[fn] = filename

    extracted_blocks = {filename: [] for filename in categorized_fns.keys()}
    all_extracted = []

    # ONLY top-level nodes in tree.body
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            name = node.name
            if name in fn_to_file:
                start, end = get_ast_node_text(lines, node)
                target_file = fn_to_file[name]
                extracted_blocks[target_file].append({
                    "start": start,
                    "end": end,
                    "name": name
                })
                all_extracted.append({"start": start, "end": end})

    all_extracted.sort(key=lambda x: x["start"], reverse=True)
    
    os.makedirs("app/services", exist_ok=True)
    
    def write_module(filename, fns):
        if not fns: return
        dirname = os.path.dirname(f"app/{filename}")
        if dirname: os.makedirs(dirname, exist_ok=True)
        
        with open(f"app/{filename}", "w", encoding="utf-8") as f:
            f.write("import sys\n")
            f.write("import app.main as main\n")
            f.write("sys.modules[__name__].__dict__.update(main.__dict__)\n\n")
            
            fns_sorted = sorted(fns, key=lambda x: x["start"])
            for r in fns_sorted:
                chunk = lines[r["start"]:r["end"]]
                f.writelines(chunk)
                f.write("\n\n")
            
            # CRITICAL FIX: Ensure `from module import *` imports underscore-prefixed functions!
            f.write("__all__ = [k for k in globals().keys() if not k.startswith('__')]\n")

    for filename, blocks in extracted_blocks.items():
        write_module(filename, blocks)
        print(f"Extracted {len(blocks)} functions to {filename}")
    
    for r in all_extracted:
        del lines[r["start"]:r["end"]]

    idx = -1
    for i, line in enumerate(lines):
        if line.startswith("from app.canvas_manager import *"):
            idx = i
            break
            
    import_statements = (
        "from app.services.search import *\n"
        "from app.services.finance import *\n"
        "from app.services.location import *\n"
        "from app.services.sports import *\n"
        "from app.services.youtube_helpers import *\n"
        "from app.llm import *\n"
        "from app.utils import *\n"
    )

    if idx != -1:
        lines.insert(idx + 1, import_statements)
    else:
        idx2 = -1
        for i, line in enumerate(lines):
            if line.startswith("__all__ ="):
                idx2 = i
                break
        if idx2 != -1:
            lines.insert(idx2, "\n" + import_statements + "\n")
        else:
            lines.append("\n" + import_statements + "\n")

    with open("app/main.py", "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    print(f"Total functions extracted: {len(all_extracted)}")

if __name__ == "__main__":
    main()
