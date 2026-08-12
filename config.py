import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
HELIUS_API_KEY     = os.environ.get("HELIUS_API_KEY", "")
MORALIS_API_KEY    = os.environ.get("MORALIS_API_KEY", "")
try:
    ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))
except (ValueError, TypeError):
    ADMIN_TELEGRAM_ID = 0
SENTRY_DSN         = os.environ.get("SENTRY_DSN", "")
