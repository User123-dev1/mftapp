"""
Compliance & Audit System
HIPAA, GLBA, ISO-27001, SOC II, PCI DSS, GDPR, CFR Part 11
"""

import os
import json
import hashlib
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# COMPLIANCE FRAMEWORKS
# ============================================================================

class ComplianceFramework(Enum):
    """Supported compliance frameworks"""
    HIPAA = "hipaa"  # Health Insurance Portability and Accountability Act
    GLBA = "glba"  # Gramm-Leach-Bliley Act
    ISO_27001 = "iso_27001"  # Information Security Management
    SOC_TYPE_II = "soc_type_ii"  # Service Organization Control Type II
    PCI_DSS = "pci_dss"  # Payment Card Industry Data Security Standard
    GDPR = "gdpr"  # General Data Protection Regulation
    CFR_PART_11 = "cfr_part_11"  # FDA 21 CFR Part 11 (Electronic Records)


class EncryptionAlgorithm(Enum):
    """Supported encryption algorithms"""
    AES_256_GCM = "aes_256_gcm"
    AES_256_CBC = "aes_256_cbc"
    RSA_4096 = "rsa_4096"
    RSA_2048 = "rsa_2048"
    CHACHA20_POLY1305 = "chacha20_poly1305"


class AuditEventType(Enum):
    """Types of auditable events"""
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    USER_CREATED = "user_created"
    USER_MODIFIED = "user_modified"
    USER_DELETED = "user_deleted"
    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REVOKED = "permission_revoked"
    FILE_UPLOADED = "file_uploaded"
    FILE_DOWNLOADED = "file_downloaded"
    FILE_DELETED = "file_deleted"
    FILE_MODIFIED = "file_modified"
    TRANSFER_STARTED = "transfer_started"
    TRANSFER_COMPLETED = "transfer_completed"
    TRANSFER_FAILED = "transfer_failed"
    COMPLIANCE_ENABLED = "compliance_enabled"
    COMPLIANCE_DISABLED = "compliance_disabled"
    ENCRYPTION_ENABLED = "encryption_enabled"
    ENCRYPTION_DISABLED = "encryption_disabled"
    CONFIG_CHANGED = "config_changed"
    RULE_CREATED = "rule_created"
    RULE_MODIFIED = "rule_modified"
    RULE_DELETED = "rule_deleted"
    AD_SYNC_STARTED = "ad_sync_started"
    AD_SYNC_COMPLETED = "ad_sync_completed"
    AD_SYNC_FAILED = "ad_sync_failed"
    SYSTEM_STARTED = "system_started"
    SYSTEM_STOPPED = "system_stopped"
    SERVER_OFFLINE = "server_offline"
    SERVER_ONLINE = "server_online"


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class ComplianceConfig:
    """Configuration for compliance framework"""
    framework: ComplianceFramework
    enabled: bool = False
    encryption_required: bool = False
    encryption_algorithm: Optional[EncryptionAlgorithm] = None
    audit_retention_days: int = 2555  # 7 years for most compliance
    data_classification_required: bool = False
    access_logging_required: bool = True
    multi_factor_auth_required: bool = False
    data_encryption_at_rest: bool = False
    data_encryption_in_transit: bool = True
    automated_backup_required: bool = False
    disaster_recovery_plan: bool = False
    incident_response_plan: bool = False

    # Framework-specific settings
    hipaa_business_associate_agreement: bool = False  # HIPAA
    pci_dss_level: Optional[int] = None  # PCI DSS (1-4)
    gdpr_data_protection_officer: Optional[str] = None  # GDPR
    gdpr_lawful_basis: Optional[str] = None  # GDPR
    iso_27001_certification_date: Optional[str] = None  # ISO 27001
    soc_ii_report_date: Optional[str] = None  # SOC Type II
    cfr_part_11_validation: bool = False  # CFR Part 11


@dataclass
class AuditEvent:
    """Audit trail event"""
    event_id: str
    timestamp: datetime
    event_type: AuditEventType
    user_id: Optional[str]
    username: Optional[str]
    ip_address: Optional[str]
    action: str
    resource: Optional[str]
    resource_type: Optional[str]
    result: str  # success, failure, warning
    details: Dict
    signature: Optional[str] = None  # Digital signature for CFR Part 11

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'event_id': self.event_id,
            'timestamp': self.timestamp.isoformat(),
            'event_type': self.event_type.value,
            'user_id': self.user_id,
            'username': self.username,
            'ip_address': self.ip_address,
            'action': self.action,
            'resource': self.resource,
            'resource_type': self.resource_type,
            'result': self.result,
            'details': self.details,
            'signature': self.signature
        }


@dataclass
class ADUser:
    """Active Directory user"""
    user_id: str
    username: str
    email: Optional[str]
    display_name: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    department: Optional[str]
    title: Optional[str]
    manager: Optional[str]
    groups: List[str]
    enabled: bool = True
    last_logon: Optional[datetime] = None
    created_date: Optional[datetime] = None
    modified_date: Optional[datetime] = None

    # MFT permissions
    can_upload: bool = False
    can_download: bool = False
    can_delete: bool = False
    can_create_rules: bool = False
    can_edit_rules: bool = False
    can_manage_users: bool = False
    can_edit_permissions: bool = False
    can_export_users: bool = False
    can_view_audit_logs: bool = False
    is_admin: bool = False

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'user_id': self.user_id,
            'username': self.username,
            'email': self.email,
            'display_name': self.display_name,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'department': self.department,
            'title': self.title,
            'manager': self.manager,
            'groups': self.groups,
            'enabled': self.enabled,
            'last_logon': self.last_logon.isoformat() if self.last_logon else None,
            'created_date': self.created_date.isoformat() if self.created_date else None,
            'modified_date': self.modified_date.isoformat() if self.modified_date else None,
            'can_upload': self.can_upload,
            'can_download': self.can_download,
            'can_delete': self.can_delete,
            'can_create_rules': self.can_create_rules,
            'can_manage_users': self.can_manage_users,
            'can_view_audit_logs': self.can_view_audit_logs,
            'is_admin': self.is_admin
        }


# ============================================================================
# COMPLIANCE MANAGER
# ============================================================================

class ComplianceManager:
    """Manages compliance configurations"""

    def __init__(self, config_file: str = "compliance_config.json"):
        self.config_file = config_file
        self.frameworks: Dict[ComplianceFramework, ComplianceConfig] = {}
        self.load_config()

    def load_config(self):
        """Load compliance configuration"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                    for framework_name, config_data in data.items():
                        framework = ComplianceFramework(framework_name)
                        # Convert encryption algorithm if present
                        if config_data.get('encryption_algorithm'):
                            config_data['encryption_algorithm'] = EncryptionAlgorithm(
                                config_data['encryption_algorithm']
                            )
                        # Remove 'framework' from config_data to avoid duplicate
                        if 'framework' in config_data:
                            del config_data['framework']
                        self.frameworks[framework] = ComplianceConfig(
                            framework=framework,
                            **config_data
                        )
                logger.info(f"✅ Loaded compliance config: {len(self.frameworks)} frameworks")
            except Exception as e:
                logger.error(f"❌ Failed to load compliance config: {e}")
                self._initialize_defaults()
        else:
            self._initialize_defaults()

    def _initialize_defaults(self):
        """Initialize default compliance configurations"""
        # HIPAA
        self.frameworks[ComplianceFramework.HIPAA] = ComplianceConfig(
            framework=ComplianceFramework.HIPAA,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=2555,  # 7 years
            access_logging_required=True,
            data_encryption_at_rest=True,
            data_encryption_in_transit=True,
            hipaa_business_associate_agreement=False
        )

        # PCI DSS
        self.frameworks[ComplianceFramework.PCI_DSS] = ComplianceConfig(
            framework=ComplianceFramework.PCI_DSS,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=365,  # 1 year minimum
            access_logging_required=True,
            multi_factor_auth_required=True,
            data_encryption_at_rest=True,
            data_encryption_in_transit=True,
            pci_dss_level=4
        )

        # GDPR
        self.frameworks[ComplianceFramework.GDPR] = ComplianceConfig(
            framework=ComplianceFramework.GDPR,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=2555,  # 7 years
            data_classification_required=True,
            access_logging_required=True,
            data_encryption_at_rest=True,
            data_encryption_in_transit=True
        )

        # ISO 27001
        self.frameworks[ComplianceFramework.ISO_27001] = ComplianceConfig(
            framework=ComplianceFramework.ISO_27001,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=2555,
            access_logging_required=True,
            incident_response_plan=True,
            disaster_recovery_plan=True
        )

        # SOC Type II
        self.frameworks[ComplianceFramework.SOC_TYPE_II] = ComplianceConfig(
            framework=ComplianceFramework.SOC_TYPE_II,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=2555,
            access_logging_required=True,
            automated_backup_required=True
        )

        # GLBA
        self.frameworks[ComplianceFramework.GLBA] = ComplianceConfig(
            framework=ComplianceFramework.GLBA,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=2555,
            access_logging_required=True,
            data_encryption_at_rest=True,
            data_encryption_in_transit=True
        )

        # CFR Part 11
        self.frameworks[ComplianceFramework.CFR_PART_11] = ComplianceConfig(
            framework=ComplianceFramework.CFR_PART_11,
            enabled=False,
            encryption_required=True,
            encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
            audit_retention_days=2555,
            access_logging_required=True,
            data_classification_required=True,
            cfr_part_11_validation=True
        )

        self.save_config()

    def save_config(self):
        """Save compliance configuration"""
        try:
            data = {}
            for framework, config in self.frameworks.items():
                config_dict = asdict(config)
                # Convert enums to strings
                config_dict['framework'] = framework.value
                if config_dict.get('encryption_algorithm'):
                    config_dict['encryption_algorithm'] = config_dict['encryption_algorithm'].value
                data[framework.value] = config_dict

            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info("✅ Saved compliance configuration")
        except Exception as e:
            logger.error(f"❌ Failed to save compliance config: {e}")

    def enable_framework(self, framework: ComplianceFramework):
        """Enable compliance framework"""
        if framework in self.frameworks:
            self.frameworks[framework].enabled = True
            self.save_config()
            logger.info(f"✅ Enabled {framework.value}")

    def disable_framework(self, framework: ComplianceFramework):
        """Disable compliance framework"""
        if framework in self.frameworks:
            self.frameworks[framework].enabled = False
            self.save_config()
            logger.info(f"⚠️ Disabled {framework.value}")

    def update_framework_config(self, framework: ComplianceFramework, **kwargs):
        """Update framework configuration"""
        if framework in self.frameworks:
            config = self.frameworks[framework]
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            self.save_config()
            logger.info(f"✅ Updated {framework.value} configuration")

    def get_enabled_frameworks(self) -> List[ComplianceFramework]:
        """Get list of enabled frameworks"""
        return [fw for fw, config in self.frameworks.items() if config.enabled]

    def is_encryption_required(self) -> bool:
        """Check if any enabled framework requires encryption"""
        return any(
            config.encryption_required
            for config in self.frameworks.values()
            if config.enabled
        )

    def get_minimum_retention_days(self) -> int:
        """Get minimum audit retention days from enabled frameworks"""
        enabled = [
            config.audit_retention_days
            for config in self.frameworks.values()
            if config.enabled
        ]
        return max(enabled) if enabled else 365  # Default 1 year


# ============================================================================
# AUDIT MANAGER
# ============================================================================

class AuditManager:
    """Manages audit trail"""

    def __init__(self, audit_file: str = "audit_trail.jsonl"):
        self.audit_file = audit_file
        self.events: List[AuditEvent] = []
        self.load_recent_events()

    def load_recent_events(self, days: int = 30):
        """Load recent audit events"""
        if os.path.exists(self.audit_file):
            try:
                with open(self.audit_file, 'r') as f:
                    for line in f:
                        try:
                            data = json.loads(line)
                            # Parse timestamp
                            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
                            # Parse event type
                            data['event_type'] = AuditEventType(data['event_type'])

                            event = AuditEvent(**data)
                            self.events.append(event)
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to parse audit event: {e}")

                # Keep only recent events in memory
                from datetime import timedelta
                cutoff = datetime.now() - timedelta(days=days)
                self.events = [e for e in self.events if e.timestamp > cutoff]

                logger.info(f"✅ Loaded {len(self.events)} recent audit events")
            except Exception as e:
                logger.error(f"❌ Failed to load audit trail: {e}")

    def log_event(
        self,
        event_type: AuditEventType,
        action: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        resource: Optional[str] = None,
        resource_type: Optional[str] = None,
        result: str = "success",
        details: Optional[Dict] = None,
        sign_event: bool = False
    ):
        """Log an audit event"""
        import uuid

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            event_type=event_type,
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            action=action,
            resource=resource,
            resource_type=resource_type,
            result=result,
            details=details or {}
        )

        # Sign event for CFR Part 11 compliance
        if sign_event:
            event.signature = self._sign_event(event)

        # Add to in-memory list
        self.events.append(event)

        # Write to file
        self._write_event(event)

        logger.info(f"📝 Audit: {event_type.value} - {action} - {result}")

    def _sign_event(self, event: AuditEvent) -> str:
        """Create digital signature for event (CFR Part 11)"""
        # Create hash of event data
        event_string = f"{event.event_id}{event.timestamp.isoformat()}{event.event_type.value}{event.action}{event.result}"
        signature = hashlib.sha256(event_string.encode()).hexdigest()
        return signature

    def _write_event(self, event: AuditEvent):
        """Write event to audit file"""
        try:
            with open(self.audit_file, 'a') as f:
                f.write(json.dumps(event.to_dict()) + '\n')
        except Exception as e:
            logger.error(f"❌ Failed to write audit event: {e}")

    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        event_type: Optional[AuditEventType] = None,
        user_id: Optional[str] = None,
        result: Optional[str] = None,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Query audit events"""
        filtered = self.events

        if start_date:
            filtered = [e for e in filtered if e.timestamp >= start_date]

        if end_date:
            filtered = [e for e in filtered if e.timestamp <= end_date]

        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]

        if user_id:
            filtered = [e for e in filtered if e.user_id == user_id]

        if result:
            filtered = [e for e in filtered if e.result == result]

        # Sort by timestamp descending (newest first)
        filtered.sort(key=lambda e: e.timestamp, reverse=True)

        return filtered[:limit]

    def get_statistics(self) -> Dict:
        """Get audit statistics"""
        total_events = len(self.events)

        # Count by event type
        by_type = {}
        for event in self.events:
            event_type = event.event_type.value
            by_type[event_type] = by_type.get(event_type, 0) + 1

        # Count by result
        by_result = {}
        for event in self.events:
            by_result[event.result] = by_result.get(event.result, 0) + 1

        # Count by user
        by_user = {}
        for event in self.events:
            if event.username:
                by_user[event.username] = by_user.get(event.username, 0) + 1

        return {
            'total_events': total_events,
            'by_type': by_type,
            'by_result': by_result,
            'by_user': by_user,
            'oldest_event': self.events[-1].timestamp.isoformat() if self.events else None,
            'newest_event': self.events[0].timestamp.isoformat() if self.events else None
        }


# ============================================================================
# ACTIVE DIRECTORY MANAGER
# ============================================================================

class ActiveDirectoryManager:
    """Manages Active Directory integration"""

    def __init__(self, users_file: str = "ad_users.json"):
        self.users_file = users_file
        self.users: Dict[str, ADUser] = {}
        self.last_sync: Optional[datetime] = None
        self.load_users()

    def load_users(self):
        """Load users from file"""
        if os.path.exists(self.users_file):
            try:
                with open(self.users_file, 'r') as f:
                    data = json.load(f)
                    self.last_sync = datetime.fromisoformat(data['last_sync']) if data.get('last_sync') else None

                    for user_data in data.get('users', []):
                        # Parse dates
                        if user_data.get('last_logon'):
                            user_data['last_logon'] = datetime.fromisoformat(user_data['last_logon'])
                        if user_data.get('created_date'):
                            user_data['created_date'] = datetime.fromisoformat(user_data['created_date'])
                        if user_data.get('modified_date'):
                            user_data['modified_date'] = datetime.fromisoformat(user_data['modified_date'])

                        user = ADUser(**user_data)
                        self.users[user.user_id] = user

                logger.info(f"✅ Loaded {len(self.users)} users")
            except Exception as e:
                logger.error(f"❌ Failed to load users: {e}")

    def save_users(self):
        """Save users to file"""
        try:
            data = {
                'last_sync': self.last_sync.isoformat() if self.last_sync else None,
                'users': [user.to_dict() for user in self.users.values()]
            }

            with open(self.users_file, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info("✅ Saved user data")
        except Exception as e:
            logger.error(f"❌ Failed to save users: {e}")

    def sync_from_ad(self, ad_config: Dict) -> Dict:
        """Sync users from Active Directory - REAL IMPLEMENTATION"""
        try:
            from ldap3 import Server, Connection, ALL, SUBTREE, NTLM
            from datetime import datetime, timedelta

            logger.info("🔄 Starting REAL AD sync...")

            # Validate config
            if not ad_config.get('server'):
                raise ValueError("AD server not configured")

            # Build LDAP server URL
            server_url = ad_config.get('server')
            if not server_url.startswith('ldap://') and not server_url.startswith('ldaps://'):
                server_url = f"ldap://{server_url}"

            port = ad_config.get('port', 389)
            use_ssl = ad_config.get('use_ssl', False)

            logger.info(f"📡 Connecting to: {server_url}:{port}")
            logger.info(f"🔐 SSL: {use_ssl}")

            # Create server object
            server = Server(
                server_url,
                port=port,
                use_ssl=use_ssl,
                get_info=ALL
            )

            # Get credentials
            bind_dn = ad_config.get('bind_dn')
            bind_password = ad_config.get('bind_password')

            logger.info(f"👤 Binding as: {bind_dn}")
            logger.info(f"🔑 Password provided: {'Yes' if bind_password else 'No'}")

            if not bind_dn or not bind_password:
                raise ValueError("Bind DN and password are required for AD authentication")

            # TRY NTLM Authentication (Best for Windows AD)
            logger.info("🔐 Attempting NTLM authentication...")

            # For NTLM, use format: DOMAIN\Username
            if bind_dn.startswith('CN='):
                username = bind_dn.split(',')[0].replace('CN=', '')
                domain = 'scepces'
                ntlm_user = f"{domain}\\{username}"
            else:
                ntlm_user = bind_dn

            logger.info(f"   NTLM format: {ntlm_user}")

            conn = Connection(
                server,
                user=ntlm_user,
                password=bind_password,
                authentication=NTLM,
                auto_bind=False
            )

            if not conn.bind():
                raise Exception(f"Authentication failed: {conn.result}")

            logger.info("✅ Successfully authenticated to AD")

            # Search for users
            base_dn = ad_config.get('base_dn')
            user_filter = ad_config.get('user_filter', '(&(objectClass=user)(!(objectClass=computer)))')

            logger.info(f"🔍 Searching in: {base_dn}")
            logger.info(f"🔍 Filter: {user_filter}")

            # Define attributes to retrieve
            attributes = [
                'sAMAccountName',
                'userPrincipalName',
                'mail',
                'displayName',
                'givenName',
                'sn',
                'department',
                'title',
                'manager',
                'memberOf',
                'userAccountControl',
                'lastLogon',
                'lastLogonTimestamp',
                'whenCreated',
                'whenChanged',
                'distinguishedName'
            ]

            # Perform search
            success = conn.search(
                search_base=base_dn,
                search_filter=user_filter,
                search_scope=SUBTREE,
                attributes=attributes
            )

            if not success:
                logger.error(f"❌ Search failed: {conn.result}")
                raise Exception(f"Search failed: {conn.result}")

            logger.info(f"📊 Found {len(conn.entries)} entries")

            synced_count = 0

            for entry in conn.entries:
                try:
                    # Helper function to safely extract attribute value
                    def get_attr_value(attr, default=None):
                        """Safely extract attribute value"""
                        if not attr:
                            return default
                        try:
                            # Handle single value
                            if hasattr(attr, 'value'):
                                val = attr.value
                                # If it's a list with one item, return the item
                                if isinstance(val, list) and len(val) == 1:
                                    return str(val[0]) if val[0] else default
                                # If it's a list, return it as-is
                                elif isinstance(val, list):
                                    return val
                                # Otherwise return the value
                                else:
                                    return str(val) if val else default
                            else:
                                return str(attr) if attr else default
                        except:
                            return default

                    # Extract username (REQUIRED)
                    username = get_attr_value(entry.sAMAccountName)

                    if not username:
                        logger.warning(f"⚠️ Skipping entry without username: {entry.entry_dn}")
                        continue

                    logger.info(f"  Processing user: {username}")

                    # Parse userAccountControl (handle as integer)
                    uac = 0
                    if entry.userAccountControl:
                        try:
                            uac_value = get_attr_value(entry.userAccountControl, '0')
                            uac = int(uac_value)
                        except (ValueError, TypeError) as e:
                            logger.warning(f"    ⚠️ Could not parse userAccountControl: {e}")
                            uac = 0

                    # Check if account is disabled (bit 2 = 0x0002)
                    account_disabled = bool(uac & 0x0002)

                    # Parse groups (memberOf is a list)
                    groups = []
                    if entry.memberOf:
                        member_of_list = get_attr_value(entry.memberOf, [])
                        if not isinstance(member_of_list, list):
                            member_of_list = [member_of_list]

                        for group_dn in member_of_list:
                            # Extract CN from DN (e.g., "CN=Domain Admins,CN=Users,DC=...")
                            if group_dn:
                                cn_parts = str(group_dn).split(',')
                                for part in cn_parts:
                                    if part.strip().startswith('CN='):
                                        groups.append(part.strip()[3:])
                                        break

                    # Parse lastLogon (Windows FILETIME - 64-bit integer)
                    last_logon = None
                    if entry.lastLogon or entry.lastLogonTimestamp:
                        try:
                            # Prefer lastLogon, fall back to lastLogonTimestamp
                            logon_time = entry.lastLogon if entry.lastLogon else entry.lastLogonTimestamp
                            filetime_str = get_attr_value(logon_time, '0')
                            filetime = int(filetime_str)

                            # Convert Windows FILETIME to datetime
                            # FILETIME = 100-nanosecond intervals since 1601-01-01
                            if filetime > 0:
                                last_logon = datetime(1601, 1, 1) + timedelta(microseconds=filetime / 10)
                        except Exception as e:
                            logger.debug(f"    Could not parse lastLogon for {username}: {e}")

                    # Parse dates (these are already datetime objects in ldap3)
                    created_date = get_attr_value(entry.whenCreated)
                    modified_date = get_attr_value(entry.whenChanged)

                    # Convert to datetime if they're strings
                    if isinstance(created_date, str):
                        try:
                            created_date = datetime.fromisoformat(created_date.replace('Z', '+00:00'))
                        except:
                            created_date = None

                    if isinstance(modified_date, str):
                        try:
                            modified_date = datetime.fromisoformat(modified_date.replace('Z', '+00:00'))
                        except:
                            modified_date = None

                    # Extract other fields
                    email = get_attr_value(entry.mail)
                    display_name = get_attr_value(entry.displayName)
                    first_name = get_attr_value(entry.givenName)
                    last_name = get_attr_value(entry.sn)
                    department = get_attr_value(entry.department)
                    title = get_attr_value(entry.title)
                    manager = get_attr_value(entry.manager)

                    # Determine if user is admin based on groups
                    is_admin = any(g in ['Domain Admins', 'Administrators', 'Enterprise Admins'] for g in groups)

                    # Create ADUser object
                    user = ADUser(
                        user_id=username.lower(),
                        username=username,
                        email=email,
                        display_name=display_name,
                        first_name=first_name,
                        last_name=last_name,
                        department=department,
                        title=title,
                        manager=manager,
                        groups=groups,
                        enabled=not account_disabled,
                        last_logon=last_logon,
                        created_date=created_date,
                        modified_date=modified_date,
                        # Default permissions
                        can_upload=True,
                        can_download=True,
                        can_delete=False,
                        can_create_rules=False,
                        can_manage_users=False,
                        can_view_audit_logs=False,
                        is_admin=is_admin
                    )

                    # Apply admin permissions
                    if user.is_admin:
                        user.can_upload = True
                        user.can_download = True
                        user.can_delete = True
                        user.can_create_rules = True
                        user.can_manage_users = True
                        user.can_view_audit_logs = True

                    # Store user
                    self.users[user.user_id] = user
                    synced_count += 1

                    logger.info(f"  ✅ Synced: {username} ({display_name or 'N/A'}) - Groups: {len(groups)}")

                except Exception as e:
                    logger.warning(f"  ⚠️ Error processing user: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            # Close connection
            conn.unbind()

            # Update sync timestamp
            self.last_sync = datetime.now()
            self.save_users()

            logger.info(f"✅ AD sync completed: {synced_count} users synced")

            return {
                'success': True,
                'users_synced': synced_count,
                'timestamp': self.last_sync.isoformat()
            }

        except ImportError:
            logger.error("❌ ldap3 library not installed. Install with: pip install ldap3")
            return {
                'success': False,
                'error': 'ldap3 library not installed'
            }
        except Exception as e:
            logger.error(f"❌ AD sync failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e)
            }

    def get_user(self, user_id: str) -> Optional[ADUser]:
        """Get user by ID"""
        return self.users.get(user_id)

    def get_user_by_username(self, username: str) -> Optional[ADUser]:
        """Get user by username"""
        for user in self.users.values():
            if user.username == username:
                return user
        return None

    def update_user_permissions(
            self,
            user_id: str,
            can_upload: Optional[bool] = None,
            can_download: Optional[bool] = None,
            can_delete: Optional[bool] = None,
            can_create_rules: Optional[bool] = None,
            can_edit_rules: Optional[bool] = None,
            can_manage_users: Optional[bool] = None,
            can_edit_permissions: Optional[bool] = None,
            can_export_users: Optional[bool] = None,
            can_view_audit_logs: Optional[bool] = None,
            is_admin: Optional[bool] = None
    ):
        """Update user permissions"""
        if user_id in self.users:
            user = self.users[user_id]

            if can_upload is not None:
                user.can_upload = can_upload
            if can_download is not None:
                user.can_download = can_download
            if can_delete is not None:
                user.can_delete = can_delete
            if can_create_rules is not None:
                user.can_create_rules = can_create_rules
            if can_edit_rules is not None:
                user.can_edit_rules = can_edit_rules
            if can_manage_users is not None:
                user.can_manage_users = can_manage_users
            if can_edit_permissions is not None:
                user.can_edit_permissions = can_edit_permissions
            if can_export_users is not None:
                user.can_export_users = can_export_users
            if can_view_audit_logs is not None:
                user.can_view_audit_logs = can_view_audit_logs
            if is_admin is not None:
                user.is_admin = is_admin

            self.save_users()
            logger.info(f"✅ Updated permissions for {user.username}")

    def search_users(
            self,
            query: Optional[str] = None,
            department: Optional[str] = None,
            enabled: Optional[bool] = None
    ) -> List[ADUser]:
        """Search users"""
        results = list(self.users.values())

        if query:
            query = query.lower()
            results = [
                u for u in results
                if query in u.username.lower()
                   or (u.display_name and query in u.display_name.lower())
                   or (u.email and query in u.email.lower())
            ]

        if department:
            results = [u for u in results if u.department == department]

        if enabled is not None:
            results = [u for u in results if u.enabled == enabled]

        return results


if __name__ == "__main__":
    # Test compliance manager
    cm = ComplianceManager()
    print("Compliance frameworks loaded:")
    for fw in cm.frameworks:
        print(f"  - {fw.value}: {'Enabled' if cm.frameworks[fw].enabled else 'Disabled'}")

    # Test audit manager
    am = AuditManager()
    am.log_event(
        AuditEventType.SYSTEM_STARTED,
        "System started",
        username="system",
        result="success"
    )

    print(f"\nAudit events: {len(am.events)}")

    # Test AD manager
    ad = ActiveDirectoryManager()
    ad.sync_from_ad({})
    print(f"\nAD users: {len(ad.users)}")