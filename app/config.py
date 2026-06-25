import os

PORT = int(os.getenv("PORT", "8035"))
VLLM_URL = os.getenv("VLLM_URL", "http://10.0.0.141:8000")
PRISM_URL = os.getenv("PRISM_URL", "http://10.0.0.16:7777")
DATABASE_URL = os.getenv("DATABASE_URL", "data/notes.db")
LAZY_TOOL_SERVICE_URL = os.getenv("LAZY_TOOL_SERVICE_URL", "http://10.0.0.16:5591")
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://10.0.0.16:3032")


# Ensure the database directory exists
db_dir = os.path.dirname(DATABASE_URL)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)
