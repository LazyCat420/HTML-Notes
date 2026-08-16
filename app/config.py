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
# portal-service is the live inventory of every registered service/container
# (fed from vault-service's projects.json). The App Hub widget and the
# html_notes_list_services/open_app tools read it — never the Docker socket.
PORTAL_SERVICE_URL = os.getenv("PORTAL_SERVICE_URL", "http://10.0.0.16:4001")
# vault-service is the single source of truth for secrets (GOOGLE_API_KEY, etc).
# Secrets are fetched at runtime with the bearer token — never hardcoded here.
VAULT_SERVICE_URL = os.getenv("VAULT_SERVICE_URL", "http://10.0.0.16:5599")
VAULT_SERVICE_TOKEN = os.getenv("VAULT_SERVICE_TOKEN", "")



# Ensure the database directory exists
db_dir = os.path.dirname(DATABASE_URL)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

# Obsidian vault: notes saved from the canvas are written here as .md files with
# YAML frontmatter (title/tags/created/updated), so they show up in Obsidian and
# you can piggyback on its markdown + metadata. Defaults to a subdir of the
# writable data volume (./data/vault on the NAS) so it works out of the box; to
# use your REAL vault, mount it in docker-compose and set OBSIDIAN_VAULT_DIR to
# the mount path (e.g. -v /volume1/Obsidian/MyVault:/app/vault, OBSIDIAN_VAULT_DIR=/app/vault).
OBSIDIAN_VAULT_DIR = os.getenv(
    "OBSIDIAN_VAULT_DIR", os.path.join(db_dir or "data", "vault"))
os.makedirs(OBSIDIAN_VAULT_DIR, exist_ok=True)
