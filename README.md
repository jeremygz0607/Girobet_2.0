# Aviator Payout Scraper + Signal Engine

Scrapes payout/multiplier data from the Aviator casino game, saves rounds to MongoDB, triggers signals based on patterns, and sends Telegram notifications with automated daily/hourly messages.

## Components

- **aviator.py** – Headless browser logs in, opens the game, scrapes payout multipliers, saves directly to MongoDB, and triggers the signal engine. No log file used. Single script does everything.
- **log_monitor.py** – Optional: tails `log.log` if you run an older setup. For direct mode, aviator.py uses `process_payout_list()` in-process; log_monitor's file-tail mode is deprecated.
- **signal_engine.py** – Pattern detection (6 rounds < 2x), signal creation, gale escalation (up to 2 gales), win/loss tracking, and Telegram notifications.
- **telegram_service.py** – All Telegram message templates (Portuguese/Brazil) with emojis: signal alerts, win/loss/gale messages, daily/hourly recaps.
- **scheduler.py** – Scheduled messages in BRT timezone: daily opener (08:00), mid-day recap (14:00), hourly scoreboard, end of day recap (22:30), daily close (23:00), weekly recap (Sunday 21:00).

## Environment variables

Copy `.env.example` to `.env` (or set variables in your shell). **Do not commit real credentials.**

| Variable | Used by | Required | Description |
|----------|---------|----------|-------------|
| `AVIATOR_USERNAME` | aviator.py | Yes | Casino login username |
| `AVIATOR_PASSWORD` | aviator.py | Yes | Casino login password |
| `AVIATOR_GAME_URL` | aviator.py | No | Game URL (default in config) |
| `AVIATOR_LOGIN_URL` | aviator.py | No | Login URL (default in config) |
| `MONGODB_URI` | aviator.py | Yes (for DB) | MongoDB connection string |
| `MONGODB_DATABASE` | aviator.py | No | Database name (default: `casino`) |
| `MONGODB_COLLECTION` | aviator.py | No | Collection name (default: `rounds`) |
| `TELEGRAM_BOT_TOKEN` | telegram_service.py | Yes (for Telegram) | Bot token from @BotFather |
| `TELEGRAM_CHANNEL_ID` | telegram_service.py | Yes (for Telegram) | Channel ID (e.g., `@aviator_maquina` or numeric) |
| `AFFILIATE_LINK` | telegram_service.py | No | Affiliate link for "JOGAR AGORA" buttons |

## Usage

Run from the project root. Only one script is needed:

```bash
python aviator.py
```

This script scrapes payouts, saves them directly to MongoDB, runs the signal engine, and sends Telegram notifications. No `log.log` file is created. MongoDB, Telegram, and the scheduler are initialized automatically.

## Dependencies

```bash
pip install -r requirements.txt
```

- seleniumbase, beautifulsoup4, pymongo, requests, python-dotenv, pytz, APScheduler

## Configuration

Shared paths, log-message patterns, and signal engine parameters are in **config.py**:

- **Signal Engine:** `SEQUENCE_LENGTH=6`, `THRESHOLD=2.0`, `TARGET_CASHOUT=1.80`, `MAX_GALE=2`, `COOLDOWN_ROUNDS=3`
- **MongoDB collections:** `rounds`, `signals`, `daily_stats`, `engine_state`

## Signal Engine

**Trigger:** When the last 6 rounds all have multiplier < 2.0x (and no active signal, not in cooldown).

**Flow:**
- **Signal Active** → Next round ≥ target → **WIN** (send win message, increment daily wins).
- **Signal Active** → Next round < target → Escalate to **Gale 1** (send gale 1 message).
- **Gale 1 Active** → Next round ≥ target → **RECOVERED** (send recovery message, increment daily wins).
- **Gale 1 Active** → Next round < target → Escalate to **Gale 2** (send gale 2 message).
- **Gale 2 Active** → Next round ≥ target → **RECOVERED** (send recovery message, increment daily wins).
- **Gale 2 Active** → Next round < target → **LOST** (send loss message, increment daily losses, start cooldown for 3 rounds).

**Streak celebrations:** Automatically sent at 5, 10, 15, 20+ consecutive wins.

## Telegram Messages

All messages in **Portuguese (Brazil)** with emojis:

- **Daily Opener** (08:00 BRT): Yesterday's stats, instructions.
- **Signal Confirmed**: When pattern triggers, instructions to bet with Auto Cashout.
- **Win / Gale 1 / Gale 2 / Recovery / Loss**: Real-time signal resolution messages.
- **Hourly Scoreboard** (10:00, 12:00, 16:00, 18:00, 20:00 BRT): Last 2 hours results.
- **Mid-Day Recap** (14:00 BRT): Today's stats so far.
- **End of Day Recap** (22:30 BRT): Full day results with performance message.
- **Daily Close** (23:00 BRT): Final stats, see you tomorrow.
- **Weekly Recap** (Sunday 21:00 BRT): Mon-Sun summary.

If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHANNEL_ID` are not set, messages are logged only (no errors).

## Database Schema (MongoDB)

### `rounds` collection
- `_id` (int): Sequential round ID
- `multiplier` (float): Round result (e.g., 1.50, 2.30)
- `timestamp` (datetime): When the round occurred
- `created_at` (datetime): When saved to DB

### `signals` collection
- `_id` (int): Sequential signal ID
- `trigger_round_id` (int): Round that triggered the signal
- `target` (float): Cashout target (e.g., 1.80)
- `status` (string): `active`, `won`, `gale1`, `gale2`, `lost`
- `result_round_id` (int): Round where signal was resolved
- `result_multiplier` (float): Final round result
- `gale_depth` (int): 0, 1, or 2
- `created_at` (datetime): Signal creation time
- `resolved_at` (datetime): Signal resolution time

### `daily_stats` collection
- `_id` (string): Date in YYYY-MM-DD format
- `date` (string): Date in YYYY-MM-DD
- `wins` (int): Total wins for the day
- `losses` (int): Total losses for the day
- `signals_sent` (int): Total signals created for the day
- `updated_at` (datetime): Last update time

### `engine_state` collection
- `_id` (string): "state"
- `cooldown_until_round_id` (int): Round ID after which cooldown ends (null when not in cooldown)
