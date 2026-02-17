"""
Log Monitor for aviator.py
Monitors the log file and saves newly detected payouts to MongoDB
"""
import ast
import logging
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

from pymongo import MongoClient

import config
import signal_engine
import telegram_service
import scheduler

# Configure logging for this monitor
MONITOR_LOG_FILENAME = "log_monitor.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(MONITOR_LOG_FILENAME, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# MongoDB connection
mongo_client = None
mongo_collection = None


def init_mongodb():
    """Initialize MongoDB connection, signal engine, Telegram, and scheduler."""
    global mongo_client, mongo_collection
    if not config.MONGODB_URI:
        logger.error("MONGODB_URI environment variable is not set. Exiting.")
        return False
    try:
        mongo_client = MongoClient(config.MONGODB_URI)
        mongo_db = mongo_client[config.MONGODB_DATABASE]
        mongo_collection = mongo_db[config.MONGODB_COLLECTION]
        logger.info(f"Connected to MongoDB: {config.MONGODB_DATABASE}.{config.MONGODB_COLLECTION}")
        
        # Initialize signal engine
        signal_engine.init(mongo_db)
        
        # Initialize Telegram
        telegram_service.init()
        
        # Initialize scheduler (for daily/hourly messages)
        scheduler.init(mongo_db)
        
        return True
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return False


def convert_multiplier_to_decimal(multiplier_str):
    """Convert multiplier string (e.g., '2.50x') to Decimal"""
    try:
        # Strip trailing 'x'/'X' and normalize thousand/decimal separators.
        # Some casinos format big multipliers as '1,640.11x' – Decimal cannot
        # parse the comma, so we remove any thousand separators.
        multiplier_value = re.sub(r'[xX]', '', multiplier_str.strip())
        multiplier_value = multiplier_value.replace(',', '')
        return Decimal(multiplier_value)
    except Exception as e:
        logger.error(f"Error converting multiplier '{multiplier_str}': {e}")
        return None


def save_round_to_db(multiplier_str, round_timestamp):
    """Save a round to MongoDB database with sequential integer _id"""
    global mongo_collection

    if mongo_collection is None:
        logger.warning("MongoDB collection not initialized. Skipping database save.")
        return False

    try:
        multiplier = convert_multiplier_to_decimal(multiplier_str)
        if multiplier is None:
            return False

        max_round = None
        try:
            for doc in mongo_collection.find().sort("_id", -1).limit(100):
                doc_id = doc.get("_id")
                if isinstance(doc_id, int):
                    max_round = doc
                    break
        except Exception as e:
            logger.debug(f"Error finding max integer ID: {e}")

        if max_round and isinstance(max_round.get("_id"), int):
            next_id = max_round["_id"] + 1
        else:
            total_count = mongo_collection.count_documents({})
            if total_count == 0:
                next_id = 1
            else:
                logger.warning(
                    "Collection contains ObjectIds. Starting integer IDs from 1000000. "
                    "Existing ObjectId documents will be ignored for sequence detection."
                )
                next_id = 1000000

        document = {
            "_id": next_id,
            "multiplier": float(multiplier),
            "timestamp": round_timestamp,
            "created_at": datetime.now(timezone.utc)
        }

        mongo_collection.insert_one(document)
        logger.info(f"✅ Saved to DB: _id={next_id}, multiplier={multiplier}, timestamp={round_timestamp}")
        return document
    except Exception as e:
        logger.error(f"Error saving round to database: {e}")
        return None


def parse_payout_from_log(line):
    """
    Parse payout information from log line.
    Expected format: "Found X payouts | ['2.50x', '1.80x', ...]"
    Returns: list of payout strings or None
    """
    try:
        if config.LOG_PAYOUTS_FOUND_PREFIX not in line or config.LOG_PAYOUTS_FOUND_SEP not in line:
            return None
        match = re.search(r"Found \d+ payouts \| (\[.*\])", line)
        if match:
            payout_list_str = match.group(1)
            payout_list = ast.literal_eval(payout_list_str)
            if isinstance(payout_list, list):
                return payout_list
    except (ValueError, SyntaxError) as e:
        logger.debug(f"Error parsing payout from log line: {e}")
    return None


def _process_lines(new_lines, previous_payout_list):
    """Process new log lines and return updated previous_payout_list."""
    for line in new_lines:
        if config.LOG_PAYOUTS_FOUND_PREFIX not in line or "payouts" not in line:
            continue
        payout_list = parse_payout_from_log(line)
        if not payout_list:
            continue

        if previous_payout_list is not None:
            new_values = []
            if payout_list and previous_payout_list:
                previous_first = previous_payout_list[0]
                try:
                    match_index = payout_list.index(previous_first)
                    if match_index > 0:
                        new_values = payout_list[:match_index]
                    elif payout_list[0] != previous_first:
                        new_values = [payout_list[0]]
                except ValueError:
                    if payout_list[0] != previous_payout_list[0]:
                        new_values = [payout_list[0]]

            if new_values:
                if len(new_values) == 1:
                    new_value = new_values[0]
                    round_timestamp = datetime.now(timezone.utc)
                    logger.info(f"🆕 NEW PAYOUT DETECTED from log | New Value: {new_value}")
                    round_doc = save_round_to_db(new_value, round_timestamp)
                    if round_doc:
                        signal_engine.on_new_round(round_doc)
                    previous_payout_list = payout_list.copy()
                else:
                    logger.info(f"🆕 {len(new_values)} NEW PAYOUTS DETECTED from log | New Values: {new_values}")
                    for multiplier_str in reversed(new_values):
                        round_timestamp = datetime.now(timezone.utc)
                        logger.info(f"   Saving payout: {multiplier_str}")
                        round_doc = save_round_to_db(multiplier_str, round_timestamp)
                        if round_doc:
                            signal_engine.on_new_round(round_doc)
                        time.sleep(0.1)
                    previous_payout_list = payout_list.copy()
                    logger.info(f"✅ All {len(new_values)} new payouts saved to database")
        else:
            logger.info(f"Initial payout list loaded from log: {len(payout_list)} payouts")
            previous_payout_list = payout_list.copy()

    return previous_payout_list


def monitor_log_file():
    """Monitor log file and save new payouts to MongoDB. Reopens file on rotation/truncation."""
    global mongo_collection

    if not init_mongodb():
        logger.error("MongoDB initialization failed. Exiting.")
        return

    previous_payout_list = None
    log_path = config.LOG_FILE

    logger.info(f"Starting to monitor log file: {log_path}")
    logger.info("Will only read new lines (tail-like). Reopens file on rotation/truncation.")

    while True:
        while not os.path.exists(log_path):
            logger.info("Waiting for log file to be created...")
            time.sleep(2)

        try:
            with open(log_path, 'rb') as f:
                f.seek(0, 2)
                last_position = f.tell()
                file_size_mb = last_position / (1024 * 1024)
                if file_size_mb > 10:
                    logger.info(
                        f"Log file is large ({file_size_mb:.2f} MB). Starting from end - will only process new lines."
                    )

                while True:
                    try:
                        current_size = os.path.getsize(log_path)
                        if current_size < last_position:
                            logger.info("Log file truncated/rotated. Reopening file.")
                            previous_payout_list = None
                            break

                        f.seek(last_position)
                        chunk_size = 8192
                        buffer = b''
                        new_lines = []

                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            buffer += chunk
                            while b'\n' in buffer:
                                line_bytes, buffer = buffer.split(b'\n', 1)
                                try:
                                    decoded_line = line_bytes.decode('utf-8', errors='ignore')
                                    new_lines.append(decoded_line)
                                except Exception:
                                    continue
                        if buffer:
                            try:
                                decoded_line = buffer.decode('utf-8', errors='ignore')
                                if decoded_line.strip():
                                    new_lines.append(decoded_line)
                            except Exception:
                                pass

                        if new_lines:
                            previous_payout_list = _process_lines(new_lines, previous_payout_list)

                        last_position = f.tell()
                        time.sleep(2)

                    except IOError as e:
                        logger.debug(f"IOError reading log file: {e}")
                        time.sleep(1)
                        try:
                            f.seek(last_position)
                        except Exception:
                            f.seek(0, 2)
                            last_position = f.tell()

        except FileNotFoundError:
            logger.warning(f"Log file not found: {log_path}. Will retry.")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error monitoring log file: {e}", exc_info=True)
            time.sleep(5)

        # After rotation/truncation we break out of "with", file is closed; loop reopens


def main():
    try:
        monitor_log_file()
    finally:
        scheduler.shutdown()
        if mongo_client:
            try:
                mongo_client.close()
                logger.info("MongoDB connection closed")
            except Exception:
                pass


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Log Monitor for aviator.py")
    logger.info("Monitors log file and saves new payouts to MongoDB")
    logger.info("=" * 60)
    logger.info("")
    main()
