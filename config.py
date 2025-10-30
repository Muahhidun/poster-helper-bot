"""Configuration module for Poster Helper Bot"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
STORAGE_DIR = PROJECT_ROOT / "storage"
LOGS_DIR = PROJECT_ROOT / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
STORAGE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip()]
ADMIN_USER_IDS = [int(uid.strip()) for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()]

# Poster API
POSTER_ACCOUNT = os.getenv("POSTER_ACCOUNT", "pizz-burg")
POSTER_TOKEN = os.getenv("POSTER_TOKEN")
POSTER_USER_ID = int(os.getenv("POSTER_USER_ID", "22"))
POSTER_BASE_URL = f"https://{POSTER_ACCOUNT}.joinposter.com/api"

# OpenAI (Whisper)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Anthropic (Claude)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Poster defaults
DEFAULT_WAREHOUSE_ID = int(os.getenv("DEFAULT_WAREHOUSE_ID", "1"))
DEFAULT_ACCOUNT_FROM_ID = int(os.getenv("DEFAULT_ACCOUNT_FROM_ID", "4"))
CURRENCY = os.getenv("CURRENCY", "KZT")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# File paths
CATEGORY_ALIASES_CSV = DATA_DIR / "alias_category_mapping.csv"
ACCOUNTS_CSV = DATA_DIR / "poster_accounts.csv"
DATABASE_PATH = STORAGE_DIR / "bot.db"

# Validation
def validate_config():
    """Validate required configuration"""
    errors = []

    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set")
    if not POSTER_TOKEN:
        errors.append("POSTER_TOKEN is not set")
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set")
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set")
    if not ALLOWED_USER_IDS:
        errors.append("ALLOWED_USER_IDS is not set")

    if errors:
        raise ValueError(f"Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))

    return True

if __name__ == "__main__":
    try:
        validate_config()
        print("✅ Configuration is valid")
        print(f"   Allowed users: {ALLOWED_USER_IDS}")
        print(f"   Poster account: {POSTER_ACCOUNT}")
        print(f"   Database: {DATABASE_PATH}")
    except ValueError as e:
        print(f"❌ {e}")
