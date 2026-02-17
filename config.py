import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

LOG_FILE = "log.log"
PID_FILE = "aviator.pid"

LOG_PAYOUTS_FOUND_PREFIX = "Found "
LOG_PAYOUTS_FOUND_SEP = " payouts | "
LOG_NO_PAYOUTS_MSG = "No payouts found on the page"

# Aviator scraper
AVIATOR_USERNAME = os.environ.get("AVIATOR_USERNAME", "")
AVIATOR_PASSWORD = os.environ.get("AVIATOR_PASSWORD", "")
GAME_URL = os.environ.get("AVIATOR_GAME_URL", "https://girobrasil1.com/casino/game/1892568")
LOGIN_URL = os.environ.get("AVIATOR_LOGIN_URL", "https://girobrasil1.com/casino?cmd=signin&path=loginMultichannel")

# MongoDB (log monitor)
MONGODB_URI = os.environ.get("MONGODB_URI", "")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "casino")
MONGODB_COLLECTION = os.environ.get("MONGODB_COLLECTION", "rounds")

# Signal Engine
SEQUENCE_LENGTH = 3
THRESHOLD = 2.0
TARGET_CASHOUT = 1.80
MAX_GALE = 2
COOLDOWN_ROUNDS = 3
# MongoDB collection names for signal engine (same database as rounds)
SIGNALS_COLLECTION = "signals"
DAILY_STATS_COLLECTION = "daily_stats"
ENGINE_STATE_COLLECTION = "engine_state"

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
AFFILIATE_LINK = os.environ.get("AFFILIATE_LINK", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID)

# Operating hours: if True, no signals created between 23:00-08:00 BRT (daily_close to daily_opener).
OPERATING_HOURS_ONLY = os.environ.get("OPERATING_HOURS_ONLY", "false").lower() in ("true", "1", "yes")
