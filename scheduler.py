"""
Scheduler: scheduled Telegram messages (daily opener, hourly scoreboard, recaps, etc.)
All times in BRT (America/Sao_Paulo timezone).
"""
import logging
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

import config
import signal_engine
import telegram_service

logger = logging.getLogger(__name__)

BRT = pytz.timezone("America/Sao_Paulo")
_scheduler = None
_db = None


def _today_brt():
    """Today's date in BRT for consistent daily_stats across server timezones."""
    return datetime.now(BRT).date()


def init(db):
    """
    Initialize scheduler with database reference for stats queries.
    Starts background scheduler with cron jobs for daily/hourly messages.
    """
    global _scheduler, _db
    _db = db

    _scheduler = BackgroundScheduler(timezone=BRT)

    # Daily Opener: 08:00 BRT
    _scheduler.add_job(
        _job_daily_opener,
        CronTrigger(hour=8, minute=0, timezone=BRT),
        id="daily_opener",
        name="Daily Opener (08:00 BRT)",
    )

    # Mid-Day Recap: 14:00 BRT
    _scheduler.add_job(
        _job_midday_recap,
        CronTrigger(hour=14, minute=0, timezone=BRT),
        id="midday_recap",
        name="Mid-Day Recap (14:00 BRT)",
    )

    # Session Summary: every 45-60 min (replaces Hourly Scoreboard)
    _session_summary_minutes = random.randint(45, 60)
    _scheduler.add_job(
        _job_session_summary,
        IntervalTrigger(minutes=_session_summary_minutes, timezone=BRT),
        id="session_summary",
        name=f"Session Summary (every {_session_summary_minutes} min)",
    )

    # End of Day Recap: 22:30 BRT
    _scheduler.add_job(
        _job_end_of_day_recap,
        CronTrigger(hour=22, minute=30, timezone=BRT),
        id="end_of_day_recap",
        name="End of Day Recap (22:30 BRT)",
    )

    # Daily Close: 23:00 BRT
    _scheduler.add_job(
        _job_daily_close,
        CronTrigger(hour=23, minute=0, timezone=BRT),
        id="daily_close",
        name="Daily Close (23:00 BRT)",
    )

    # Weekly Recap: Sunday 21:00 BRT
    _scheduler.add_job(
        _job_weekly_recap,
        CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=BRT),
        id="weekly_recap",
        name="Weekly Recap (Sunday 21:00 BRT)",
    )

    # Template 2: Pattern Monitoring - every 12 min when 3+ rounds < 2x (no active signal)
    _scheduler.add_job(
        _job_pattern_monitoring,
        CronTrigger(minute="*/12", timezone=BRT),
        id="pattern_monitoring",
        name="Pattern Monitoring (every 12 min)",
    )

    # Keep-Alive: check every 60s, post if channel silent 5+ min and not in cooldown
    _scheduler.add_job(
        _job_keep_alive,
        IntervalTrigger(minutes=1, timezone=BRT),
        id="keep_alive",
        name="Keep-Alive check (every 1 min)",
    )

    _scheduler.start()
    logger.info("Scheduler started with BRT timezone")


def post_shutdown_summary():
    """
    Post a session summary when scraper shuts down or goes offline.
    Call before scheduler.shutdown() so DB is still available.
    """
    try:
        _job_session_summary()
    except Exception as e:
        logger.debug(f"post_shutdown_summary error: {e}")


def shutdown():
    """Shutdown the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        logger.info("Scheduler shut down")


# ============================================================
# Helper: Get daily_stats for a given date
# ============================================================
def _get_daily_stats(date_str):
    """Get daily_stats doc for date (YYYY-MM-DD). Returns dict or None."""
    if _db is None:
        return None
    try:
        coll = _db[config.DAILY_STATS_COLLECTION]
        return coll.find_one({"_id": date_str})
    except Exception as e:
        logger.debug(f"_get_daily_stats error: {e}")
        return None


def _get_today_wins_losses(stats):
    """Get total wins/losses for the day. today_wins = signals_sent - today_losses; uses today_losses from doc."""
    if not stats:
        return 0, 0
    losses = stats.get("today_losses", stats.get("losses", 0))
    wins = stats.get("today_wins")
    if wins is None:
        # Backfill: today_wins = signals_sent - today_losses (for docs created before this field)
        wins = max(0, stats.get("signals_sent", 0) - losses)
    return wins, losses


def _get_signals_for_date(date_str):
    """Get all resolved signals for a given date (YYYY-MM-DD). Returns list."""
    if _db is None:
        return []
    try:
        coll = _db[config.SIGNALS_COLLECTION]
        # Resolved signals: status in (won, lost)
        # Filter by created_at date (BRT)
        start_dt = datetime.fromisoformat(date_str).replace(tzinfo=pytz.utc)
        end_dt = start_dt + timedelta(days=1)
        cursor = coll.find(
            {
                "status": {"$in": ["won", "lost"]},
                "created_at": {"$gte": start_dt, "$lt": end_dt},
            }
        ).sort("created_at", 1)
        return list(cursor)
    except Exception as e:
        logger.debug(f"_get_signals_for_date error: {e}")
        return []


def _build_result_emojis(signals):
    """Build emoji string from list of signals: ✅ for won, 🛑 for lost."""
    emojis = []
    for sig in signals:
        if sig.get("status") == "won":
            emojis.append("✅")
        elif sig.get("status") == "lost":
            emojis.append("🛑")
    return "".join(emojis)


def _calculate_best_streak(signals):
    """Calculate the longest consecutive win streak from a list of signals."""
    best = 0
    current = 0
    for sig in signals:
        if sig.get("status") == "won":
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


# ============================================================
# Job: Daily Opener (08:00 BRT)
# ============================================================
def _job_daily_opener():
    """Send daily opener with yesterday's stats. Clears any session_closed flag."""
    signal_engine.clear_session_closed()
    yesterday = (_today_brt() - timedelta(days=1)).isoformat()
    stats = _get_daily_stats(yesterday)
    wins, losses = _get_today_wins_losses(stats)
    telegram_service.send_daily_opener(wins, losses)


# ============================================================
# Job: Template 2 - Pattern Monitoring (every 12 min)
# ============================================================
def _job_pattern_monitoring():
    """Send pattern monitoring when 3+ rounds < 2x, no active signal, not in cooldown."""
    data = signal_engine.get_pattern_monitoring_data()
    if data:
        count, remaining = data
        telegram_service.send_pattern_monitoring(count, remaining)


# ============================================================
# Job: Keep-Alive (every 1 min)
# ============================================================
def _job_keep_alive():
    """If channel silent 5+ min and not in cooldown, post random keep-alive message."""
    signal_engine.check_and_send_keep_alive()


# ============================================================
# Job: Mid-Day Recap (14:00 BRT)
# ============================================================
def _job_midday_recap():
    """Send mid-day recap with today's stats so far."""
    today_str = _today_brt().isoformat()
    stats = _get_daily_stats(today_str)
    wins, losses = _get_today_wins_losses(stats)

    signals = _get_signals_for_date(today_str)
    result_emojis = _build_result_emojis(signals)
    best_streak = _calculate_best_streak(signals)

    telegram_service.send_midday_recap(result_emojis, wins, losses, best_streak)


# ============================================================
# Job: Session Summary (every 45-60 min)
# ============================================================
def _job_session_summary():
    """Send session summary: signals since last summary (or since 08:00). Every 45-60 min."""
    if _db is None:
        return
    now = datetime.now(BRT)
    engine_coll = _db[config.ENGINE_STATE_COLLECTION]
    try:
        state = engine_coll.find_one({"_id": "state"}) or {}
    except Exception as e:
        logger.debug(f"_job_session_summary state error: {e}")
        state = {}
    last_at = state.get("last_session_summary_at")
    today_8am = BRT.localize(datetime(_today_brt().year, _today_brt().month, _today_brt().day, 8, 0))
    if last_at is None:
        if now < today_8am:
            return  # Before 08:00, skip
        session_start = today_8am
    else:
        if hasattr(last_at, "tzinfo") and last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=pytz.utc)
        last_brt = last_at.astimezone(BRT)
        # New day: use 08:00 BRT today as session start
        if last_brt.date() < _today_brt():
            session_start = today_8am
        else:
            session_start = last_brt
    session_start_utc = session_start.astimezone(pytz.utc)
    try:
        coll = _db[config.SIGNALS_COLLECTION]
        cursor = coll.find(
            {
                "status": {"$in": ["won", "lost"]},
                "created_at": {"$gte": session_start_utc},
            }
        ).sort("created_at", 1)
        signals = list(cursor)
    except Exception as e:
        logger.debug(f"_job_session_summary signals error: {e}")
        signals = []
    period_wins = sum(1 for s in signals if s.get("status") == "won")
    period_losses = sum(1 for s in signals if s.get("status") == "lost")
    total_signals = len(signals)
    if total_signals == 0:
        logger.info("No signals in session period, skipping session summary")
        return
    delta = now - session_start
    total_min = int(delta.total_seconds() / 60)
    if total_min < 60:
        session_duration = f"{total_min} min"
    else:
        h, m = divmod(total_min, 60)
        session_duration = f"{h}h {m}min"
    win_rate = (period_wins / total_signals * 100) if total_signals > 0 else 0
    telegram_service.send_session_summary(session_duration, total_signals, period_wins, period_losses, win_rate)
    try:
        engine_coll.update_one(
            {"_id": "state"},
            {"$set": {"last_session_summary_at": datetime.now(pytz.utc)}},
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"_job_session_summary update error: {e}")


# ============================================================
# Job: End of Day Recap (22:30 BRT)
# ============================================================
def _job_end_of_day_recap():
    """Send end of day recap with full day stats."""
    today_str = _today_brt().isoformat()
    stats = _get_daily_stats(today_str)
    wins, losses = _get_today_wins_losses(stats)
    total_signals = stats.get("signals_sent", 0) if stats else 0

    signals = _get_signals_for_date(today_str)
    result_emojis = _build_result_emojis(signals)
    best_streak = _calculate_best_streak(signals)

    telegram_service.send_end_of_day_recap(result_emojis, wins, losses, best_streak, total_signals)


# ============================================================
# Job: Daily Close (23:00 BRT)
# ============================================================
def _job_daily_close():
    """Send daily close message with today's stats."""
    today_str = _today_brt().isoformat()
    stats = _get_daily_stats(today_str)
    wins, losses = _get_today_wins_losses(stats)
    telegram_service.send_daily_close(wins, losses)


# ============================================================
# Job: Weekly Recap (Sunday 21:00 BRT)
# ============================================================
def _job_weekly_recap():
    """Send weekly recap: stats for last 7 days (Mon-Sun)."""
    today = _today_brt()
    # Get the most recent Sunday (today if today is Sunday, else previous Sunday)
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    # Week is Mon-Sun, so start is last_sunday - 6 days
    week_start = last_sunday - timedelta(days=6)

    day_names = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    daily_data = []
    week_wins = 0
    week_losses = 0
    week_total_signals = 0
    best_day_name = ""
    best_day_rate = 0

    for i in range(7):
        current_date = week_start + timedelta(days=i)
        date_str = current_date.isoformat()
        stats = _get_daily_stats(date_str)
        wins, losses = _get_today_wins_losses(stats)
        total = wins + losses
        rate = (wins / total * 100) if total > 0 else 0

        daily_data.append({"day": day_names[i], "wins": wins, "losses": losses, "rate": rate})
        week_wins += wins
        week_losses += losses
        if stats:
            week_total_signals += stats.get("signals_sent", 0)

        if rate > best_day_rate:
            best_day_rate = rate
            best_day_name = day_names[i]

    telegram_service.send_weekly_recap(daily_data, week_wins, week_losses, week_total_signals, best_day_name, best_day_rate)
