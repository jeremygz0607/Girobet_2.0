"""
Signal Engine: trigger logic, resolution (gale), state machine, and persistence.
Uses MongoDB collections: rounds (read), signals, daily_stats, engine_state (cooldown).
"""
import logging
import random
from datetime import datetime, timezone, time, timedelta
import pytz

import config
import telegram_service

logger = logging.getLogger(__name__)

BRT = pytz.timezone("America/Sao_Paulo")

# Database and collections (set by init)
_db = None
_rounds_coll = None
_signals_coll = None
_daily_stats_coll = None
_engine_state_coll = None

# Signal status values (matches schema: active, won, gale1, gale2, lost)
STATUS_ACTIVE = "active"
STATUS_WON = "won"
STATUS_GALE1 = "gale1"
STATUS_GALE2 = "gale2"
STATUS_LOST = "lost"


def init(db):
    """Initialize signal engine with MongoDB database. Uses same DB as rounds."""
    global _db, _rounds_coll, _signals_coll, _daily_stats_coll, _engine_state_coll
    _db = db
    _rounds_coll = db[config.MONGODB_COLLECTION]
    _signals_coll = db[config.SIGNALS_COLLECTION]
    _daily_stats_coll = db[config.DAILY_STATS_COLLECTION]
    _engine_state_coll = db[config.ENGINE_STATE_COLLECTION]
    telegram_service.register_message_sent_callback(record_message_sent)
    logger.info("Signal engine initialized (signals, daily_stats, engine_state)")


def _get_rounds_collection():
    if _rounds_coll is None:
        return None
    return _rounds_coll


def get_recent_rounds(n):
    """
    Get the last n rounds (newest first). Each item: { _id, multiplier }.
    Only considers rounds with integer _id.
    """
    coll = _get_rounds_collection()
    if coll is None:
        return []
    try:
        cursor = coll.find(
            {"_id": {"$type": "int"}},
            {"_id": 1, "multiplier": 1}
        ).sort("_id", -1).limit(n)
        return list(cursor)
    except Exception as e:
        logger.debug(f"get_recent_rounds error: {e}")
        return []


def get_active_signal():
    """Return the single active signal document, or None.
    Active = status in (active, gale1, gale2) - signal is still in progress awaiting result.
    """
    if _signals_coll is None:
        return None
    try:
        return _signals_coll.find_one(
            {"status": {"$in": [STATUS_ACTIVE, STATUS_GALE1, STATUS_GALE2]}}
        )
    except Exception as e:
        logger.debug(f"get_active_signal error: {e}")
        return None


def active_signal_exists():
    return get_active_signal() is not None


def _get_engine_state():
    if _engine_state_coll is None:
        return {}
    try:
        doc = _engine_state_coll.find_one({"_id": "state"})
        return doc or {}
    except Exception as e:
        logger.debug(f"_get_engine_state error: {e}")
        return {}


def is_session_closed():
    """True if we're between 23:00 and 08:00 BRT and OPERATING_HOURS_ONLY is enabled."""
    if not getattr(config, "OPERATING_HOURS_ONLY", False):
        return False
    state = _get_engine_state()
    if state.get("session_closed") is False:
        return False  # Explicitly opened by daily_opener
    now = datetime.now(BRT).time()
    return now >= time(23, 0) or now < time(8, 0)


def clear_session_closed():
    """Called at daily_opener (08:00 BRT) to ensure we accept new signals."""
    if _engine_state_coll is None:
        return
    try:
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"session_closed": False}},
            upsert=True,
        )
        logger.info("Session opened (daily_opener)")
    except Exception as e:
        logger.debug(f"clear_session_closed error: {e}")


def record_message_sent():
    """Update last_message_at for keep-alive tracking. Called via callback when any message is sent."""
    if _engine_state_coll is None:
        return
    try:
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"last_message_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"record_message_sent error: {e}")


def check_and_send_keep_alive():
    """
    If channel silent for KEEP_ALIVE_SILENCE_MINUTES and NOT in cooldown, post keep-alive.
    Also handles volatility cooldown midpoint keep-alive.
    Rotates variants A/B/C, never same twice in a row. Max 1 per 5-min window.
    """
    if _engine_state_coll is None:
        return
    # Volatility cooldown midpoint: post one keep-alive midway through
    check_volatility_cooldown_midpoint()
    if is_in_volatility_cooldown():
        return  # Skip normal keep-alive during volatility cooldown (we have midpoint)
    if in_cooldown():
        return
    if _is_in_interrupted_cooldown():
        return
    if active_signal_exists():
        return  # Don't keep-alive while signal is active
    silence_min = getattr(config, "KEEP_ALIVE_SILENCE_MINUTES", 5)
    state = _get_engine_state()
    last_at = state.get("last_message_at")
    if last_at is None:
        return  # No message sent yet, skip
    if isinstance(last_at, datetime) and last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last_at < timedelta(minutes=silence_min):
        return
    last_variant = state.get("last_keep_alive_variant", -1)
    # Rotate: pick next variant (0,1,2) never same twice
    next_variant = (last_variant + 1) % 3
    try:
        telegram_service.send_keep_alive_message(next_variant)
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"last_message_at": datetime.now(timezone.utc), "last_keep_alive_variant": next_variant}},
            upsert=True,
        )
        logger.info(f"Keep-alive sent (variant {next_variant})")
    except Exception as e:
        logger.debug(f"check_and_send_keep_alive error: {e}")


def is_in_volatility_cooldown():
    """True if in volatility cooldown (3 consecutive rounds < 1.20x)."""
    state = _get_engine_state()
    until = state.get("volatility_cooldown_until")
    if until is None:
        return False
    try:
        if isinstance(until, datetime) and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until
    except Exception:
        return False


def _check_volatility_trigger(recent_rounds):
    """True if last 3 rounds all have multiplier < VOLATILITY_THRESHOLD (1.20x)."""
    threshold = getattr(config, "VOLATILITY_THRESHOLD", 1.20)
    if len(recent_rounds) < 3:
        return False
    last_3 = recent_rounds[:3]
    return all(r.get("multiplier", 999) < threshold for r in last_3)


def _enter_volatility_cooldown():
    """Enter volatility cooldown: 5-8 min pause, post message, schedule midpoint keep-alive."""
    if _engine_state_coll is None:
        return
    min_min = getattr(config, "VOLATILITY_COOLDOWN_MIN_MIN", 5)
    max_min = getattr(config, "VOLATILITY_COOLDOWN_MAX_MIN", 8)
    duration_min = random.randint(min_min, max_min)
    now = datetime.now(timezone.utc)
    until = now + timedelta(minutes=duration_min)
    try:
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {
                "volatility_cooldown_until": until,
                "volatility_cooldown_started_at": now,
                "volatility_cooldown_duration_min": duration_min,
                "volatility_cooldown_midpoint_sent": False,
            }},
            upsert=True,
        )
        telegram_service.send_cooldown_mode_message()
        logger.info(f"Volatility cooldown entered for {duration_min} minutes")
    except Exception as e:
        logger.debug(f"_enter_volatility_cooldown error: {e}")


def check_volatility_cooldown_midpoint():
    """If in volatility cooldown and past midpoint, post one keep-alive and mark sent."""
    if _engine_state_coll is None:
        return
    state = _get_engine_state()
    until = state.get("volatility_cooldown_until")
    started = state.get("volatility_cooldown_started_at")
    midpoint_sent = state.get("volatility_cooldown_midpoint_sent", False)
    if until is None or started is None or midpoint_sent:
        return
    try:
        if isinstance(until, datetime) and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if isinstance(started, datetime) and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except Exception:
        return
    duration_min = state.get("volatility_cooldown_duration_min", 5)
    midpoint = started + timedelta(minutes=duration_min / 2)
    now = datetime.now(timezone.utc)
    if now >= midpoint:
        # Pick a random keep-alive variant for midpoint
        variant = random.randint(0, 2)
        telegram_service.send_keep_alive_message(variant)
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"volatility_cooldown_midpoint_sent": True}},
        )
        logger.info("Volatility cooldown midpoint keep-alive sent")


def _consecutive_under_threshold(recent_rounds):
    """Count consecutive rounds from newest that are all < THRESHOLD. Returns 0, 1, 2, 3, ..."""
    if not recent_rounds:
        return 0
    count = 0
    for r in recent_rounds:
        if r.get("multiplier", 999) < config.THRESHOLD:
            count += 1
        else:
            break
    return count


def _pre_signal_sent_for_run():
    """True if we already sent Template 2 for this run (so we don't send again until trigger or reset)."""
    state = _get_engine_state()
    return state.get("pre_signal_sent", False) is True


def _set_pre_signal_sent(value):
    """Set/clear pre_signal_sent flag in engine state."""
    if _engine_state_coll is None:
        return
    try:
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"pre_signal_sent": value}},
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"_set_pre_signal_sent error: {e}")


def _is_in_interrupted_cooldown():
    """True if in V2 interrupted cooldown (2 min after Signal Interrupted). Stub returns False if not implemented."""
    state = _get_engine_state()
    last = state.get("last_signal_interrupted_at")
    if last is None:
        return False
    try:
        if isinstance(last, datetime) and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        since = datetime.now(timezone.utc) - last
        return since < timedelta(minutes=getattr(config, "INTERRUPTED_COOLDOWN_MINUTES", 2))
    except Exception:
        return False


def in_cooldown():
    """
    True if we are in cooldown: last loss set cooldown_until_round_id and
    current latest round _id is still less than that.
    """
    state = _get_engine_state()
    until = state.get("cooldown_until_round_id")
    if until is None:
        return False
    coll = _get_rounds_collection()
    if coll is None:
        return False
    try:
        latest = coll.find_one({"_id": {"$type": "int"}}, {"_id": 1}, sort=[("_id", -1)])
        if not latest:
            return True
        return latest["_id"] < until
    except Exception as e:
        logger.debug(f"in_cooldown check error: {e}")
        return False


def get_pattern_monitoring_data():
    """
    Returns (count, remaining) if we should send Template 2 (Pattern Monitoring), else None.
    Conditions: no active signal, not in cooldown, not in volatility cooldown, 3+ rounds < THRESHOLD.
    count = number of consecutive rounds from newest that are all < THRESHOLD.
    remaining = max(0, SEQUENCE_LENGTH - count).
    """
    if active_signal_exists():
        return None
    if in_cooldown():
        return None
    if is_in_volatility_cooldown():
        return None
    recent = get_recent_rounds(10)
    if len(recent) < 3:
        return None
    count = 0
    for r in recent:
        if r.get("multiplier", 0) < config.THRESHOLD:
            count += 1
        else:
            break
    if count < 3:
        return None
    remaining = max(0, config.SEQUENCE_LENGTH - count)
    return (count, remaining)


def check_trigger(recent_rounds):
    """
    recent_rounds: list of last 8 rounds (newest first), each with 'multiplier'.
    Returns True if: last SEQUENCE_LENGTH rounds all < THRESHOLD, no active signal, not in cooldown.
    """
    if active_signal_exists():
        return False
    if in_cooldown():
        return False
    if is_in_volatility_cooldown():
        return False
    if is_session_closed():
        return False
    if len(recent_rounds) < config.SEQUENCE_LENGTH:
        return False
    last_six = recent_rounds[: config.SEQUENCE_LENGTH]
    if all(r.get("multiplier", 0) < config.THRESHOLD for r in last_six):
        return True
    return False


def _next_signal_id():
    if _signals_coll is None:
        return 1
    try:
        doc = _signals_coll.find_one(sort=[("_id", -1)])
        if doc and isinstance(doc.get("_id"), int):
            return doc["_id"] + 1
        return 1
    except Exception as e:
        logger.debug(f"_next_signal_id error: {e}")
        return 1


def _today_str():
    """Today's date in BRT (YYYY-MM-DD) for consistent daily_stats across timezones."""
    return datetime.now(BRT).date().isoformat()


def _ensure_daily_stats():
    """Ensure today's daily_stats row exists; create if not."""
    if _daily_stats_coll is None:
        return
    today = _today_str()
    try:
        _daily_stats_coll.update_one(
            {"_id": today},
            {
                "$setOnInsert": {
                    "_id": today,
                    "date": today,
                    "wins": 0,
                    "losses": 0,
                    "signals_sent": 0,
                    "today_wins": 0,
                    "today_losses": 0,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"_ensure_daily_stats error: {e}")


def create_signal(trigger_round_id, target):
    """
    Create a new active signal. Increment daily signals_sent.
    trigger_round_id: _id of the round that triggered (first of the 6).
    """
    if _signals_coll is None:
        return None
    _ensure_daily_stats()
    # Template 2 (Pre-Signal) was already sent earlier; here we send only Template 3 (on time with this round)
    now = datetime.now(timezone.utc)
    today = _today_str()
    sig_id = _next_signal_id()
    doc = {
        "_id": sig_id,
        "trigger_round_id": trigger_round_id,
        "target": float(target),
        "status": STATUS_ACTIVE,
        "result_round_id": None,
        "result_multiplier": None,
        "gale_depth": 0,
        "created_at": now,
        "resolved_at": None,
    }
    try:
        _signals_coll.insert_one(doc)
        # today_wins = signals_sent - today_losses (create/backfill today_wins explicitly)
        stats_doc = _daily_stats_coll.find_one({"_id": today}) or {}
        current_signals = stats_doc.get("signals_sent", 0)
        current_today_losses = stats_doc.get("today_losses", 0)
        new_signals_sent = current_signals + 1
        today_wins_value = max(0, new_signals_sent - current_today_losses)
        _daily_stats_coll.update_one(
            {"_id": today},
            {
                "$inc": {"signals_sent": 1},
                "$set": {
                    "today_wins": today_wins_value,
                    "updated_at": now,
                },
            },
            upsert=True,
        )
        logger.info(f"Signal created: id={sig_id}, trigger_round_id={trigger_round_id}, target={target}")
        
        # Send signal message (Template 3) and store message_id for reply threading
        recent = get_recent_rounds(1)
        last_round = recent[0].get("multiplier") if recent else 0
        msg_id = telegram_service.send_signal(last_round=last_round, target=target)
        if msg_id is not None:
            _signals_coll.update_one(
                {"_id": sig_id},
                {"$set": {"telegram_message_id": msg_id}},
            )
            doc["telegram_message_id"] = msg_id
        
        return doc
    except Exception as e:
        logger.error(f"create_signal error: {e}")
        return None


def resolve_signal(signal, round_data):
    """
    Resolve an active signal with the new round result.
    round_data: { _id, multiplier, ... }
    """
    multiplier = round_data.get("multiplier")
    target = signal.get("target")
    if multiplier is None or target is None:
        logger.warning("resolve_signal: missing multiplier or target")
        return

    if multiplier >= target:
        # WIN (or recovery)
        mark_won(signal, round_data)
        increment_daily_wins()  # Increment BEFORE sending so Telegram shows correct stats
        if signal.get("gale_depth", 0) == 0:
            send_win_message(signal, round_data)
        else:
            send_recovery_message(signal, round_data)
    else:
        # Did not hit target
        gale_depth = signal.get("gale_depth", 0)
        if gale_depth < config.MAX_GALE:
            new_depth = gale_depth + 1
            update_signal_gale(signal, new_depth)
            send_gale_message(signal, new_depth)
        else:
            mark_lost(signal, round_data)
            increment_daily_losses()  # Increment BEFORE sending so Telegram shows correct stats
            send_loss_message(signal, round_data)
            start_cooldown(config.COOLDOWN_ROUNDS, round_data.get("_id"))


def mark_won(signal, round_data):
    now = datetime.now(timezone.utc)
    _signals_coll.update_one(
        {"_id": signal["_id"]},
        {
            "$set": {
                "status": STATUS_WON,
                "result_round_id": round_data.get("_id"),
                "result_multiplier": round_data.get("multiplier"),
                "resolved_at": now,
            }
        },
    )
    logger.info(f"Signal {signal['_id']} WON at multiplier {round_data.get('multiplier')}")


def mark_lost(signal, round_data):
    now = datetime.now(timezone.utc)
    _signals_coll.update_one(
        {"_id": signal["_id"]},
        {
            "$set": {
                "status": STATUS_LOST,
                "result_round_id": round_data.get("_id"),
                "result_multiplier": round_data.get("multiplier"),
                "resolved_at": now,
            }
        },
    )
    logger.info(f"Signal {signal['_id']} LOST at multiplier {round_data.get('multiplier')}")


def update_signal_gale(signal, new_depth):
    status = STATUS_GALE1 if new_depth == 1 else STATUS_GALE2
    _signals_coll.update_one(
        {"_id": signal["_id"]},
        {"$set": {"status": status, "gale_depth": new_depth}},
    )
    logger.info(f"Signal {signal['_id']} escalated to gale {new_depth}")


def increment_daily_wins():
    _ensure_daily_stats()
    today = _today_str()
    now = datetime.now(timezone.utc)
    _daily_stats_coll.update_one(
        {"_id": today},
        {"$inc": {"wins": 1}, "$set": {"updated_at": now}},
    )


def increment_daily_losses():
    _ensure_daily_stats()
    today = _today_str()
    now = datetime.now(timezone.utc)
    # today_wins = signals_sent - today_losses (create/backfill today_wins explicitly)
    stats_doc = _daily_stats_coll.find_one({"_id": today}) or {}
    current_signals = stats_doc.get("signals_sent", 0)
    current_today_losses = stats_doc.get("today_losses", 0)
    new_today_losses = current_today_losses + 1
    today_wins_value = current_signals - new_today_losses
    _daily_stats_coll.update_one(
        {"_id": today},
        {
            "$inc": {"losses": 1, "today_losses": 1},
            "$set": {
                "today_wins": max(0, today_wins_value),
                "updated_at": now,
            },
        },
        upsert=True,
    )


def reset_daily_stats_after_two_losses():
    """
    When today_losses has just reached 2 (two losses in a row), reset today's
    display counters so the next messages show "1 ✅ | 1 ❌".
    Sets wins=0, losses=1 so next win shows 1✅|1❌ and next loss shows 2✅|1❌ then 2❌.
    """
    if _daily_stats_coll is None:
        return
    today = _today_str()
    now = datetime.now(timezone.utc)
    try:
        _daily_stats_coll.update_one(
            {"_id": today},
            {"$set": {"wins": 0, "losses": 1, "updated_at": now}},
            upsert=True,
        )
        logger.info("Daily stats reset after 2 losses in a row (wins=0, losses=1)")
    except Exception as e:
        logger.debug(f"reset_daily_stats_after_two_losses error: {e}")


def start_cooldown(rounds_count, result_round_id):
    """Start cooldown: no new signal until result_round_id + rounds_count."""
    if result_round_id is None or _engine_state_coll is None:
        return
    until = result_round_id + rounds_count
    try:
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"cooldown_until_round_id": until}},
            upsert=True,
        )
        logger.info(f"Cooldown started until round_id >= {until}")
    except Exception as e:
        logger.debug(f"start_cooldown error: {e}")


# --- Message hooks: Telegram integration ---

# For tracking streak (reset _last_streak_celebration on loss so we celebrate again)
_current_streak = 0
_last_streak_celebration = 0

STREAK_MILESTONES_BASE = [3, 5, 7, 10]  # 3, 5, 7, 10, then every 5: 15, 20, 25, 30...


def _is_streak_milestone(count):
    """True if count is a milestone: 3, 5, 7, 10, 15, 20, 25, 30..."""
    if count in STREAK_MILESTONES_BASE:
        return True
    if count >= 15 and count % 5 == 0:
        return True
    return False


def _persist_consecutive_wins(count):
    """Persist consecutive_wins to engine_state (state manager)."""
    if _engine_state_coll is None:
        return
    try:
        _engine_state_coll.update_one(
            {"_id": "state"},
            {"$set": {"consecutive_wins": count}},
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"_persist_consecutive_wins error: {e}")


def _get_consecutive_wins_from_db():
    """
    Get current consecutive win streak from resolved signals (most recent first).
    Used to restore streak after process restart.
    """
    if _signals_coll is None:
        return 0
    try:
        cursor = _signals_coll.find(
            {"status": {"$in": [STATUS_WON, STATUS_LOST]}},
            {"status": 1},
        ).sort("resolved_at", -1).limit(50)
        count = 0
        for doc in cursor:
            if doc.get("status") == STATUS_WON:
                count += 1
            else:
                break
        return count
    except Exception as e:
        logger.debug(f"_get_consecutive_wins_from_db error: {e}")
        return 0


def _get_today_stats():
    """Get today's daily_stats (wins, losses)."""
    if _daily_stats_coll is None:
        return {"wins": 0, "losses": 0}
    today = _today_str()
    try:
        doc = _daily_stats_coll.find_one({"_id": today})
        if doc:
            return {"wins": doc.get("wins", 0), "losses": doc.get("losses", 0)}
        return {"wins": 0, "losses": 0}
    except Exception as e:
        logger.debug(f"_get_today_stats error: {e}")
        return {"wins": 0, "losses": 0}


def send_win_message(signal, round_data):
    """Send win message (gale_depth = 0)."""
    global _current_streak
    logger.info(f"[WIN] Signal {signal['_id']} | target={signal['target']} | result={round_data.get('multiplier')}")
    
    # Restore streak from DB if needed (e.g. after process restart); else increment
    if _current_streak == 0:
        _current_streak = _get_consecutive_wins_from_db()  # Already includes this win (mark_won was called)
    else:
        _current_streak += 1
    _persist_consecutive_wins(_current_streak)

    stats = _get_today_stats()
    telegram_service.send_win_result(
        result=round_data.get("multiplier"),
        target=signal["target"],
        today_wins=stats["wins"],
        today_losses=stats["losses"],
        reply_to_message_id=signal.get("telegram_message_id"),
    )
    # Streak celebration (5, 10, 15, ...) sent after win message
    _check_streak_celebration()


def send_recovery_message(signal, round_data):
    """Send recovery message (gale 1 or 2 hit target)."""
    global _current_streak
    logger.info(
        f"[RECOVERED] Signal {signal['_id']} gale_depth={signal.get('gale_depth')} | "
        f"result={round_data.get('multiplier')}"
    )
    
    if _current_streak == 0:
        _current_streak = _get_consecutive_wins_from_db()  # Already includes this win (mark_won was called)
    else:
        _current_streak += 1
    _persist_consecutive_wins(_current_streak)

    stats = _get_today_stats()
    telegram_service.send_gale_recovery(
        gale_depth=signal.get("gale_depth"),
        result=round_data.get("multiplier"),
        target=signal["target"],
        today_wins=stats["wins"],
        today_losses=stats["losses"],
        reply_to_message_id=signal.get("telegram_message_id"),
    )
    # Streak celebration (5, 10, 15, ...) sent after recovery message
    _check_streak_celebration()


def send_gale_message(signal, new_depth):
    """Send gale escalation message (gale 1 or gale 2)."""
    logger.info(f"[GALE] Signal {signal['_id']} escalated to gale {new_depth}")
    
    # Get the last round multiplier (result that missed)
    recent = get_recent_rounds(1)
    last_mult = recent[0].get("multiplier") if recent else 0
    
    reply_to = signal.get("telegram_message_id")
    if new_depth == 1:
        telegram_service.send_gale1_trigger(
            result=last_mult, target=signal["target"], reply_to_message_id=reply_to
        )
    elif new_depth == 2:
        telegram_service.send_gale2_trigger(
            result=last_mult, target=signal["target"], reply_to_message_id=reply_to
        )


def send_loss_message(signal, round_data):
    """Send loss message (gale 2 failed). Reset consecutive_wins on stop loss."""
    global _current_streak, _last_streak_celebration  # Reset both on loss
    logger.info(
        f"[LOSS] Signal {signal['_id']} | target={signal['target']} | "
        f"result={round_data.get('multiplier')}"
    )
    
    _current_streak = 0
    _last_streak_celebration = 0
    _persist_consecutive_wins(0)
    
    stats = _get_today_stats()
    telegram_service.send_loss_message_telegram(
        result=round_data.get("multiplier"),
        today_wins=stats["wins"],
        today_losses=stats["losses"],
        reply_to_message_id=signal.get("telegram_message_id"),
    )
    # When we hit 2 losses in a row, reset display counters so next messages show 1✅|1❌
    if stats["losses"] == 2:
        reset_daily_stats_after_two_losses()


def _check_streak_celebration():
    """Check if we hit a streak milestone (3, 5, 7, 10, 15, 20, 25...) and send alert."""
    global _last_streak_celebration
    if _is_streak_milestone(_current_streak) and _current_streak > _last_streak_celebration:
        telegram_service.send_streak_celebration(_current_streak)
        _last_streak_celebration = _current_streak


def on_new_round(round_data):
    """
    Main entry: called for each new round stored.
    round_data: { _id, multiplier, timestamp, ... }
    - If there is an active signal, resolve it.
    - Check volatility trigger (3 rounds < 1.20x) -> enter cooldown.
    - Else if trigger condition met and not in cooldowns, create a new signal.
    """
    if _db is None:
        return
    recent = get_recent_rounds(8)
    active = get_active_signal()

    if active:
        resolve_signal(active, round_data)
        return
    # Volatility cooldown: 3 consecutive rounds < 1.20x -> pause signaling 5-8 min
    if _check_volatility_trigger(recent) and not is_in_volatility_cooldown():
        _enter_volatility_cooldown()
        return
    if is_in_volatility_cooldown():
        return
    if in_cooldown() or is_session_closed():
        return
    consecutive = _consecutive_under_threshold(recent)
    # Trigger fires (3 consecutive < THRESHOLD): send Template 3 immediately
    if check_trigger(recent):
        _set_pre_signal_sent(False)
        trigger_round_id = recent[0]["_id"] if recent else round_data.get("_id")
        create_signal(trigger_round_id, config.TARGET_CASHOUT)
        return
    # One round before trigger (2 consecutive): send Template 2 once
    if consecutive == 2 and not _pre_signal_sent_for_run():
        telegram_service.send_pre_signal_analyzing()
        _set_pre_signal_sent(True)
    if consecutive < 2:
        _set_pre_signal_sent(False)
