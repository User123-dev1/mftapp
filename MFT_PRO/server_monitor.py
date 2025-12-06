"""
Server Health Monitoring System
Monitors remote servers for connectivity and logs offline/online events
"""

import socket
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ServerStatus:
    """Status information for a monitored server"""
    host: str  # Can be hostname, FQDN, or IP address
    port: int
    protocol: str
    is_online: bool
    last_check: datetime
    last_online: Optional[datetime] = None
    last_offline: Optional[datetime] = None
    offline_since: Optional[datetime] = None
    total_downtime_seconds: float = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    # IP address tracking for DHCP environments
    resolved_ip: Optional[str] = None  # Last resolved IP address
    previous_ip: Optional[str] = None  # Previous IP (for change detection)
    is_fqdn: bool = False  # True if host is a hostname/FQDN, False if IP
    ip_changed: bool = False  # True if IP changed since last check


class ServerHealthMonitor:
    """Monitors server health and tracks connectivity"""

    def __init__(self, audit_manager=None, check_interval=30):
        """
        Initialize server health monitor

        Args:
            audit_manager: Audit manager for logging events
            check_interval: Seconds between health checks (default: 30)
        """
        self.audit_manager = audit_manager
        self.check_interval = check_interval
        self.servers: Dict[str, ServerStatus] = {}
        self.monitoring = False
        self.monitor_thread = None
        self.activity_log: List[Dict] = []
        self.max_activity_log_size = 1000

    def add_server(self, host: str, port: int, protocol: str):
        """Add a server to monitor"""
        server_key = f"{host}:{port}"

        if server_key not in self.servers:
            self.servers[server_key] = ServerStatus(
                host=host,
                port=port,
                protocol=protocol,
                is_online=True,  # Assume online initially
                last_check=datetime.now()
            )
            logger.info(f"📡 Added server to monitoring: {host}:{port} ({protocol})")

    def remove_server(self, host: str, port: int):
        """Remove a server from monitoring"""
        server_key = f"{host}:{port}"
        if server_key in self.servers:
            del self.servers[server_key]
            logger.info(f"🔌 Removed server from monitoring: {host}:{port}")

    def check_server_connectivity(self, host: str, port: int, timeout=5) -> bool:
        """
        Check if a server is reachable

        Args:
            host: Server hostname or IP
            port: Server port
            timeout: Connection timeout in seconds

        Returns:
            True if server is reachable, False otherwise
        """
        try:
            # Try to establish a TCP connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()

            return result == 0
        except socket.gaierror:
            # DNS resolution failed
            return False
        except Exception as e:
            logger.debug(f"Connection check failed for {host}:{port}: {e}")
            return False

    def _log_activity(self, event_type: str, server_key: str, message: str, details: Dict = None):
        """Log activity to activity log"""
        activity = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'server': server_key,
            'message': message,
            'details': details or {}
        }

        self.activity_log.insert(0, activity)

        # Limit activity log size
        if len(self.activity_log) > self.max_activity_log_size:
            self.activity_log = self.activity_log[:self.max_activity_log_size]

    def _monitor_loop(self):
        """Main monitoring loop"""
        logger.info(f"\n{'='*80}")
        logger.info(f"🔍 SERVER HEALTH MONITOR STARTED")
        logger.info(f"{'='*80}")
        logger.info(f"Check interval: {self.check_interval} seconds")
        logger.info(f"Monitoring {len(self.servers)} servers")
        logger.info(f"{'='*80}\n")

        while self.monitoring:
            try:
                for server_key, status in self.servers.items():
                    # Check connectivity
                    is_reachable = self.check_server_connectivity(status.host, status.port)
                    now = datetime.now()

                    # Update last check time
                    status.last_check = now

                    if is_reachable:
                        status.consecutive_successes += 1
                        status.consecutive_failures = 0

                        # Server came back online
                        if not status.is_online and status.consecutive_successes >= 2:
                            # Calculate downtime
                            if status.offline_since:
                                downtime = (now - status.offline_since).total_seconds()
                                status.total_downtime_seconds += downtime
                                downtime_str = self._format_duration(downtime)
                            else:
                                downtime = 0
                                downtime_str = "Unknown"

                            logger.info(f"\n{'='*80}")
                            logger.info(f"✅ SERVER BACK ONLINE")
                            logger.info(f"{'='*80}")
                            logger.info(f"Server: {status.host}:{status.port}")
                            logger.info(f"Protocol: {status.protocol}")
                            logger.info(f"Downtime: {downtime_str}")
                            logger.info(f"{'='*80}\n")

                            # Log to activity log
                            self._log_activity(
                                event_type='server_online',
                                server_key=server_key,
                                message=f"Server {status.host}:{status.port} is back online",
                                details={
                                    'host': status.host,
                                    'port': status.port,
                                    'protocol': status.protocol,
                                    'downtime_seconds': downtime,
                                    'downtime_formatted': downtime_str,
                                    'offline_since': status.offline_since.isoformat() if status.offline_since else None
                                }
                            )

                            # Log to audit system
                            if self.audit_manager:
                                from compliance_system import AuditEventType
                                self.audit_manager.log_event(
                                    AuditEventType.SERVER_ONLINE,
                                    f"Server {status.host}:{status.port} ({status.protocol}) back online after {downtime_str}",
                                    username="system",
                                    result="success",
                                    details={
                                        'host': status.host,
                                        'port': status.port,
                                        'protocol': status.protocol,
                                        'downtime_seconds': downtime
                                    }
                                )

                            status.is_online = True
                            status.last_online = now
                            status.offline_since = None

                    else:
                        status.consecutive_failures += 1
                        status.consecutive_successes = 0

                        # Server went offline (confirm with 2 consecutive failures)
                        if status.is_online and status.consecutive_failures >= 2:
                            logger.warning(f"\n{'='*80}")
                            logger.warning(f"⚠️  SERVER OFFLINE")
                            logger.warning(f"{'='*80}")
                            logger.warning(f"Server: {status.host}:{status.port}")
                            logger.warning(f"Protocol: {status.protocol}")
                            logger.warning(f"Last online: {status.last_online.strftime('%Y-%m-%d %H:%M:%S') if status.last_online else 'Unknown'}")
                            logger.warning(f"{'='*80}\n")

                            # Log to activity log
                            self._log_activity(
                                event_type='server_offline',
                                server_key=server_key,
                                message=f"Server {status.host}:{status.port} is OFFLINE",
                                details={
                                    'host': status.host,
                                    'port': status.port,
                                    'protocol': status.protocol,
                                    'last_online': status.last_online.isoformat() if status.last_online else None
                                }
                            )

                            # Log to audit system
                            if self.audit_manager:
                                from compliance_system import AuditEventType
                                self.audit_manager.log_event(
                                    AuditEventType.SERVER_OFFLINE,
                                    f"Server {status.host}:{status.port} ({status.protocol}) is OFFLINE",
                                    username="system",
                                    result="failure",
                                    details={
                                        'host': status.host,
                                        'port': status.port,
                                        'protocol': status.protocol
                                    }
                                )

                            status.is_online = False
                            status.last_offline = now
                            status.offline_since = now

                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"❌ Error in monitoring loop: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(self.check_interval)

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format"""
        if seconds < 60:
            return f"{int(seconds)} seconds"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{minutes} minutes, {secs} seconds"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours} hours, {minutes} minutes"
        else:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            return f"{days} days, {hours} hours"

    def start_monitoring(self):
        """Start the monitoring thread"""
        if not self.monitoring:
            self.monitoring = True
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()
            logger.info("✅ Server health monitoring started")

    def stop_monitoring(self):
        """Stop the monitoring thread"""
        if self.monitoring:
            self.monitoring = False
            if self.monitor_thread:
                self.monitor_thread.join(timeout=5)
            logger.info("🛑 Server health monitoring stopped")

    def get_server_status(self, host: str, port: int) -> Optional[ServerStatus]:
        """Get status for a specific server"""
        server_key = f"{host}:{port}"
        return self.servers.get(server_key)

    def get_all_statuses(self) -> Dict[str, Dict]:
        """Get status of all monitored servers"""
        statuses = {}
        for server_key, status in self.servers.items():
            statuses[server_key] = {
                'host': status.host,
                'port': status.port,
                'protocol': status.protocol,
                'is_online': status.is_online,
                'last_check': status.last_check.isoformat(),
                'last_online': status.last_online.isoformat() if status.last_online else None,
                'last_offline': status.last_offline.isoformat() if status.last_offline else None,
                'offline_since': status.offline_since.isoformat() if status.offline_since else None,
                'total_downtime': self._format_duration(status.total_downtime_seconds),
                'consecutive_failures': status.consecutive_failures,
                'consecutive_successes': status.consecutive_successes
            }
        return statuses

    def get_activity_log(self, limit: int = 100) -> List[Dict]:
        """Get recent activity log entries"""
        return self.activity_log[:limit]

    def clear_activity_log(self):
        """Clear the activity log"""
        self.activity_log.clear()
        logger.info("Activity log cleared")

    def sync_servers_from_rules(self, rules: Dict):
        """Sync monitored servers from transfer rules"""
        # Collect all unique servers from rules
        rule_servers = set()

        for rule in rules.values():
            if hasattr(rule, 'host') and hasattr(rule, 'port') and rule.host:
                server_key = f"{rule.host}:{rule.port}"
                rule_servers.add((rule.host, rule.port, rule.protocol))

        # Add new servers
        for host, port, protocol in rule_servers:
            self.add_server(host, port, protocol)

        # Remove servers that are no longer in rules
        current_servers = set(self.servers.keys())
        rule_server_keys = set(f"{host}:{port}" for host, port, protocol in rule_servers)

        for server_key in current_servers:
            if server_key not in rule_server_keys:
                host, port = server_key.split(':')
                self.remove_server(host, int(port))


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    monitor = ServerHealthMonitor(check_interval=10)
    monitor.add_server("10.10.100.4", 22, "sftp")
    monitor.add_server("192.168.1.100", 445, "smb")

    monitor.start_monitoring()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop_monitoring()
