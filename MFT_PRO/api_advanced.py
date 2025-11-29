"""
Advanced API Endpoints for MFT Application
Handles devices, transfer rules, domain integration, and audit logging
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import socket
import json

from database import (
    get_database, Device, DeviceDowntime, TransferRule,
    PendingDeletion, TransferQueue, DomainConfig, DomainUser,
    UserPermission, AuditLog, DeletionDelay
)

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/v1", tags=["Advanced"])


# Pydantic models for requests/responses
class DeviceRequest(BaseModel):
    name: str
    hostname: str
    ip_address: Optional[str] = None
    port: int = 22
    protocol: str = "sftp"
    username: Optional[str] = None
    password: Optional[str] = None
    private_key_path: Optional[str] = None


class DeviceResponse(BaseModel):
    id: int
    name: str
    hostname: str
    ip_address: Optional[str]
    port: int
    protocol: str
    status: str
    last_seen: Optional[str]
    created_at: Optional[str]


class TransferRuleRequest(BaseModel):
    name: str
    source_device_id: int
    source_path: str
    destination_device_id: int
    destination_path: str
    transfer_mode: str = "copy"  # copy or move
    deletion_delay: str = "immediate"
    file_pattern: str = "*"
    enabled: bool = True
    schedule_type: str = "on_demand"
    schedule_interval: Optional[int] = None
    cron_expression: Optional[str] = None
    retry_on_failure: bool = True
    max_retries: int = 3


class DomainConfigRequest(BaseModel):
    name: str = "default"
    domain_type: str = "active_directory"
    server: str
    port: int = 389
    use_ssl: bool = True
    base_dn: str = ""
    bind_user: str = ""
    bind_password: str = ""
    user_search_base: str = ""
    group_search_base: str = ""
    sync_interval_hours: int = 24
    enabled: bool = True


class UserPermissionRequest(BaseModel):
    domain_user_id: Optional[int] = None
    local_username: Optional[str] = None
    can_create_transfers: bool = False
    can_delete_transfers: bool = False
    can_manage_devices: bool = False
    can_manage_rules: bool = False
    can_view_audit: bool = False
    can_manage_users: bool = False
    can_manage_domain: bool = False
    is_admin: bool = False
    allowed_devices: List[int] = []


# Device endpoints
@router.get("/devices", response_model=List[DeviceResponse])
async def list_devices():
    """Get all devices"""
    db = get_database()
    devices = db.get_all_devices()
    return [DeviceResponse(**d.to_dict()) for d in devices]


@router.post("/devices", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(request: DeviceRequest):
    """Create a new device"""
    db = get_database()

    device = Device(
        name=request.name,
        hostname=request.hostname,
        ip_address=request.ip_address,
        port=request.port,
        protocol=request.protocol,
        username=request.username,
        password=request.password,
        private_key_path=request.private_key_path,
        status="unknown"
    )

    device_id = db.create_device(device)
    device.id = device_id

    return DeviceResponse(**device.to_dict())


@router.get("/devices/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: int):
    """Get device by ID"""
    db = get_database()
    device = db.get_device(device_id)

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    return DeviceResponse(**device.to_dict())


@router.post("/devices/{device_id}/check")
async def check_device_status(device_id: int):
    """Check device connectivity and update status"""
    db = get_database()
    device = db.get_device(device_id)

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Try to resolve hostname and check connectivity
    try:
        # Resolve hostname to IP
        ip_address = socket.gethostbyname(device.hostname)

        # Try to connect to port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((ip_address, device.port))
        sock.close()

        if result == 0:
            status = "online"
            db.update_device_status(device_id, status, ip_address)
            return {"status": "online", "ip_address": ip_address, "message": "Device is reachable"}
        else:
            status = "offline"
            db.update_device_status(device_id, status)
            return {"status": "offline", "message": f"Port {device.port} is not accessible"}

    except socket.gaierror:
        db.update_device_status(device_id, "offline")
        return {"status": "offline", "message": f"Cannot resolve hostname: {device.hostname}"}
    except Exception as e:
        db.update_device_status(device_id, "offline")
        return {"status": "offline", "message": str(e)}


@router.get("/devices/{device_id}/downtime")
async def get_device_downtime(device_id: int, days: int = 30):
    """Get device downtime history"""
    db = get_database()

    device = db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    downtime = db.get_device_downtime(device_id, days)

    total_downtime = sum(d.duration_seconds or 0 for d in downtime)

    return {
        "device_id": device_id,
        "device_name": device.name,
        "period_days": days,
        "total_downtime_seconds": total_downtime,
        "total_downtime_hours": round(total_downtime / 3600, 2),
        "incidents": [
            {
                "went_offline": d.went_offline,
                "came_online": d.came_online,
                "duration_seconds": d.duration_seconds,
                "reason": d.reason
            }
            for d in downtime
        ]
    }


# Transfer rule endpoints
@router.get("/rules")
async def list_transfer_rules(enabled_only: bool = False):
    """Get all transfer rules"""
    db = get_database()
    rules = db.get_transfer_rules(enabled_only)
    return [r.to_dict() for r in rules]


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_transfer_rule(request: TransferRuleRequest):
    """Create a new transfer rule"""
    db = get_database()

    # Validate devices exist
    source = db.get_device(request.source_device_id)
    dest = db.get_device(request.destination_device_id)

    if not source:
        raise HTTPException(status_code=400, detail="Source device not found")
    if not dest:
        raise HTTPException(status_code=400, detail="Destination device not found")

    # Validate deletion delay
    valid_delays = [d.value for d in DeletionDelay]
    if request.deletion_delay not in valid_delays:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid deletion_delay. Must be one of: {valid_delays}"
        )

    rule = TransferRule(
        name=request.name,
        source_device_id=request.source_device_id,
        source_path=request.source_path,
        destination_device_id=request.destination_device_id,
        destination_path=request.destination_path,
        transfer_mode=request.transfer_mode,
        deletion_delay=request.deletion_delay,
        file_pattern=request.file_pattern,
        enabled=request.enabled,
        schedule_type=request.schedule_type,
        schedule_interval=request.schedule_interval,
        cron_expression=request.cron_expression,
        retry_on_failure=request.retry_on_failure,
        max_retries=request.max_retries
    )

    rule_id = db.create_transfer_rule(rule)
    rule.id = rule_id

    return rule.to_dict()


@router.get("/rules/{rule_id}")
async def get_transfer_rule(rule_id: int):
    """Get transfer rule by ID"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM transfer_rules WHERE id = ?", (rule_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")

    return TransferRule(**dict(row)).to_dict()


@router.put("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: int):
    """Enable/disable a transfer rule"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT enabled FROM transfer_rules WHERE id = ?", (rule_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Rule not found")

    new_status = not row['enabled']
    cursor.execute(
        "UPDATE transfer_rules SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_status, rule_id)
    )
    conn.commit()
    conn.close()

    db.log_audit("system", "toggle", "transfer_rule", str(rule_id),
                {"enabled": new_status})

    return {"rule_id": rule_id, "enabled": new_status}


@router.post("/rules/{rule_id}/execute")
async def execute_rule_now(rule_id: int):
    """Execute a transfer rule immediately"""
    from transfer_scheduler import get_scheduler
    from api_server import mft_app
    
    if not mft_app:
        raise HTTPException(status_code=503, detail="MFT application not initialized")
    
    try:
        scheduler = get_scheduler(mft_app)
        await scheduler.execute_rule_now(rule_id)
        return {"message": f"Rule {rule_id} execution started", "rule_id": rule_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing rule: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Transfer queue endpoints
@router.get("/queue")
async def get_transfer_queue(rule_id: Optional[int] = None):
    """Get queued transfers"""
    db = get_database()
    queue = db.get_queued_transfers(rule_id)

    return [
        {
            "id": q.id,
            "transfer_rule_id": q.transfer_rule_id,
            "source_file": q.source_file,
            "file_size": q.file_size,
            "queued_at": q.queued_at,
            "retry_count": q.retry_count,
            "status": q.status
        }
        for q in queue
    ]


# Pending deletions endpoints
@router.get("/pending-deletions")
async def get_pending_deletions():
    """Get files pending deletion"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT pd.*, tr.name as rule_name
        FROM pending_deletions pd
        JOIN transfer_rules tr ON pd.transfer_rule_id = tr.id
        WHERE pd.status = 'pending'
        ORDER BY pd.scheduled_deletion
    """)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


@router.get("/deletions-due")
async def get_deletions_due():
    """Get files due for deletion now"""
    db = get_database()
    deletions = db.get_deletions_due()

    return [
        {
            "id": d.id,
            "file_path": d.file_path,
            "scheduled_deletion": d.scheduled_deletion,
            "checksum": d.checksum_sha256
        }
        for d in deletions
    ]


# Domain configuration endpoints
@router.get("/domain/configs")
async def list_domain_configs():
    """Get all domain configurations"""
    db = get_database()
    configs = db.get_all_domain_configs()
    return [c.to_dict() for c in configs]


@router.post("/domain/configs", status_code=status.HTTP_201_CREATED)
async def create_domain_config(request: DomainConfigRequest):
    """Create or update domain configuration"""
    db = get_database()

    config = DomainConfig(
        name=request.name,
        domain_type=request.domain_type,
        server=request.server,
        port=request.port,
        use_ssl=request.use_ssl,
        base_dn=request.base_dn,
        bind_user=request.bind_user,
        bind_password=request.bind_password,
        user_search_base=request.user_search_base,
        group_search_base=request.group_search_base,
        sync_interval_hours=request.sync_interval_hours,
        enabled=request.enabled
    )

    config_id = db.save_domain_config(config)
    config.id = config_id

    return config.to_dict()


@router.post("/domain/test")
async def test_domain_connection_direct(request: DomainConfigRequest):
    """Test domain connection without saving config"""
    try:
        import ldap3
        from ldap3 import Server, Connection, ALL

        server_url = f"ldap{'s' if request.use_ssl else ''}://{request.server}"
        
        server = Server(
            server_url,
            port=request.port,
            use_ssl=request.use_ssl,
            get_info=ALL,
            connect_timeout=10
        )

        conn = Connection(
            server,
            user=request.bind_user,
            password=request.bind_password,
            auto_bind=True,
            raise_exceptions=True
        )

        server_info = {
            "host": request.server,
            "port": request.port,
            "ssl": request.use_ssl,
            "connected": conn.bound,
            "server_type": str(server.info.vendor_name) if server.info else "Unknown"
        }

        conn.unbind()

        return {"status": "success", "message": "Connection successful", "info": server_info}

    except ImportError:
        return {
            "status": "error",
            "message": "ldap3 library not installed. Install with: pip install ldap3"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/domain/config")
async def save_domain_config_simple(request: DomainConfigRequest):
    """Save domain config (simplified endpoint)"""
    db = get_database()
    config = DomainConfig(
        name=request.name or "default",
        domain_type=request.domain_type,
        server=request.server,
        port=request.port,
        use_ssl=request.use_ssl,
        base_dn=request.base_dn,
        bind_user=request.bind_user,
        bind_password=request.bind_password,
        user_search_base=request.user_search_base
    )
    config_id = db.save_domain_config(config)
    return {"id": config_id, "message": "Configuration saved"}


@router.post("/domain/sync")
async def sync_domain_users_simple():
    """Sync users from the first configured domain - FIXED VERSION"""
    db = get_database()
    configs = db.get_all_domain_configs()

    if not configs:
        raise HTTPException(status_code=404, detail="No domain configured. Save a domain config first.")

    config = configs[0]  # Use first config

    try:
        import ldap3
        from ldap3 import Server, Connection, ALL, SUBTREE
        import json

        # Build server URL with protocol
        server_url = f"ldap{'s' if config.use_ssl else ''}://{config.server}"
        
        server = Server(
            server_url, 
            port=config.port, 
            use_ssl=config.use_ssl, 
            get_info=ALL,
            connect_timeout=10
        )
        
        # Try to bind with credentials
        try:
            conn = Connection(
                server, 
                user=config.bind_user, 
                password=config.bind_password, 
                auto_bind=True,
                raise_exceptions=True
            )
        except Exception as bind_error:
            logger.error(f"LDAP bind failed: {bind_error}")
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to connect to LDAP server: {str(bind_error)}"
            )

        search_base = config.user_search_base or config.base_dn
        
        # Try different search filters for compatibility
        search_filters = [
            '(&(objectClass=user)(objectCategory=person)(!(objectClass=computer)))',  # AD specific
            '(&(objectClass=person)(!(objectClass=computer)))',  # Generic person
            '(objectClass=inetOrgPerson)',  # OpenLDAP
            '(objectClass=user)'  # Simple user
        ]
        
        synced = 0
        entries_found = []
        
        for search_filter in search_filters:
            try:
                result = conn.search(
                    search_base=search_base,
                    search_filter=search_filter,
                    search_scope=SUBTREE,
                    attributes=['sAMAccountName', 'uid', 'cn', 'mail', 'memberOf', 
                               'userAccountControl', 'displayName', 'distinguishedName'],
                    paged_size=100
                )
                
                if result and conn.entries:
                    entries_found = conn.entries
                    logger.info(f"Found {len(entries_found)} entries with filter: {search_filter}")
                    break
                    
            except Exception as search_error:
                logger.warning(f"Search failed with filter {search_filter}: {search_error}")
                continue
        
        if not entries_found:
            conn.unbind()
            return {
                "synced": 0, 
                "message": "No users found. Check search base and bind credentials.",
                "search_base": search_base
            }

        for entry in entries_found:
            try:
                # Get username - try sAMAccountName (AD) then uid (OpenLDAP) then cn
                username = None
                if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName.value:
                    username = str(entry.sAMAccountName.value)
                elif hasattr(entry, 'uid') and entry.uid.value:
                    username = str(entry.uid.value)
                elif hasattr(entry, 'cn') and entry.cn.value:
                    username = str(entry.cn.value)
                
                if not username:
                    logger.warning(f"Skipping entry without username: {entry.entry_dn}")
                    continue
                
                # Get email
                email_val = ""
                if hasattr(entry, 'mail') and entry.mail.value:
                    email_val = str(entry.mail.value)
                
                # Get display name
                display_name = ""
                if hasattr(entry, 'displayName') and entry.displayName.value:
                    display_name = str(entry.displayName.value)
                elif hasattr(entry, 'cn') and entry.cn.value:
                    display_name = str(entry.cn.value)
                
                # Get groups - convert to JSON array
                groups = []
                if hasattr(entry, 'memberOf') and entry.memberOf.values:
                    for group_dn in entry.memberOf.values:
                        # Extract CN from DN
                        group_str = str(group_dn)
                        if 'CN=' in group_str:
                            cn_part = group_str.split(',')[0].replace('CN=', '')
                            groups.append(cn_part)
                
                groups_json = json.dumps(groups)
                
                # Get distinguished name
                dn = str(entry.entry_dn) if hasattr(entry, 'entry_dn') else ""
                
                # Check if enabled (for AD)
                is_enabled = True
                if hasattr(entry, 'userAccountControl') and entry.userAccountControl.value:
                    uac = int(entry.userAccountControl.value)
                    # Bit 2 indicates disabled account
                    is_enabled = not (uac & 2)
                
                from database import DomainUser
                user = DomainUser(
                    domain_config_id=config.id,
                    username=username,
                    email=email_val if email_val else None,
                    display_name=display_name if display_name else None,
                    distinguished_name=dn,
                    groups=groups_json,
                    enabled=is_enabled
                )
                
                db.save_domain_user(user)
                synced += 1
                
            except Exception as user_error:
                logger.error(f"Error processing user entry: {user_error}")
                continue

        conn.unbind()
        
        # Update last sync time
        conn_db = db._get_connection()
        cursor = conn_db.cursor()
        cursor.execute(
            "UPDATE domain_config SET last_sync = CURRENT_TIMESTAMP WHERE id = ?",
            (config.id,)
        )
        conn_db.commit()
        conn_db.close()
        
        return {
            "synced": synced, 
            "message": f"Successfully synced {synced} users from {config.server}",
            "search_base": search_base
        }

    except ImportError:
        raise HTTPException(
            status_code=500, 
            detail="ldap3 library not installed. Install with: pip install ldap3"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Domain sync error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Domain sync failed: {str(e)}")


@router.get("/users")
async def list_users():
    """List all domain users (simplified endpoint)"""
    db = get_database()
    users = db.get_domain_users()
    return [u.to_dict() for u in users]


@router.post("/domain/configs/{config_id}/test")
async def test_domain_connection(config_id: int):
    """Test domain connection"""
    db = get_database()
    config = db.get_domain_config(config_id)

    if not config:
        raise HTTPException(status_code=404, detail="Domain config not found")

    try:
        # Try LDAP connection
        import ldap3
        from ldap3 import Server, Connection, ALL

        server_url = f"ldap{'s' if config.use_ssl else ''}://{config.server}"
        
        server = Server(
            server_url,
            port=config.port,
            use_ssl=config.use_ssl,
            get_info=ALL,
            connect_timeout=10
        )

        conn = Connection(
            server,
            user=config.bind_user,
            password=config.bind_password,
            auto_bind=True,
            raise_exceptions=True
        )

        # Get server info
        server_info = {
            "host": config.server,
            "port": config.port,
            "ssl": config.use_ssl,
            "connected": conn.bound,
            "server_type": str(server.info.vendor_name) if server.info else "Unknown"
        }

        conn.unbind()

        db.log_audit("system", "test_connection", "domain_config", str(config_id),
                    {"success": True})

        return {"status": "success", "message": "Connection successful", "info": server_info}

    except ImportError:
        return {
            "status": "error",
            "message": "ldap3 library not installed. Install with: pip install ldap3"
        }
    except Exception as e:
        db.log_audit("system", "test_connection", "domain_config", str(config_id),
                    {"success": False, "error": str(e)}, status="failure")
        return {"status": "error", "message": str(e)}


@router.post("/domain/configs/{config_id}/sync")
async def sync_domain_users(config_id: int):
    """Sync users from domain"""
    db = get_database()
    config = db.get_domain_config(config_id)

    if not config:
        raise HTTPException(status_code=404, detail="Domain config not found")

    try:
        import ldap3
        from ldap3 import Server, Connection, ALL, SUBTREE

        server_url = f"ldap{'s' if config.use_ssl else ''}://{config.server}"
        
        server = Server(
            server_url,
            port=config.port,
            use_ssl=config.use_ssl,
            get_info=ALL,
            connect_timeout=10
        )

        conn = Connection(
            server,
            user=config.bind_user,
            password=config.bind_password,
            auto_bind=True,
            raise_exceptions=True
        )

        # Search for users
        search_base = config.user_search_base or config.base_dn
        conn.search(
            search_base=search_base,
            search_filter='(objectClass=user)',
            search_scope=SUBTREE,
            attributes=['sAMAccountName', 'mail', 'displayName', 'distinguishedName', 'memberOf', 'userAccountControl']
        )

        synced_count = 0
        for entry in conn.entries:
            # Check if account is enabled (bit 2 of userAccountControl)
            uac = int(entry.userAccountControl.value) if hasattr(entry, 'userAccountControl') else 0
            is_enabled = not (uac & 2)

            # Get groups
            groups = []
            if hasattr(entry, 'memberOf') and entry.memberOf:
                groups = [str(g).split(',')[0].replace('CN=', '') for g in entry.memberOf]

            user = DomainUser(
                domain_config_id=config_id,
                username=str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') else "",
                email=str(entry.mail) if hasattr(entry, 'mail') and entry.mail else None,
                display_name=str(entry.displayName) if hasattr(entry, 'displayName') else None,
                distinguished_name=str(entry.distinguishedName),
                groups=json.dumps(groups),
                enabled=is_enabled
            )

            db.save_domain_user(user)
            synced_count += 1

        conn.unbind()

        # Update last sync time
        conn_db = db._get_connection()
        cursor = conn_db.cursor()
        cursor.execute(
            "UPDATE domain_config SET last_sync = CURRENT_TIMESTAMP WHERE id = ?",
            (config_id,)
        )
        conn_db.commit()
        conn_db.close()

        db.log_audit("system", "sync_users", "domain_config", str(config_id),
                    {"synced_count": synced_count})

        return {"status": "success", "synced_users": synced_count}

    except ImportError:
        return {
            "status": "error",
            "message": "ldap3 library not installed. Install with: pip install ldap3"
        }
    except Exception as e:
        db.log_audit("system", "sync_users", "domain_config", str(config_id),
                    {"error": str(e)}, status="failure")
        return {"status": "error", "message": str(e)}


@router.get("/domain/users")
async def list_domain_users(config_id: Optional[int] = None):
    """Get domain users"""
    db = get_database()
    users = db.get_domain_users(config_id)
    return [u.to_dict() for u in users]


# User permissions endpoints
@router.get("/permissions")
async def list_permissions():
    """Get all user permissions"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT up.*, du.username as domain_username, du.display_name
        FROM user_permissions up
        LEFT JOIN domain_users du ON up.domain_user_id = du.id
        ORDER BY up.id
    """)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


@router.post("/permissions", status_code=status.HTTP_201_CREATED)
async def create_permission(request: UserPermissionRequest):
    """Create user permission"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO user_permissions (
            domain_user_id, local_username, can_create_transfers,
            can_delete_transfers, can_manage_devices, can_manage_rules,
            can_view_audit, can_manage_users, can_manage_domain,
            is_admin, allowed_devices
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        request.domain_user_id, request.local_username,
        request.can_create_transfers, request.can_delete_transfers,
        request.can_manage_devices, request.can_manage_rules,
        request.can_view_audit, request.can_manage_users,
        request.can_manage_domain, request.is_admin,
        json.dumps(request.allowed_devices)
    ))

    perm_id = cursor.lastrowid
    conn.commit()
    conn.close()

    db.log_audit("system", "create", "permission", str(perm_id),
                {"domain_user_id": request.domain_user_id})

    return {"id": perm_id, "message": "Permission created"}


# Audit log endpoints
@router.get("/audit")
async def get_audit_logs(
    limit: int = 100,
    username: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None
):
    """Get audit logs"""
    db = get_database()
    logs = db.get_audit_logs(
        limit=limit,
        username=username,
        action=action,
        resource_type=resource_type
    )

    return [l.to_dict() for l in logs]


@router.get("/audit/actions")
async def get_audit_actions():
    """Get list of audit action types"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")
    rows = cursor.fetchall()
    conn.close()

    return [row['action'] for row in rows]


@router.get("/audit/resource-types")
async def get_audit_resource_types():
    """Get list of audit resource types"""
    db = get_database()
    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT resource_type FROM audit_log ORDER BY resource_type")
    rows = cursor.fetchall()
    conn.close()

    return [row['resource_type'] for row in rows]


# Configuration endpoints
@router.get("/config/deletion-delays")
async def get_deletion_delays():
    """Get available deletion delay options"""
    return [
        {"value": d.value, "label": d.value.replace("_", " ").title()}
        for d in DeletionDelay
    ]


@router.get("/config/transfer-modes")
async def get_transfer_modes():
    """Get available transfer modes"""
    return [
        {"value": "copy", "label": "Copy (keep source file)"},
        {"value": "move", "label": "Move (delete source after transfer)"}
    ]


@router.get("/config/schedule-types")
async def get_schedule_types():
    """Get available schedule types"""
    return [
        {"value": "on_demand", "label": "On Demand (manual execution)"},
        {"value": "interval", "label": "Interval (execute every N seconds)"},
        {"value": "cron", "label": "Cron (schedule with cron expression)"}
    ]
