"""
Signal Engine: trigger logic, resolution (gale), state machine, and persistence.
Uses MongoDB collections: rounds (read), signals, daily_stats, engine_state (cooldown).
"""
import logging
from datetime import datetime, timezone, date, time
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
    Conditions: no active signal, not in cooldown, 3+ consecutive rounds (from newest) < THRESHOLD.
    count = number of consecutive rounds from newest that are all < THRESHOLD.
    remaining = max(0, SEQUENCE_LENGTH - count).
    """
    if active_signal_exists():
        return None
    if in_cooldown():
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
    # Template 2: Pattern monitoring sent just before Template 3 (signal)
    pattern_data = get_pattern_monitoring_data()
    if pattern_data:
        count, remaining = pattern_data
        telegram_service.send_pattern_monitoring(count, remaining)
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
        
        # Send signal message (Template 3)
        recent = get_recent_rounds(1)
        last_round = recent[0].get("multiplier") if recent else 0
        telegram_service.send_signal(last_round=last_round, target=target)
        
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

    stats = _get_today_stats()
    telegram_service.send_win_result(
        result=round_data.get("multiplier"),
        target=signal["target"],
        today_wins=stats["wins"],
        today_losses=stats["losses"],
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

    stats = _get_today_stats()
    telegram_service.send_gale_recovery(
        gale_depth=signal.get("gale_depth"),
        result=round_data.get("multiplier"),
        target=signal["target"],
        today_wins=stats["wins"],
        today_losses=stats["losses"],
    )
    # Streak celebration (5, 10, 15, ...) sent after recovery message
    _check_streak_celebration()


def send_gale_message(signal, new_depth):
    """Send gale escalation message (gale 1 or gale 2)."""
    logger.info(f"[GALE] Signal {signal['_id']} escalated to gale {new_depth}")
    
    # Get the last round multiplier (result that missed)
    recent = get_recent_rounds(1)
    last_mult = recent[0].get("multiplier") if recent else 0
    
    if new_depth == 1:
        telegram_service.send_gale1_trigger(result=last_mult, target=signal["target"])
    elif new_depth == 2:
        telegram_service.send_gale2_trigger(result=last_mult, target=signal["target"])


def send_loss_message(signal, round_data):
    """Send loss message (gale 2 failed)."""
    global _current_streak, _last_streak_celebration  # Reset both on loss
    logger.info(
        f"[LOSS] Signal {signal['_id']} | target={signal['target']} | "
        f"result={round_data.get('multiplier')}"
    )
    
    _current_streak = 0
    _last_streak_celebration = 0  # Reset so we celebrate again when we hit 5/10/15 next time
    
    stats = _get_today_stats()
    telegram_service.send_loss_message_telegram(
        result=round_data.get("multiplier"),
        today_wins=stats["wins"],
        today_losses=stats["losses"],
    )
    # When we hit 2 losses in a row, reset display counters so next messages show 1✅|1❌
    if stats["losses"] == 2:
        reset_daily_stats_after_two_losses()


def _check_streak_celebration():
    """Check if we hit a streak milestone (5, 10, 15, 20) and send celebration."""
    global _last_streak_celebration
    if _current_streak in [5, 10] and _current_streak > _last_streak_celebration:
        telegram_service.send_streak_celebration(_current_streak)
        _last_streak_celebration = _current_streak
    elif _current_streak >= 15 and _current_streak > _last_streak_celebration:
        # For 15+ send every 5 (15, 20, 25, ...)
        if _current_streak % 5 == 0:
            telegram_service.send_streak_celebration(_current_streak, "✅" * _current_streak)
            _last_streak_celebration = _current_streak


def on_new_round(round_data):
    """
    Main entry: called for each new round stored.
    round_data: { _id, multiplier, timestamp, ... }
    - If there is an active signal, resolve it.
    - Else if trigger condition met, create a new signal.
    """
    if _db is None:
        return
    recent = get_recent_rounds(8)
    active = get_active_signal()

    if active:
        resolve_signal(active, round_data)
    elif check_trigger(recent):
        # Trigger round: the newest round in the sequence (first of last 6)
        trigger_round_id = recent[0]["_id"] if recent else round_data.get("_id")
        create_signal(trigger_round_id, config.TARGET_CASHOUT)
