import os

PORT = int(os.getenv("PORT", "8035"))
VLLM_URL = os.getenv("VLLM_URL", "http://10.0.0.141:8000")
VLLM_FAST_URL = os.getenv("VLLM_FAST_URL", "http://10.0.0.16:8001")
PRISM_URL = os.getenv("PRISM_URL", "http://10.0.0.16:7777")
# lazy-tool-service gateway (runs the agentic loop + widget tool execution).
# Host port 5591 maps to the container's 7778.
LAZY_AGENT_URL = os.getenv("LAZY_AGENT_URL", "http://10.0.0.16:5591")
DATABASE_URL = os.getenv("DATABASE_URL", "data/notes.db")
LAZY_TOOL_SERVICE_URL = os.getenv("LAZY_TOOL_SERVICE_URL", "http://10.0.0.16:5591")
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://10.0.0.16:3032")
MUSIC_PLAYER_URL = os.getenv("MUSIC_PLAYER_URL", "http://10.0.0.16:8002")
# scraper-service backs web_search/read_page. The tools-api search tools
# (search_web/search_news/read_web_page) are registered in the gateway catalog
# with a null endpoint and its python bridge has no interpreter in the image,
# so they return "Unknown tool" — this is the only working search path.
SCRAPER_SERVICE_URL = os.getenv("SCRAPER_SERVICE_URL", "http://10.0.0.16:8001")



# Ensure the database directory exists
db_dir = os.path.dirname(DATABASE_URL)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)
