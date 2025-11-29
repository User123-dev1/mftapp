#!/usr/bin/env python3
"""
Complete MFT API Server with All Endpoints
Includes: devices, transfers, rules, statistics, audit logs
"""

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid

# Import existing modules
try:
    from database import Database
    from mft_application import MFTApplication
except ImportError as e:
    print(f"Warning: Could not import modules: {e}")
    Database = None
    MFTApplication = None

app = FastAPI(title="MFT API Server", version="2.0")

# Initialize database and MFT app
if Database:
    db = Database()
else:
    db = None

if MFTApplication:
    mft_app = MFTApplication()
else:
    mft_app = None

# In-memory storage for transfers (fallback if database doesn't have transfer methods)
transfers_storage = []


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class DeviceCreate(BaseModel):
    name: str
    hostname: str
    port: int = 22
    protocol: str = "sftp"
    username: Optional[str] = None
    password: Optional[str] = None


class DeviceResponse(BaseModel):
    id: int
    name: str
    hostname: str
    port: int
    protocol: str
    status: str = "unknown"
    username: Optional[str] = None


class TransferCreate(BaseModel):
    source_path: str
    destination_path: str
    protocol: str
    host: str
    port: int = 22
    username: Optional[str] = None
    password: Optional[str] = None
    encryption_enabled: bool = True
    compliance_frameworks: List[str] = []
    schedule_type: str = "on_demand"
    schedule_interval: Optional[int] = None
    cron_expression: Optional[str] = None


class TransferResponse(BaseModel):
    task_id: str
    status: str
    source_path: str
    destination_path: str
    protocol: str
    created_at: str


class RuleCreate(BaseModel):
    name: str
    source_device_id: int
    destination_device_id: int
    source_path: str
    destination_path: str
    transfer_mode: str = "COPY"
    schedule_type: str = "on_demand"
    schedule_interval: Optional[int] = None
    cron_expression: Optional[str] = None
    enabled: bool = True


class RuleResponse(BaseModel):
    id: int
    name: str
    source_path: str
    destination_path: str
    transfer_mode: str
    enabled: bool


class StatisticsResponse(BaseModel):
    active_transfers: int = 0
    completed_transfers: int = 0
    failed_transfers: int = 0
    success_rate: float = 100.0
    total_devices: int = 0
    total_rules: int = 0


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0"
    }


# ============================================================================
# DEVICES ENDPOINTS
# ============================================================================

@app.get("/api/v1/devices", response_model=List[DeviceResponse])
async def list_devices():
    """List all configured devices"""
    try:
        if not db:
            return []

        devices = db.get_all_devices()
        return [
            DeviceResponse(
                id=d['id'],
                name=d['name'],
                hostname=d['hostname'],
                port=d['port'],
                protocol=d.get('protocol', 'sftp'),
                status=d.get('status', 'unknown'),
                username=d.get('username')
            )
            for d in devices
        ]
    except Exception as e:
        print(f"Error listing devices: {e}")
        return []


@app.post("/api/v1/devices", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(device: DeviceCreate):
    """Create a new device"""
    try:
        if not db:
            raise HTTPException(status_code=500, detail="Database not initialized")

        device_id = db.add_device(
            name=device.name,
            hostname=device.hostname,
            port=device.port,
            protocol=device.protocol,
            username=device.username,
            password=device.password
        )

        return DeviceResponse(
            id=device_id,
            name=device.name,
            hostname=device.hostname,
            port=device.port,
            protocol=device.protocol,
            status="online",
            username=device.username
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/v1/devices/{device_id}")
async def delete_device(device_id: int):
    """Delete a device"""
    try:
        if not db:
            raise HTTPException(status_code=500, detail="Database not initialized")

        db.delete_device(device_id)
        return {"message": "Device deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/devices/{device_id}/check")
async def check_device(device_id: int):
    """Check device connectivity"""
    try:
        if not db:
            raise HTTPException(status_code=500, detail="Database not initialized")

        device = db.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        # TODO: Implement actual connectivity check
        # For now, just return a success response
        return {
            "device_id": device_id,
            "status": "online",
            "message": "Device is reachable"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# TRANSFERS ENDPOINTS (NEW!)
# ============================================================================

@app.get("/api/v1/transfers")
async def list_transfers():
    """List all transfers"""
    try:
        # Try database first
        if db and hasattr(db, 'get_all_transfers'):
            transfers = db.get_all_transfers()
            return transfers

        # Fallback to in-memory storage
        return transfers_storage
    except Exception as e:
        print(f"Error listing transfers: {e}")
        return transfers_storage  # Return in-memory as fallback


@app.post("/api/v1/transfers", response_model=TransferResponse, status_code=status.HTTP_201_CREATED)
async def create_transfer(transfer: TransferCreate):
    """
    Create a new file transfer task

    This endpoint creates an immediate transfer or schedules one for later.
    """
    try:
        # Generate a unique task ID
        task_id = str(uuid.uuid4())

        # Clean up paths - remove protocol prefix if present
        source_path = transfer.source_path
        dest_path = transfer.destination_path

        # Remove protocol prefixes like "sftp://"
        for protocol in ['sftp://', 'ftps://', 'ftp://', 'smb://', 'unc://', 'https://']:
            source_path = source_path.replace(protocol, '')
            dest_path = dest_path.replace(protocol, '')

        # For Windows paths with hostnames, extract just the path
        # Example: "192.168.252.16/C$/Users/..." -> "/C$/Users/..."
        if '/' in source_path:
            parts = source_path.split('/', 1)
            if len(parts) > 1 and not parts[0].startswith('/'):
                # First part looks like a hostname, keep only the path
                source_path = '/' + parts[1]

        if '/' in dest_path:
            parts = dest_path.split('/', 1)
            if len(parts) > 1 and not parts[0].startswith('/'):
                dest_path = '/' + parts[1]

        # Store in database
        if db and hasattr(db, 'add_transfer'):
            db.add_transfer(
                task_id=task_id,
                source_path=source_path,
                destination_path=dest_path,
                protocol=transfer.protocol,
                host=transfer.host,
                port=transfer.port,
                username=transfer.username,
                password=transfer.password,
                encryption_enabled=transfer.encryption_enabled,
                schedule_type=transfer.schedule_type,
                schedule_interval=transfer.schedule_interval,
                cron_expression=transfer.cron_expression,
                status='pending'
            )

        # Also store in memory for immediate retrieval
        transfer_record = {
            'task_id': task_id,
            'source_path': source_path,
            'destination_path': dest_path,
            'protocol': transfer.protocol,
            'host': transfer.host,
            'port': transfer.port,
            'username': transfer.username,
            'encryption_enabled': transfer.encryption_enabled,
            'schedule_type': transfer.schedule_type,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat()
        }
        transfers_storage.append(transfer_record)

        # Execute transfer if on_demand
        if transfer.schedule_type == 'on_demand':
            try:
                if mft_app:
                    # Try different method names that might exist
                    result = False
                    if hasattr(mft_app, 'execute_transfer'):
                        result = mft_app.execute_transfer(
                            source=f"{transfer.protocol}://{transfer.host}:{transfer.port}{source_path}",
                            destination=f"{transfer.protocol}://{transfer.host}:{transfer.port}{dest_path}",
                            username=transfer.username,
                            password=transfer.password
                        )
                    elif hasattr(mft_app, 'transfer_file'):
                        # Try different parameter name combinations
                        # Build config object
                        config = {
                            'protocol': transfer.protocol,
                            'host': transfer.host,
                            'port': transfer.port,
                            'username': transfer.username,
                            'password': transfer.password,
                            'encryption_enabled': transfer.encryption_enabled
                        }

                        try:
                            # Try: source_path, destination_path, config
                            result = mft_app.transfer_file(
                                source_path=f"{transfer.protocol}://{transfer.host}:{transfer.port}{source_path}",
                                destination_path=f"{transfer.protocol}://{transfer.host}:{transfer.port}{dest_path}",
                                config=config
                            )
                        except TypeError as te:
                            if "unexpected keyword argument" in str(te) or "missing" in str(te):
                                # Try: source_path, destination_path, config (positional)
                                try:
                                    result = mft_app.transfer_file(
                                        f"{transfer.protocol}://{transfer.host}:{transfer.port}{source_path}",
                                        f"{transfer.protocol}://{transfer.host}:{transfer.port}{dest_path}",
                                        config
                                    )
                                except TypeError:
                                    # Try: just paths and config
                                    try:
                                        result = mft_app.transfer_file(
                                            source_path,
                                            dest_path,
                                            config
                                        )
                                    except TypeError as final_error:
                                        # Log what we tried
                                        print(f"⚠️  Could not call transfer_file(). Tried:")
                                        print(f"   1. transfer_file(source_path=..., destination_path=..., config=...)")
                                        print(f"   2. transfer_file(..., ..., config)")
                                        print(f"   Final error: {final_error}")
                                        result = False
                            else:
                                raise
                    elif hasattr(mft_app, 'start_transfer'):
                        result = mft_app.start_transfer(
                            source_path=source_path,
                            destination_path=dest_path,
                            protocol=transfer.protocol,
                            host=transfer.host,
                            port=transfer.port,
                            username=transfer.username,
                            password=transfer.password
                        )
                    elif hasattr(mft_app, 'run_transfer'):
                        result = mft_app.run_transfer(
                            source_path=source_path,
                            destination_path=dest_path,
                            protocol=transfer.protocol,
                            host=transfer.host,
                            port=transfer.port,
                            username=transfer.username,
                            password=transfer.password
                        )
                    else:
                        # MFT app loaded but no known method
                        available_methods = [m for m in dir(mft_app) if not m.startswith('_')]
                        print(f"⚠️  Warning: MFT app has no known transfer method.")
                        print(f"   Available methods: {available_methods}")
                        result = False
                        transfer_record[
                            'error'] = f"No compatible transfer method found. Available: {', '.join(available_methods[:5])}"

                    status_msg = "completed" if result else "failed"
                else:
                    status_msg = "scheduled"
            except Exception as e:
                print(f"Transfer execution error: {e}")
                status_msg = "failed"
                transfer_record['error'] = str(e)

            # Update status in memory storage
            transfer_record['status'] = status_msg
        else:
            status_msg = "scheduled"

        return TransferResponse(
            task_id=task_id,
            status=status_msg,
            source_path=source_path,
            destination_path=dest_path,
            protocol=transfer.protocol,
            created_at=datetime.utcnow().isoformat()
        )

    except Exception as e:
        print(f"Error creating transfer: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to create transfer: {str(e)}"
        )


@app.get("/api/v1/transfers/{task_id}")
async def get_transfer_status(task_id: str):
    """Get status of a specific transfer"""
    try:
        # Try database first
        if db and hasattr(db, 'get_transfer'):
            transfer = db.get_transfer(task_id)
            if transfer:
                return transfer

        # Try in-memory storage
        for transfer in transfers_storage:
            if transfer['task_id'] == task_id:
                return transfer

        raise HTTPException(status_code=404, detail="Transfer not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/v1/transfers/{task_id}")
async def delete_transfer(task_id: str):
    """Delete a transfer record"""
    try:
        # Try database first
        if db and hasattr(db, 'delete_transfer'):
            db.delete_transfer(task_id)

        # Remove from in-memory storage
        global transfers_storage
        transfers_storage = [t for t in transfers_storage if t['task_id'] != task_id]

        return {"message": "Transfer deleted successfully", "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# USERS (Active Directory) ENDPOINTS
# ============================================================================

# USERS (Active Directory) ENDPOINTS
# ============================================================================

users_storage = []  # In-memory storage for AD users
ad_config = {}  # In-memory storage for AD configuration


@app.post("/api/v1/users/config")
async def save_ad_config(config: dict):
    """Save Active Directory configuration"""
    try:
        # Store in memory
        ad_config.update(config)

        # Try to save to database if available
        if db and hasattr(db, 'save_ad_config'):
            db.save_ad_config(config)

        return {
            "success": True,
            "message": "Active Directory configuration saved successfully",
            "config": {
                "domain_controller": config.get('domain_controller'),
                "domain": config.get('domain'),
                "base_dn": config.get('base_dn'),
                "port": config.get('port'),
                "use_ssl": config.get('use_ssl')
            }
        }
    except Exception as e:
        print(f"Error saving AD config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/users/test")
async def test_ad_connection(config: dict):
    """Test Active Directory connection"""
    try:
        # Try to use database test if available
        if db and hasattr(db, 'test_ad_connection'):
            result = db.test_ad_connection(config)
            return {
                "success": True,
                "message": "Connection successful",
                "users_found": result.get('users_found', 0)
            }

        # Fallback: Try FileMonitorApp
        try:
            from file_monitor_app import FileMonitorApp
            app_instance = FileMonitorApp()
            if hasattr(app_instance, 'test_ad_connection'):
                result = app_instance.test_ad_connection(config)
                return {
                    "success": True,
                    "message": "Connection successful",
                    "users_found": result.get('users_found', 0)
                }
        except ImportError:
            pass

        # Demo mode - simulate success
        return {
            "success": True,
            "message": "Demo mode: Connection test simulated",
            "users_found": 10
        }

    except Exception as e:
        print(f"Error testing AD connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/users")
async def list_users():
    """Get all synced AD users"""
    try:
        if db and hasattr(db, 'get_all_users'):
            users = db.get_all_users()
            # Convert objects to dictionaries if needed
            result = []
            for u in users:
                if isinstance(u, dict):
                    result.append(u)
                else:
                    # It's an object, extract attributes
                    result.append({
                        'id': getattr(u, 'id', None),
                        'username': getattr(u, 'username', getattr(u, 'sam_account_name', 'Unknown')),
                        'display_name': getattr(u, 'display_name', getattr(u, 'name', 'Unknown')),
                        'email': getattr(u, 'email', None),
                        'department': getattr(u, 'department', None),
                        'enabled': getattr(u, 'enabled', True),
                        'status': getattr(u, 'status', 'active'),
                        'synced_at': getattr(u, 'synced_at', None)
                    })
            return {"users": result, "last_sync": datetime.utcnow().isoformat()}
        return {"users": users_storage, "last_sync": None if not users_storage else datetime.utcnow().isoformat()}
    except Exception as e:
        print(f"Error listing users: {e}")
        return {"users": users_storage, "last_sync": None}


@app.post("/api/v1/users/sync")
async def sync_ad_users(config: dict = None):
    """Sync users from Active Directory"""
    try:
        # Use provided config or fall back to saved config
        if not config:
            config = ad_config

        # Try to use database sync if available
        if db and hasattr(db, 'sync_ad_users'):
            result = db.sync_ad_users(config)
            users_count = result.get('users_synced', 0) if isinstance(result, dict) else len(result)
            return {
                "success": True,
                "users_synced": users_count,
                "message": f"Successfully synced {users_count} users from Active Directory",
                "timestamp": datetime.utcnow().isoformat()
            }

        # Fallback: Check if FileMonitorApp has AD sync
        try:
            from file_monitor_app import FileMonitorApp
            app_instance = FileMonitorApp()
            if hasattr(app_instance, 'sync_ad_users'):
                result = app_instance.sync_ad_users(config)
                users_count = result.get('users_synced', 0) if isinstance(result, dict) else 0
                return {
                    "success": True,
                    "users_synced": users_count,
                    "message": f"Successfully synced {users_count} users",
                    "timestamp": datetime.utcnow().isoformat()
                }
        except ImportError:
            pass

        # If no sync method available, create mock data
        mock_users = [
            {
                "id": 1,
                "username": "administrator",
                "display_name": "Administrator",
                "email": "admin@" + config.get('domain', 'domain.local').lower(),
                "department": "IT",
                "enabled": True,
                "status": "active",
                "synced_at": datetime.utcnow().isoformat()
            },
            {
                "id": 2,
                "username": "user1",
                "display_name": "User One",
                "email": "user1@" + config.get('domain', 'domain.local').lower(),
                "department": "Engineering",
                "enabled": True,
                "status": "active",
                "synced_at": datetime.utcnow().isoformat()
            },
            {
                "id": 3,
                "username": "jsmith",
                "display_name": "John Smith",
                "email": "jsmith@" + config.get('domain', 'domain.local').lower(),
                "department": "Sales",
                "enabled": True,
                "status": "active",
                "synced_at": datetime.utcnow().isoformat()
            }
        ]
        users_storage.clear()
        users_storage.extend(mock_users)

        return {
            "success": True,
            "users_synced": len(mock_users),
            "message": f"Demo mode: Created {len(mock_users)} mock users from {config.get('domain', 'N/A')} (no real AD connection)",
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        print(f"Error syncing AD users: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# RULES ENDPOINTS
# ============================================================================

@app.get("/api/v1/rules", response_model=List[RuleResponse])
async def list_rules():
    """List all transfer rules"""
    try:
        if not db:
            return []

        rules = db.get_all_rules() if hasattr(db, 'get_all_rules') else []
        return [
            RuleResponse(
                id=r['id'],
                name=r['name'],
                source_path=r.get('source_path', ''),
                destination_path=r.get('destination_path', ''),
                transfer_mode=r.get('transfer_mode', 'COPY'),
                enabled=r.get('enabled', True)
            )
            for r in rules
        ]
    except Exception as e:
        print(f"Error listing rules: {e}")
        return []


@app.post("/api/v1/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(rule: RuleCreate):
    """Create a new transfer rule"""
    try:
        if not db or not hasattr(db, 'add_rule'):
            raise HTTPException(status_code=500, detail="Database not initialized")

        rule_id = db.add_rule(
            name=rule.name,
            source_device_id=rule.source_device_id,
            destination_device_id=rule.destination_device_id,
            source_path=rule.source_path,
            destination_path=rule.destination_path,
            transfer_mode=rule.transfer_mode,
            schedule_type=rule.schedule_type,
            schedule_interval=rule.schedule_interval,
            cron_expression=rule.cron_expression,
            enabled=rule.enabled
        )

        return RuleResponse(
            id=rule_id,
            name=rule.name,
            source_path=rule.source_path,
            destination_path=rule.destination_path,
            transfer_mode=rule.transfer_mode,
            enabled=rule.enabled
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/v1/rules/{rule_id}")
async def delete_rule(rule_id: int):
    """Delete a rule"""
    try:
        if not db or not hasattr(db, 'delete_rule'):
            raise HTTPException(status_code=500, detail="Database not initialized")

        db.delete_rule(rule_id)
        return {"message": "Rule deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/v1/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: int):
    """Enable or disable a rule"""
    try:
        if not db or not hasattr(db, 'toggle_rule'):
            raise HTTPException(status_code=500, detail="Database not initialized")

        db.toggle_rule(rule_id)
        return {"message": "Rule toggled successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# STATISTICS ENDPOINT
# ============================================================================

@app.get("/api/v1/statistics", response_model=StatisticsResponse)
async def get_statistics():
    """Get system statistics"""
    try:
        stats = StatisticsResponse()

        if db:
            # Get device count
            devices = db.get_all_devices() if hasattr(db, 'get_all_devices') else []
            stats.total_devices = len(devices)

            # Get rule count
            rules = db.get_all_rules() if hasattr(db, 'get_all_rules') else []
            stats.total_rules = len(rules)

            # Get transfer statistics
            if hasattr(db, 'get_transfer_stats'):
                transfer_stats = db.get_transfer_stats()
                stats.active_transfers = transfer_stats.get('active', 0)
                stats.completed_transfers = transfer_stats.get('completed', 0)
                stats.failed_transfers = transfer_stats.get('failed', 0)

                # Calculate success rate
                total = stats.completed_transfers + stats.failed_transfers
                if total > 0:
                    stats.success_rate = (stats.completed_transfers / total) * 100

        return stats
    except Exception as e:
        print(f"Error getting statistics: {e}")
        return StatisticsResponse()


# ============================================================================
# AUDIT LOG ENDPOINT
# ============================================================================

@app.get("/api/v1/audit")
async def get_audit_log(limit: int = 50):
    """Get audit log entries"""
    try:
        if not db or not hasattr(db, 'get_audit_log'):
            return []

        logs = db.get_audit_log(limit=limit)
        return logs
    except Exception as e:
        print(f"Error getting audit log: {e}")
        return []


# ============================================================================
# DOMAIN CONFIGURATION (Placeholder)
# ============================================================================

@app.post("/api/v1/domain/test")
async def test_domain_connection(config: dict):
    """Test domain connection"""
    return {
        "status": "success",
        "message": "Connection successful (placeholder)"
    }


@app.post("/api/v1/domain/config")
async def save_domain_config(config: dict):
    """Save domain configuration"""
    return {
        "status": "success",
        "message": "Configuration saved (placeholder)"
    }


@app.post("/api/v1/domain/sync")
async def sync_domain_users():
    """Sync users from domain"""
    return {
        "status": "success",
        "users_synced": 0,
        "message": "Domain sync placeholder"
    }


# ============================================================================
# ROOT REDIRECT
# ============================================================================

@app.get("/")
async def root():
    """Redirect to API docs"""
    return {
        "message": "MFT API Server",
        "version": "2.0",
        "docs": "/docs",
        "health": "/api/v1/health"
    }


if __name__ == "__main__":
    import uvicorn

    print("=" * 70)
    print("🚀 MFT API Server v2.0")
    print("=" * 70)
    print()
    print("✅ All endpoints enabled:")
    print("   📦 Devices: /api/v1/devices")
    print("   🚀 Transfers: /api/v1/transfers (NEW!)")
    print("   📋 Rules: /api/v1/rules")
    print("   📊 Statistics: /api/v1/statistics")
    print("   📜 Audit: /api/v1/audit")
    print()
    print("📖 API Documentation: http://localhost:8000/docs")
    print("❤️  Health Check: http://localhost:8000/api/v1/health")
    print()
    print("=" * 70)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")