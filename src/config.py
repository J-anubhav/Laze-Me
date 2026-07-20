"""Configuration: env vars, categories, timezone."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Gmail OAuth (built from a refresh token, no browser needed at runtime) ---
GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_TOKEN_URI = "https://oauth2.googleapis.com/token"
# modify = read + move to Trash (recoverable). NOT full delete.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# --- LLM (Gemini) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemma-4-31b-it")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Behavior ---
DIGEST_TIMEZONE = os.environ.get("DIGEST_TIMEZONE", "Asia/Kolkata")
# Max chars of each email body sent to the LLM (keeps token usage low).
BODY_TRUNCATE = int(os.environ.get("BODY_TRUNCATE", "500"))
# Send a message even when the inbox had no mail for the day.
SEND_ON_EMPTY = os.environ.get("SEND_ON_EMPTY", "true").lower() == "true"

# Ordered categories with a display emoji and a Telegram hashtag (no spaces).
# The hashtag makes each category tappable/filterable in Telegram.
CATEGORY_META = [
    {"name": "Job — Rejection", "emoji": "❌", "tag": "#JobRejection"},
    {"name": "Job — Interview/Progress", "emoji": "✅", "tag": "#JobInterview"},
    {"name": "Job — Application Received", "emoji": "📨", "tag": "#JobApplied"},
    {"name": "Job — Listings/Alerts", "emoji": "💼", "tag": "#JobAlerts"},
    {"name": "Finance/Bills", "emoji": "💰", "tag": "#Finance"},
    {"name": "Ads/Promotions", "emoji": "🏷️", "tag": "#Promotions"},
    {"name": "Personal", "emoji": "👤", "tag": "#Personal"},
    {"name": "Newsletters", "emoji": "📰", "tag": "#Newsletters"},
    {"name": "Other/Important", "emoji": "📌", "tag": "#Other"},
]

CATEGORIES = [c["name"] for c in CATEGORY_META]
EMOJI = {c["name"]: c["emoji"] for c in CATEGORY_META}
TAG = {c["name"]: c["tag"] for c in CATEGORY_META}


def require(*names: str) -> None:
    """Fail fast with a clear message if a required env var is missing."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nSet them in a local .env (see .env.example) or as GitHub secrets."
        )
