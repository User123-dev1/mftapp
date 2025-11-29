"""
SQLite Database Models for MFT Application
Handles devices, transfer rules, domain integration, and audit logging
Auto-initializes on first run
"""

import sqlite3
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from enum import Enum
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Database path
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mft_database.db")


class TransferMode(Enum):
    """Transfer operation mode"""
    COPY = "copy"
    MOVE = "move"


class DeviceStatus(Enum):
    """Device connection status"""
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class DeletionDelay(Enum):
    """Delayed deletion time ranges"""
    IMMEDIATE = "immediate"
    ONE_HOUR = "1_hour"
    TWELVE_HOURS = "12_hours"
    ONE_DAY = "1_day"
    ONE_WEEK = "1_week"
    ONE_MONTH = "1_month"
    SIX_MONTHS = "6_months"
    ONE_YEAR = "1_year"

    def to_timedelta(self) -> Optional[timedelta]:
        """Convert to timedelta"""
        mapping = {
            "immediate": timedelta(0),
            "1_hour": timedelta(hours=1),
            "12_hours": timedelta(hours=12),
            "1_day": timedelta(days=1),
            "1_week": timedelta(weeks=1),
            "1_month": timedelta(days=30),
            "6_months": timedelta(days=180),
            "1_year": timedelta(days=365)
        }
        return mapping.get(self.value)


@dataclass
class Device:
    """Device model for source/destination tracking"""
    id: Optional[int] = None
    name: str = ""
    hostname: str = ""  # Use hostname for DHCP support
    ip_address: Optional[str] = None
    port: int = 22
    protocol: str = "sftp"
    username: Optional[str] = None
    password: Optional[str] = None
    private_key_path: Optional[str] = None
    status: str = "unknown"
    last_seen: Optional[str] = None
    last_ip_change: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceDowntime:
    """Track device downtime periods"""
    id: Optional[int] = None
    device_id: int = 0
    went_offline: str = ""
    came_online: Optional[str] = None
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None


@dataclass
class TransferRule:
    """Transfer rule with COPY/MOVE and delayed deletion"""
    id: Optional[int] = None
    name: str = ""
    source_device_id: int = 0
    source_path: str = ""
    destination_device_id: int = 0
    destination_path: str = ""
    transfer_mode: str = "copy"  # copy or move
    deletion_delay: str = "immediate"
    file_pattern: str = "*"  # glob pattern for files to transfer
    enabled: bool = True
    schedule_type: str = "on_demand"
    schedule_interval: Optional[int] = None
    cron_expression: Optional[str] = None
    retry_on_failure: bool = True
    max_retries: int = 3
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PendingDeletion:
    """Files pending deletion after move"""
    id: Optional[int] = None
    transfer_rule_id: int = 0
    file_path: str = ""
    original_size: int = 0
    checksum_sha256: str = ""
    transferred_at: str = ""
    scheduled_deletion: str = ""
    deleted_at: Optional[str] = None
    status: str = "pending"  # pending, deleted, failed, cancelled


@dataclass
class TransferQueue:
    """Queue transfers when device is offline"""
    id: Optional[int] = None
    transfer_rule_id: int = 0
    source_file: str = ""
    file_size: int = 0
    queued_at: str = ""
    retry_count: int = 0
    last_attempt: Optional[str] = None
    status: str = "queued"  # queued, processing, completed, failed
    error_message: Optional[str] = None


@dataclass
class DomainConfig:
    """Domain/Active Directory configuration"""
    id: Optional[int] = None
    name: str = ""
    domain_type: str = "active_directory"  # active_directory, ldap, azure_ad
    server: str = ""
    port: int = 389
    use_ssl: bool = True
    base_dn: str = ""
    bind_user: str = ""
    bind_password: str = ""  # Should be encrypted in production
    user_search_base: str = ""
    group_search_base: str = ""
    last_sync: Optional[str] = None
    sync_interval_hours: int = 24
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        # Don't expose password in dict
        result['bind_password'] = '********' if self.bind_password else None
        return result


@dataclass
class DomainUser:
    """User pulled from domain"""
    id: Optional[int] = None
    domain_config_id: int = 0
    username: str = ""
    email: Optional[str] = None
    display_name: Optional[str] = None
    distinguished_name: str = ""
    groups: str = "[]"  # JSON array of group names
    enabled: bool = True
    last_sync: str = ""
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['groups'] = json.loads(self.groups) if self.groups else []
        return result


@dataclass
class UserPermission:
    """User permissions for MFT operations"""
    id: Optional[int] = None
    domain_user_id: Optional[int] = None
    local_username: Optional[str] = None  # For non-domain users
    can_create_transfers: bool = False
    can_delete_transfers: bool = False
    can_manage_devices: bool = False
    can_manage_rules: bool = False
    can_view_audit: bool = False
    can_manage_users: bool = False
    can_manage_domain: bool = False
    is_admin: bool = False
    allowed_devices: str = "[]"  # JSON array of device IDs
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['allowed_devices'] = json.loads(self.allowed_devices) if self.allowed_devices else []
        return result


@dataclass
class AuditLog:
    """Comprehensive audit trail"""
    id: Optional[int] = None
    timestamp: str = ""
    user_id: Optional[int] = None
    username: str = ""
    action: str = ""  # create, update, delete, login, logout, transfer, etc.
    resource_type: str = ""  # device, rule, user, transfer, domain, etc.
    resource_id: Optional[str] = None
    details: str = "{}"  # JSON with action details
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    status: str = "success"  # success, failure
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['details'] = json.loads(self.details) if self.details else {}
        return result


class Database:
    """Database manager with auto-initialization"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with row factory"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_database(self):
        """Create database and tables if they don't exist"""
        if not os.path.exists(self.db_path):
            logger.info(f"Creating new database at {self.db_path}")

        conn = self._get_connection()
        cursor = conn.cursor()

        # Create tables
        cursor.executescript("""
            -- Devices table
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                hostname TEXT NOT NULL,
                ip_address TEXT,
                port INTEGER DEFAULT 22,
                protocol TEXT DEFAULT 'sftp',
                username TEXT,
                password TEXT,
                private_key_path TEXT,
                status TEXT DEFAULT 'unknown',
                last_seen DATETIME,
                last_ip_change DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Device downtime tracking
            CREATE TABLE IF NOT EXISTS device_downtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                went_offline DATETIME NOT NULL,
                came_online DATETIME,
                duration_seconds INTEGER,
                reason TEXT,
                FOREIGN KEY (device_id) REFERENCES devices(id)
            );

            -- Transfer rules
            CREATE TABLE IF NOT EXISTS transfer_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_device_id INTEGER NOT NULL,
                source_path TEXT NOT NULL,
                destination_device_id INTEGER NOT NULL,
                destination_path TEXT NOT NULL,
                transfer_mode TEXT DEFAULT 'copy',
                deletion_delay TEXT DEFAULT 'immediate',
                file_pattern TEXT DEFAULT '*',
                enabled BOOLEAN DEFAULT 1,
                schedule_type TEXT DEFAULT 'on_demand',
                schedule_interval INTEGER,
                cron_expression TEXT,
                retry_on_failure BOOLEAN DEFAULT 1,
                max_retries INTEGER DEFAULT 3,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_device_id) REFERENCES devices(id),
                FOREIGN KEY (destination_device_id) REFERENCES devices(id)
            );

            -- Pending deletions for move operations
            CREATE TABLE IF NOT EXISTS pending_deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transfer_rule_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                original_size INTEGER,
                checksum_sha256 TEXT,
                transferred_at DATETIME NOT NULL,
                scheduled_deletion DATETIME NOT NULL,
                deleted_at DATETIME,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (transfer_rule_id) REFERENCES transfer_rules(id)
            );

            -- Transfer queue for offline devices
            CREATE TABLE IF NOT EXISTS transfer_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transfer_rule_id INTEGER NOT NULL,
                source_file TEXT NOT NULL,
                file_size INTEGER,
                queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                retry_count INTEGER DEFAULT 0,
                last_attempt DATETIME,
                status TEXT DEFAULT 'queued',
                error_message TEXT,
                FOREIGN KEY (transfer_rule_id) REFERENCES transfer_rules(id)
            );

            -- Domain configuration
            CREATE TABLE IF NOT EXISTS domain_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domain_type TEXT DEFAULT 'active_directory',
                server TEXT NOT NULL,
                port INTEGER DEFAULT 389,
                use_ssl BOOLEAN DEFAULT 1,
                base_dn TEXT,
                bind_user TEXT,
                bind_password TEXT,
                user_search_base TEXT,
                group_search_base TEXT,
                last_sync DATETIME,
                sync_interval_hours INTEGER DEFAULT 24,
                enabled BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Domain users
            CREATE TABLE IF NOT EXISTS domain_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_config_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                email TEXT,
                display_name TEXT,
                distinguished_name TEXT,
                groups TEXT DEFAULT '[]',
                enabled BOOLEAN DEFAULT 1,
                last_sync DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (domain_config_id) REFERENCES domain_config(id)
            );

            -- User permissions
            CREATE TABLE IF NOT EXISTS user_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_user_id INTEGER,
                local_username TEXT,
                can_create_transfers BOOLEAN DEFAULT 0,
                can_delete_transfers BOOLEAN DEFAULT 0,
                can_manage_devices BOOLEAN DEFAULT 0,
                can_manage_rules BOOLEAN DEFAULT 0,
                can_view_audit BOOLEAN DEFAULT 0,
                can_manage_users BOOLEAN DEFAULT 0,
                can_manage_domain BOOLEAN DEFAULT 0,
                is_admin BOOLEAN DEFAULT 0,
                allowed_devices TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (domain_user_id) REFERENCES domain_users(id)
            );

            -- Audit log
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                details TEXT DEFAULT '{}',
                ip_address TEXT,
                user_agent TEXT,
                status TEXT DEFAULT 'success',
                error_message TEXT
            );

            -- Create indexes for performance
            CREATE INDEX IF NOT EXISTS idx_devices_hostname ON devices(hostname);
            CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
            CREATE INDEX IF NOT EXISTS idx_device_downtime_device ON device_downtime(device_id);
            CREATE INDEX IF NOT EXISTS idx_transfer_rules_enabled ON transfer_rules(enabled);
            CREATE INDEX IF NOT EXISTS idx_pending_deletions_status ON pending_deletions(status);
            CREATE INDEX IF NOT EXISTS idx_pending_deletions_scheduled ON pending_deletions(scheduled_deletion);
            CREATE INDEX IF NOT EXISTS idx_transfer_queue_status ON transfer_queue(status);
            CREATE INDEX IF NOT EXISTS idx_domain_users_username ON domain_users(username);
            CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(username);
            CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
        """)

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")

    # Device operations
    def create_device(self, device: Device) -> int:
        """Create a new device"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO devices (name, hostname, ip_address, port, protocol,
                               username, password, private_key_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (device.name, device.hostname, device.ip_address, device.port,
              device.protocol, device.username, device.password,
              device.private_key_path, device.status))

        device_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self.log_audit("system", "create", "device", str(device_id),
                      {"name": device.name, "hostname": device.hostname})
        return device_id

    def get_device(self, device_id: int) -> Optional[Device]:
        """Get device by ID"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return Device(**dict(row))
        return None

    def get_all_devices(self) -> List[Device]:
        """Get all devices"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM devices ORDER BY name")
        rows = cursor.fetchall()
        conn.close()

        return [Device(**dict(row)) for row in rows]

    def update_device_status(self, device_id: int, status: str, ip_address: Optional[str] = None):
        """Update device status and optionally IP address"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get current status
        cursor.execute("SELECT status, ip_address FROM devices WHERE id = ?", (device_id,))
        row = cursor.fetchone()

        if row:
            old_status = row['status']
            old_ip = row['ip_address']

            # Update device
            if ip_address and ip_address != old_ip:
                cursor.execute("""
                    UPDATE devices SET status = ?, ip_address = ?,
                           last_seen = CURRENT_TIMESTAMP, last_ip_change = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, ip_address, device_id))

                self.log_audit("system", "ip_change", "device", str(device_id),
                              {"old_ip": old_ip, "new_ip": ip_address})
            else:
                cursor.execute("""
                    UPDATE devices SET status = ?, last_seen = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, device_id))

            # Track downtime
            if old_status == "online" and status == "offline":
                cursor.execute("""
                    INSERT INTO device_downtime (device_id, went_offline)
                    VALUES (?, CURRENT_TIMESTAMP)
                """, (device_id,))
            elif old_status == "offline" and status == "online":
                cursor.execute("""
                    UPDATE device_downtime
                    SET came_online = CURRENT_TIMESTAMP,
                        duration_seconds = CAST((julianday(CURRENT_TIMESTAMP) - julianday(went_offline)) * 86400 AS INTEGER)
                    WHERE device_id = ? AND came_online IS NULL
                """, (device_id,))

        conn.commit()
        conn.close()

    def get_device_downtime(self, device_id: int, days: int = 30) -> List[DeviceDowntime]:
        """Get device downtime history"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM device_downtime
            WHERE device_id = ? AND went_offline >= datetime('now', ?)
            ORDER BY went_offline DESC
        """, (device_id, f'-{days} days'))

        rows = cursor.fetchall()
        conn.close()

        return [DeviceDowntime(**dict(row)) for row in rows]

    # Transfer rule operations
    def create_transfer_rule(self, rule: TransferRule) -> int:
        """Create a transfer rule"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO transfer_rules (name, source_device_id, source_path,
                destination_device_id, destination_path, transfer_mode,
                deletion_delay, file_pattern, enabled, schedule_type,
                schedule_interval, cron_expression, retry_on_failure, max_retries)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (rule.name, rule.source_device_id, rule.source_path,
              rule.destination_device_id, rule.destination_path, rule.transfer_mode,
              rule.deletion_delay, rule.file_pattern, rule.enabled, rule.schedule_type,
              rule.schedule_interval, rule.cron_expression, rule.retry_on_failure,
              rule.max_retries))

        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self.log_audit("system", "create", "transfer_rule", str(rule_id),
                      {"name": rule.name, "mode": rule.transfer_mode})
        return rule_id

    def get_transfer_rules(self, enabled_only: bool = False) -> List[TransferRule]:
        """Get transfer rules"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if enabled_only:
            cursor.execute("SELECT * FROM transfer_rules WHERE enabled = 1 ORDER BY name")
        else:
            cursor.execute("SELECT * FROM transfer_rules ORDER BY name")

        rows = cursor.fetchall()
        conn.close()

        return [TransferRule(**dict(row)) for row in rows]

    # Pending deletion operations
    def add_pending_deletion(self, rule_id: int, file_path: str, size: int,
                            checksum: str, delay: DeletionDelay):
        """Add a file to pending deletion queue"""
        conn = self._get_connection()
        cursor = conn.cursor()

        delta = delay.to_timedelta()
        scheduled = datetime.utcnow() + delta if delta else datetime.utcnow()

        cursor.execute("""
            INSERT INTO pending_deletions (transfer_rule_id, file_path, original_size,
                checksum_sha256, transferred_at, scheduled_deletion)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        """, (rule_id, file_path, size, checksum, scheduled.isoformat()))

        conn.commit()
        conn.close()

    def get_deletions_due(self) -> List[PendingDeletion]:
        """Get files due for deletion"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM pending_deletions
            WHERE status = 'pending' AND scheduled_deletion <= CURRENT_TIMESTAMP
        """)

        rows = cursor.fetchall()
        conn.close()

        return [PendingDeletion(**dict(row)) for row in rows]

    def mark_deleted(self, deletion_id: int, success: bool, error: Optional[str] = None):
        """Mark a pending deletion as completed or failed"""
        conn = self._get_connection()
        cursor = conn.cursor()

        status = "deleted" if success else "failed"
        cursor.execute("""
            UPDATE pending_deletions
            SET status = ?, deleted_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, deletion_id))

        conn.commit()
        conn.close()

    # Transfer queue operations
    def queue_transfer(self, rule_id: int, source_file: str, file_size: int):
        """Queue a transfer for later processing"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO transfer_queue (transfer_rule_id, source_file, file_size)
            VALUES (?, ?, ?)
        """, (rule_id, source_file, file_size))

        conn.commit()
        conn.close()

    def get_queued_transfers(self, rule_id: Optional[int] = None) -> List[TransferQueue]:
        """Get queued transfers"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if rule_id:
            cursor.execute("""
                SELECT * FROM transfer_queue WHERE status = 'queued' AND transfer_rule_id = ?
                ORDER BY queued_at
            """, (rule_id,))
        else:
            cursor.execute("""
                SELECT * FROM transfer_queue WHERE status = 'queued'
                ORDER BY queued_at
            """)

        rows = cursor.fetchall()
        conn.close()

        return [TransferQueue(**dict(row)) for row in rows]

    # Domain operations
    def save_domain_config(self, config: DomainConfig) -> int:
        """Save domain configuration"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if config.id:
            cursor.execute("""
                UPDATE domain_config SET name = ?, domain_type = ?, server = ?,
                    port = ?, use_ssl = ?, base_dn = ?, bind_user = ?, bind_password = ?,
                    user_search_base = ?, group_search_base = ?, sync_interval_hours = ?,
                    enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (config.name, config.domain_type, config.server, config.port,
                  config.use_ssl, config.base_dn, config.bind_user, config.bind_password,
                  config.user_search_base, config.group_search_base,
                  config.sync_interval_hours, config.enabled, config.id))
            config_id = config.id
        else:
            cursor.execute("""
                INSERT INTO domain_config (name, domain_type, server, port, use_ssl,
                    base_dn, bind_user, bind_password, user_search_base,
                    group_search_base, sync_interval_hours, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (config.name, config.domain_type, config.server, config.port,
                  config.use_ssl, config.base_dn, config.bind_user, config.bind_password,
                  config.user_search_base, config.group_search_base,
                  config.sync_interval_hours, config.enabled))
            config_id = cursor.lastrowid

        conn.commit()
        conn.close()

        self.log_audit("system", "save", "domain_config", str(config_id),
                      {"name": config.name, "server": config.server})
        return config_id

    def get_domain_config(self, config_id: int) -> Optional[DomainConfig]:
        """Get domain configuration"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM domain_config WHERE id = ?", (config_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return DomainConfig(**dict(row))
        return None

    def get_all_domain_configs(self) -> List[DomainConfig]:
        """Get all domain configurations"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM domain_config ORDER BY name")
        rows = cursor.fetchall()
        conn.close()

        return [DomainConfig(**dict(row)) for row in rows]

    def save_domain_user(self, user: DomainUser) -> int:
        """Save or update domain user"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Check if user exists
        cursor.execute("""
            SELECT id FROM domain_users
            WHERE domain_config_id = ? AND username = ?
        """, (user.domain_config_id, user.username))

        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                UPDATE domain_users SET email = ?, display_name = ?,
                    distinguished_name = ?, groups = ?, enabled = ?,
                    last_sync = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (user.email, user.display_name, user.distinguished_name,
                  user.groups, user.enabled, existing['id']))
            user_id = existing['id']
        else:
            cursor.execute("""
                INSERT INTO domain_users (domain_config_id, username, email,
                    display_name, distinguished_name, groups, enabled, last_sync)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user.domain_config_id, user.username, user.email,
                  user.display_name, user.distinguished_name, user.groups, user.enabled))
            user_id = cursor.lastrowid

        conn.commit()
        conn.close()
        return user_id

    def get_domain_users(self, domain_config_id: Optional[int] = None) -> List[DomainUser]:
        """Get domain users"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if domain_config_id:
            cursor.execute("""
                SELECT * FROM domain_users WHERE domain_config_id = ?
                ORDER BY display_name, username
            """, (domain_config_id,))
        else:
            cursor.execute("SELECT * FROM domain_users ORDER BY display_name, username")

        rows = cursor.fetchall()
        conn.close()

        return [DomainUser(**dict(row)) for row in rows]

    # Audit logging
    def log_audit(self, username: str, action: str, resource_type: str,
                  resource_id: Optional[str] = None, details: Optional[Dict] = None,
                  ip_address: Optional[str] = None, status: str = "success",
                  error_message: Optional[str] = None):
        """Log an audit entry"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO audit_log (username, action, resource_type, resource_id,
                details, ip_address, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (username, action, resource_type, resource_id,
              json.dumps(details or {}), ip_address, status, error_message))

        conn.commit()
        conn.close()

    def get_audit_logs(self, limit: int = 100, username: Optional[str] = None,
                      action: Optional[str] = None, resource_type: Optional[str] = None,
                      start_date: Optional[datetime] = None,
                      end_date: Optional[datetime] = None) -> List[AuditLog]:
        """Get audit logs with filters"""
        conn = self._get_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []

        if username:
            query += " AND username = ?"
            params.append(username)
        if action:
            query += " AND action = ?"
            params.append(action)
        if resource_type:
            query += " AND resource_type = ?"
            params.append(resource_type)
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date.isoformat())
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [AuditLog(**dict(row)) for row in rows]


# Global database instance
_db_instance: Optional[Database] = None


def get_database() -> Database:
    """Get or create global database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


# Initialize database on module import
def init_database():
    """Initialize database (called automatically on first use)"""
    return get_database()
