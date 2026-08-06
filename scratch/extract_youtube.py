import sys

def main():
    try:
        with open("app/main.py", "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Find the start of _diversify_by_channel (line 43)
        start_idx = -1
        for i, line in enumerate(lines):
            if line.startswith("def _diversify_by_channel"):
                start_idx = i
                break
                
        # Find the end (before _SEARCH_NOISE_HOSTS, around line 780)
        end_idx = -1
        for i in range(start_idx, len(lines)):
            if line.startswith("_SEARCH_NOISE_HOSTS"):
                end_idx = i
                break
                
        if start_idx == -1 or end_idx == -1:
            # Let's try to find _SEARCH_NOISE_HOSTS
            for i, line in enumerate(lines):
                if line.startswith("_SEARCH_NOISE_HOSTS"):
                    end_idx = i
                    break
        
        # We know end_idx is where _SEARCH_NOISE_HOSTS starts
        youtube_lines = lines[start_idx:end_idx]
        
        # We need to construct youtube_service.py
        yt_service_content = f"""import asyncio
import datetime
import httpx
import logging
import re
from typing import Optional, Dict, List, Any

# Inherit from youtube_search
from app.youtube_search import (
    fetch_videos as _yt_fetch_videos,
    score_videos as _yt_score_videos,
    detect_language as _yt_detect_language,
    clean_query as _yt_clean_query,
    Intent as _YtIntent,
    _unescape as _yt_unescape,
    _token_overlap as _yt_token_overlap,
    Freshness,
    parse_freshness,
    filter_by_age,
    parse_video_form,
    filter_by_form,
    NEWEST_PATTERN as _YT_NEWEST_PATTERN,
    RECENCY_PATTERN as _YT_RECENCY_PATTERN,
)

# We will need fast_llm_json, _scrape, and SCRAPER_SERVICE_URL
# To avoid circular imports, we will import them locally inside the functions
# or at the end of the module.
logger = logging.getLogger(__name__)

{"".join(youtube_lines)}
"""
        # To avoid circular imports, we replace fast_llm_json with a local import
        yt_service_content = yt_service_content.replace(
            "data = await fast_llm_json(", 
            "from app.main import fast_llm_json\n    data = await fast_llm_json("
        )
        
        yt_service_content = yt_service_content.replace(
            "await _scrape(",
            "from app.main import _scrape\n            return await _scrape("
        )
        # Fix the specific usage of _scrape in youtube_service
        # Actually _scrape is used like: html = await _scrape(f"https://www.youtube.com/@{ch_id}/videos")
        yt_service_content = yt_service_content.replace(
            "html = await _scrape(",
            "from app.main import _scrape\n    html = await _scrape("
        )
        
        # Fix SCRAPER_SERVICE_URL
        yt_service_content = yt_service_content.replace(
            "SCRAPER_SERVICE_URL",
            "__import__('app.main', fromlist=['SCRAPER_SERVICE_URL']).SCRAPER_SERVICE_URL"
        )
        
        # Write youtube_service.py
        with open("app/youtube_service.py", "w", encoding="utf-8") as f:
            f.write(yt_service_content)
            
        # Update main.py
        new_main_lines = lines[:start_idx] + [
            "from app.youtube_service import *\n\n"
        ] + lines[end_idx:]
        
        with open("app/main.py", "w", encoding="utf-8") as f:
            f.writelines(new_main_lines)
            
        print(f"Extracted {end_idx - start_idx} lines to app/youtube_service.py")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
