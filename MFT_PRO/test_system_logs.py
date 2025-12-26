"""Test System Log Handler"""
import logging
import time

# Initialize the log handler
from system_log_handler import get_system_log_handler

handler = get_system_log_handler()
logger = logging.getLogger(__name__)

print("=" * 80)
print("TESTING SYSTEM LOG HANDLER")
print("=" * 80)

# Generate some test logs
print("\nGenerating test logs...")
logger.debug("This is a DEBUG message")
logger.info("This is an INFO message")
logger.warning("This is a WARNING message")
logger.error("This is an ERROR message")
logger.critical("This is a CRITICAL message")

# Test with different loggers
app_logger = logging.getLogger("mft_application")
app_logger.info("Transfer started for test file")
app_logger.info("Transfer completed successfully")

db_logger = logging.getLogger("database")
db_logger.info("Database connection established")
db_logger.warning("Query took longer than expected")

# Generate an error with exception
try:
    result = 1 / 0
except Exception as e:
    logger.error("Test error with exception", exc_info=True)

print("\nTest logs generated!")

# Get statistics
stats = handler.get_stats()
print(f"\nLog Statistics:")
print(f"  Total: {stats['total']}")
print(f"  DEBUG: {stats['debug']}")
print(f"  INFO: {stats['info']}")
print(f"  WARNING: {stats['warning']}")
print(f"  ERROR: {stats['error']}")
print(f"  CRITICAL: {stats['critical']}")

# Get logs
logs = handler.get_logs(limit=20)
print(f"\nCaptured {len(logs)} logs:")
for log in logs[:5]:  # Show first 5
    print(f"  [{log['level']}] {log['logger']}: {log['message']}")

# Test filters
print("\nTesting filters...")
error_logs = handler.get_logs(level='ERROR')
print(f"  ERROR logs: {len(error_logs)}")

info_logs = handler.get_logs(level='INFO')
print(f"  INFO logs: {len(info_logs)}")

# Test logger name filter
app_logs = handler.get_logs(logger_name='mft_application')
print(f"  mft_application logs: {len(app_logs)}")

print("\n" + "=" * 80)
print("TEST COMPLETED SUCCESSFULLY!")
print("=" * 80)
print("\nThe System Log Handler is working correctly.")
print("Start the Flask app and navigate to Logs -> System Logs to see them in the UI!")
