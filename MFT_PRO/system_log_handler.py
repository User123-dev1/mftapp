"""
System Log Handler - Captures application logs for display in UI
"""
import logging
import threading
from collections import deque
from datetime import datetime
from typing import List, Dict, Optional


class SystemLogHandler(logging.Handler):
    """Custom logging handler that captures logs to memory for UI display"""

    def __init__(self, max_logs=1000):
        super().__init__()
        self.max_logs = max_logs
        self.logs = deque(maxlen=max_logs)
        self.lock = threading.Lock()

        # Set format
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.setFormatter(formatter)

    def emit(self, record):
        """Capture log record"""
        try:
            with self.lock:
                # Format the message
                msg = self.format(record)

                # Create log entry
                log_entry = {
                    'timestamp': datetime.fromtimestamp(record.created).isoformat(),
                    'level': record.levelname,
                    'logger': record.name,
                    'message': record.getMessage(),
                    'formatted': msg,
                    'pathname': record.pathname,
                    'lineno': record.lineno,
                    'funcName': record.funcName
                }

                # Add exception info if present
                if record.exc_info:
                    log_entry['exception'] = self.formatter.formatException(record.exc_info)

                self.logs.append(log_entry)
        except Exception:
            self.handleError(record)

    def get_logs(self, limit: Optional[int] = None, level: Optional[str] = None,
                 logger_name: Optional[str] = None) -> List[Dict]:
        """Get captured logs with optional filters"""
        with self.lock:
            logs = list(self.logs)

        # Filter by level
        if level:
            level_upper = level.upper()
            logs = [log for log in logs if log['level'] == level_upper]

        # Filter by logger name
        if logger_name:
            logs = [log for log in logs if logger_name.lower() in log['logger'].lower()]

        # Limit results
        if limit:
            logs = logs[-limit:]  # Get most recent logs

        # Reverse to show newest first
        logs.reverse()

        return logs

    def clear_logs(self):
        """Clear all captured logs"""
        with self.lock:
            self.logs.clear()

    def get_stats(self) -> Dict:
        """Get statistics about captured logs"""
        with self.lock:
            logs = list(self.logs)

        stats = {
            'total': len(logs),
            'debug': 0,
            'info': 0,
            'warning': 0,
            'error': 0,
            'critical': 0
        }

        for log in logs:
            level = log['level'].lower()
            if level in stats:
                stats[level] += 1

        return stats


# Global instance
_system_log_handler: Optional[SystemLogHandler] = None


def get_system_log_handler() -> SystemLogHandler:
    """Get or create the global system log handler"""
    global _system_log_handler

    if _system_log_handler is None:
        _system_log_handler = SystemLogHandler(max_logs=1000)

        # Attach to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(_system_log_handler)

        # Set level to capture everything
        _system_log_handler.setLevel(logging.DEBUG)

    return _system_log_handler


def init_system_log_handler():
    """Initialize the system log handler"""
    return get_system_log_handler()
