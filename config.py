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
TARGET_CASHOUT = 1.50
MAX_GALE = 2
COOLDOWN_ROUNDS = 3
# Pre-signal: send Template 2 first, wait this long, then Template 3 when next round is processed (on time)
PRE_SIGNAL_WAIT_MIN_SEC = 8
PRE_SIGNAL_WAIT_MAX_SEC = 12
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

# Keep-Alive: post message if channel silent for this many minutes (when not in cooldown)
KEEP_ALIVE_SILENCE_MINUTES = 5

# Volatility Cooldown: pause signaling when 3 consecutive rounds < threshold (crashes)
VOLATILITY_THRESHOLD = 1.20
VOLATILITY_COOLDOWN_MIN_MIN = 5
VOLATILITY_COOLDOWN_MAX_MIN = 8
