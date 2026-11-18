from dotenv import load_dotenv
load_dotenv()
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = os.getenv("DATA_DIR", str(BASE_DIR / "data"))
# DEMO_MODE=1: at startup, if there is no availability for today, load the saved sample files and label them as today. Testing only.
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).resolve().parent.parent / "reservecmu.db"))  # always backend/reservecmu.db
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")  # any tool-calling model
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "1000"))  # cap per model call; without it OpenRouter reserves the model max and can fail with 402
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Deep link into 25Live. VERIFY this pattern against a real space page before launch.
LIVE25_SPACE_URL = os.getenv("LIVE25_SPACE_URL", "https://25live.collegenet.com/pro/cmu#!/home/space/{id}/detail")
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o]
MIN_REPORTS = int(os.getenv("MIN_REPORTS", "3"))  # feedback reports needed before an attribute counts as "verified"

# --- 25Live sync / background refresh ---
REFRESH_ENABLED = os.getenv("REFRESH_ENABLED", "0") == "1"
AVAILABILITY_COOKIE = os.getenv("AVAILABILITY_COOKIE", "")      # only if the endpoints turn out to need a session
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "600"))
REFRESH_DAYS = int(os.getenv("REFRESH_DAYS", "3"))               # today + next N-1 days
SCRAPER_UA = os.getenv("SCRAPER_UA", "ReserveCMU/0.1 (contact: you@andrew.cmu.edu)")
