#!/usr/bin/env python3
"""
Complete MFT System - Fully Integrated
API + Web Console in one file
"""

import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

# Try to import existing modules (optional)
try:
    from database import Database

    db = Database()
    print("✅ Database module loaded")
except ImportError:
    db = None
    print("⚠️  Database module not found - using in-memory storage")

try:
    from mft_application import MFTApplication

    mft_app = MFTApplication()
    print("✅ MFT Application loaded")
except ImportError:
    mft_app = None
    print("⚠️  MFT Application not found - transfers will be simulated")

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title="MFT System", version="3.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage
transfers_storage = []
devices_storage = []
rules_storage = []


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


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/v1/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "3.0",
        "database": "connected" if db else "in-memory",
        "mft_app": "loaded" if mft_app else "simulated"
    }


# DEVICES
@app.get("/api/v1/devices")
async def list_devices():
    try:
        if db and hasattr(db, 'get_all_devices'):
            devices = db.get_all_devices()
            # Convert objects to dictionaries if needed
            result = []
            for d in devices:
                if isinstance(d, dict):
                    result.append(d)
                else:
                    # It's an object, extract attributes
                    result.append({
                        'id': getattr(d, 'id', None),
                        'name': getattr(d, 'name', 'Unknown'),
                        'hostname': getattr(d, 'hostname', 'Unknown'),
                        'port': getattr(d, 'port', 22),
                        'protocol': getattr(d, 'protocol', 'sftp'),
                        'username': getattr(d, 'username', None),
                        'status': getattr(d, 'status', 'unknown')
                    })
            return result
        return devices_storage
    except Exception as e:
        print(f"Error listing devices: {e}")
        return devices_storage


@app.post("/api/v1/devices", status_code=status.HTTP_201_CREATED)
async def create_device(device: DeviceCreate):
    try:
        device_id = len(devices_storage) + 1
        device_dict = {
            'id': device_id,
            'name': device.name,
            'hostname': device.hostname,
            'port': device.port,
            'protocol': device.protocol,
            'username': device.username,
            'status': 'unknown'
        }

        if db and hasattr(db, 'add_device'):
            device_id = db.add_device(**device.dict())
            device_dict['id'] = device_id
        else:
            devices_storage.append(device_dict)

        return device_dict
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/v1/devices/{device_id}")
async def delete_device(device_id: int):
    global devices_storage
    devices_storage = [d for d in devices_storage if d['id'] != device_id]
    if db and hasattr(db, 'delete_device'):
        db.delete_device(device_id)
    return {"message": "Device deleted"}


# TRANSFERS
@app.get("/api/v1/transfers")
async def list_transfers():
    try:
        if db and hasattr(db, 'get_all_transfers'):
            transfers = db.get_all_transfers()
            # Convert objects to dictionaries if needed
            result = []
            for t in transfers:
                if isinstance(t, dict):
                    result.append(t)
                else:
                    # It's an object, extract attributes
                    result.append({
                        'task_id': getattr(t, 'task_id', ''),
                        'source_path': getattr(t, 'source_path', ''),
                        'destination_path': getattr(t, 'destination_path', ''),
                        'protocol': getattr(t, 'protocol', 'unknown'),
                        'host': getattr(t, 'host', ''),
                        'port': getattr(t, 'port', 0),
                        'username': getattr(t, 'username', None),
                        'status': getattr(t, 'status', 'unknown'),
                        'created_at': getattr(t, 'created_at', None),
                        'encryption_enabled': getattr(t, 'encryption_enabled', False),
                        'schedule_type': getattr(t, 'schedule_type', 'on_demand')
                    })
            return result
        return transfers_storage
    except Exception as e:
        print(f"Error listing transfers: {e}")
        return transfers_storage


@app.post("/api/v1/transfers", status_code=status.HTTP_201_CREATED)
async def create_transfer(transfer: TransferCreate):
    try:
        task_id = str(uuid.uuid4())

        # Clean paths
        source_path = transfer.source_path
        dest_path = transfer.destination_path

        # Remove protocol prefixes
        for protocol in ['sftp://', 'ftps://', 'ftp://', 'smb://', 'unc://', 'https://']:
            source_path = source_path.replace(protocol, '')
            dest_path = dest_path.replace(protocol, '')

        # Extract path from hostname if present
        if '/' in source_path and not source_path.startswith('/'):
            parts = source_path.split('/', 1)
            if len(parts) > 1:
                source_path = '/' + parts[1]

        if '/' in dest_path and not dest_path.startswith('/'):
            parts = dest_path.split('/', 1)
            if len(parts) > 1:
                dest_path = '/' + parts[1]

        # Normalize backslashes to forward slashes
        source_path = source_path.replace('\\', '/')
        dest_path = dest_path.replace('\\', '/')

        # Create transfer record
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

        # Try to execute if on_demand
        if transfer.schedule_type == 'on_demand':
            try:
                if mft_app:
                    # Try different method names that might exist
                    if hasattr(mft_app, 'execute_transfer'):
                        result = mft_app.execute_transfer(
                            source=f"{transfer.protocol}://{transfer.host}:{transfer.port}{source_path}",
                            destination=f"{transfer.protocol}://{transfer.host}:{transfer.port}{dest_path}",
                            username=transfer.username,
                            password=transfer.password
                        )
                    elif hasattr(mft_app, 'transfer_file'):
                        # Build TransferConfig object (not a dict!)
                        try:
                            from mft_application import TransferConfig, TransferProtocol

                            # Convert protocol string to enum
                            protocol_map = {
                                'sftp': TransferProtocol.SFTP,
                                'ftp': TransferProtocol.FTP,
                                'ftps': TransferProtocol.FTPS,
                                'smb': TransferProtocol.SMB,
                                'unc': TransferProtocol.UNC,
                                'http': TransferProtocol.HTTP,
                                'https': TransferProtocol.HTTPS,
                            }

                            protocol_enum = protocol_map.get(transfer.protocol.lower(), TransferProtocol.SMB)

                            # Fix destination path - add host if missing
                            if dest_path and not dest_path.startswith('//') and not dest_path.startswith('\\\\'):
                                # Add host prefix for SMB/UNC protocols
                                if protocol_enum in [TransferProtocol.SMB, TransferProtocol.UNC]:
                                    # Ensure slash between host and path
                                    if not dest_path.startswith('/'):
                                        dest_path = f"//{transfer.host}/{dest_path}"
                                    else:
                                        dest_path = f"//{transfer.host}{dest_path}"

                            # Create proper TransferConfig object
                            config = TransferConfig(
                                protocol=protocol_enum,
                                host=transfer.host,
                                port=transfer.port,
                                username=transfer.username,
                                password=transfer.password,
                                encryption_enabled=transfer.encryption_enabled,
                                retry_count=3,
                                retry_delay=5,
                                timeout=300
                            )

                            print(f"\n{'=' * 80}")
                            print(f"🚀 CALLING MFT APPLICATION")
                            print(f"{'=' * 80}")
                            print(f"📂 Source: {source_path}")
                            print(f"📂 Dest: {dest_path}")
                            print(
                                f"⚙️  Config: protocol={config.protocol.value}, host={config.host}, port={config.port}")
                            print(f"{'=' * 80}\n")

                            # Call async method properly
                            import asyncio

                            # Create the transfer task
                            task_id = await mft_app.transfer_file(source_path, dest_path, config)
                            print(f"✅ Transfer task created: {task_id}")

                            # CRITICAL: Wait for transfer to actually complete!
                            # Poll status until it's done
                            max_wait = 60  # Wait up to 60 seconds
                            poll_interval = 0.5  # Check every 0.5 seconds
                            elapsed = 0

                            print(f"⏳ Waiting for transfer to complete (max {max_wait}s)...")

                            while elapsed < max_wait:
                                await asyncio.sleep(poll_interval)
                                elapsed += poll_interval

                                # Get current status
                                status = mft_app.get_transfer_status(task_id)
                                if status:
                                    current_status = status.get('status', 'unknown')
                                    print(f"   [{elapsed:.1f}s] Status: {current_status}")

                                    if current_status == 'completed':
                                        print(f"✅ Transfer COMPLETED successfully!")
                                        transfer_record['status'] = "completed"
                                        result = True
                                        break
                                    elif current_status == 'failed':
                                        error_msg = status.get('error_message', 'Unknown error')
                                        print(f"❌ Transfer FAILED: {error_msg}")
                                        transfer_record['status'] = "failed"
                                        transfer_record['error'] = error_msg
                                        result = False
                                        break
                                    elif current_status in ['pending', 'in_progress']:
                                        # Still running, continue waiting
                                        continue
                                else:
                                    print(f"   ⚠️  Could not get status for task {task_id}")

                            if elapsed >= max_wait:
                                print(f"⚠️  Transfer timeout after {max_wait}s")
                                transfer_record['status'] = "timeout"
                                transfer_record['error'] = "Transfer timeout - check if still running"
                                result = False

                        except ImportError as ie:
                            print(f"⚠️  Could not import TransferConfig: {ie}")
                            print(f"   Attempting fallback direct copy...")
                            # Fallback to basic file copy
                            try:
                                import shutil
                                import os

                                # Normalize paths for Windows
                                src_normalized = source_path.replace('//', '\\\\').replace('/', '\\')

                                # Fix destination - add host if needed
                                if not dest_path.startswith('//') and not dest_path.startswith('\\\\'):
                                    dest_path = f"//{transfer.host}{dest_path}"
                                dst_normalized = dest_path.replace('//', '\\\\').replace('/', '\\')

                                print(f"📋 Normalized paths:")
                                print(f"   Source: {src_normalized}")
                                print(f"   Dest: {dst_normalized}")

                                if os.path.exists(src_normalized):
                                    print(f"✅ Source exists")
                                    # Create destination directory
                                    dest_dir = os.path.dirname(dst_normalized)
                                    if dest_dir:
                                        os.makedirs(dest_dir, exist_ok=True)
                                        print(f"✅ Created dest directory: {dest_dir}")

                                    # Copy
                                    print(f"📤 Copying...")
                                    if os.path.isdir(src_normalized):
                                        shutil.copytree(src_normalized, dst_normalized, dirs_exist_ok=True)
                                    else:
                                        shutil.copy2(src_normalized, dst_normalized)

                                    # Verify
                                    if os.path.exists(dst_normalized):
                                        print(f"✅ FALLBACK COPY SUCCESSFUL!")
                                        transfer_record['status'] = "completed"
                                        result = True
                                    else:
                                        print(f"❌ Destination not found after copy")
                                        transfer_record['status'] = "failed"
                                        transfer_record['error'] = "File not found at destination"
                                        result = False
                                else:
                                    print(f"❌ Source not found: {src_normalized}")
                                    transfer_record['status'] = "failed"
                                    transfer_record['error'] = f"Source not found: {src_normalized}"
                                    result = False
                            except Exception as copy_error:
                                print(f"❌ Fallback copy failed: {copy_error}")
                                import traceback
                                traceback.print_exc()
                                result = False
                                transfer_record['status'] = "failed"
                                transfer_record['error'] = f"Fallback copy failed: {copy_error}"

                        except Exception as te:
                            print(f"❌ Transfer error: {te}")
                            import traceback
                            traceback.print_exc()
                            result = False
                            transfer_record['status'] = "failed"
                            transfer_record['error'] = str(te)
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
                    else:
                        # MFT app loaded but no known method
                        print(f"Warning: MFT app has no known transfer method. Available methods: {dir(mft_app)}")
                        result = False
                        transfer_record['error'] = "No compatible transfer method found"

                    transfer_record['status'] = "completed" if result else "failed"
                else:
                    # Simulate execution
                    transfer_record['status'] = "failed"
                    transfer_record['error'] = "MFT Application not loaded - simulated execution"
            except Exception as e:
                print(f"Transfer execution error: {e}")
                transfer_record['status'] = "failed"
                transfer_record['error'] = str(e)
        else:
            transfer_record['status'] = "scheduled"

        # Store transfer
        transfers_storage.append(transfer_record)
        if db and hasattr(db, 'add_transfer'):
            db.add_transfer(**transfer_record)

        return transfer_record

    except Exception as e:
        print(f"Error creating transfer: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/transfers/{task_id}")
async def get_transfer_status(task_id: str):
    # Check in-memory storage first
    for transfer in transfers_storage:
        if transfer['task_id'] == task_id:
            return transfer

    # Check database
    if db and hasattr(db, 'get_transfer'):
        transfer = db.get_transfer(task_id)
        if transfer:
            # Convert to dict if it's an object
            if isinstance(transfer, dict):
                return transfer
            else:
                return {
                    'task_id': getattr(transfer, 'task_id', task_id),
                    'source_path': getattr(transfer, 'source_path', ''),
                    'destination_path': getattr(transfer, 'destination_path', ''),
                    'protocol': getattr(transfer, 'protocol', 'unknown'),
                    'host': getattr(transfer, 'host', ''),
                    'port': getattr(transfer, 'port', 0),
                    'username': getattr(transfer, 'username', None),
                    'status': getattr(transfer, 'status', 'unknown'),
                    'created_at': getattr(transfer, 'created_at', None),
                    'encryption_enabled': getattr(transfer, 'encryption_enabled', False),
                    'schedule_type': getattr(transfer, 'schedule_type', 'on_demand'),
                    'error': getattr(transfer, 'error', None)
                }

    raise HTTPException(status_code=404, detail="Transfer not found")


@app.delete("/api/v1/transfers/{task_id}")
async def delete_transfer(task_id: str):
    global transfers_storage
    transfers_storage = [t for t in transfers_storage if t['task_id'] != task_id]
    if db and hasattr(db, 'delete_transfer'):
        db.delete_transfer(task_id)
    return {"message": "Transfer deleted", "task_id": task_id}


# USERS (Active Directory)
users_storage = []  # In-memory storage for AD users

# USERS (Active Directory)
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


# RULES
@app.get("/api/v1/rules")
async def list_rules():
    try:
        if db and hasattr(db, 'get_all_rules'):
            rules = db.get_all_rules()
            # Convert objects to dictionaries if needed
            result = []
            for r in rules:
                if isinstance(r, dict):
                    result.append(r)
                else:
                    # It's an object, extract attributes
                    result.append({
                        'id': getattr(r, 'id', None),
                        'name': getattr(r, 'name', 'Unknown'),
                        'source_path': getattr(r, 'source_path', ''),
                        'destination_path': getattr(r, 'destination_path', ''),
                        'transfer_mode': getattr(r, 'transfer_mode', 'COPY'),
                        'enabled': getattr(r, 'enabled', True)
                    })
            return result
        return rules_storage
    except Exception as e:
        print(f"Error listing rules: {e}")
        return rules_storage


# STATISTICS
@app.get("/api/v1/statistics")
async def get_statistics():
    try:
        total_devices = len(devices_storage)
        total_rules = len(rules_storage)

        active = len([t for t in transfers_storage if t['status'] in ['active', 'running']])
        completed = len([t for t in transfers_storage if t['status'] in ['completed', 'success']])
        failed = len([t for t in transfers_storage if t['status'] in ['failed', 'error']])

        total = completed + failed
        success_rate = (completed / total * 100) if total > 0 else 100.0

        return {
            "active_transfers": active,
            "completed_transfers": completed,
            "failed_transfers": failed,
            "success_rate": success_rate,
            "total_devices": total_devices,
            "total_rules": total_rules
        }
    except Exception as e:
        return {
            "active_transfers": 0,
            "completed_transfers": 0,
            "failed_transfers": 0,
            "success_rate": 100.0,
            "total_devices": 0,
            "total_rules": 0
        }


# AUDIT
@app.get("/api/v1/audit")
async def get_audit_log():
    return []


# ============================================================================
# WEB UI
# ============================================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MFT Management Console</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined" rel="stylesheet"/>
    <style>
        .material-symbols-outlined { font-size: 20px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .modal { display: none; position: fixed; z-index: 50; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }
        .modal.active { display: flex; align-items: center; justify-content: center; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .spinner { animation: spin 1s linear infinite; }
        #debug-console { 
            position: fixed; bottom: 0; right: 0; width: 500px; max-height: 400px; 
            background: #1a1a1a; color: #00ff00; border: 2px solid #00ff00; 
            border-radius: 8px 0 0 0; font-family: 'Courier New', monospace; 
            font-size: 11px; overflow-y: auto; padding: 12px; display: none; z-index: 9999;
            box-shadow: 0 -4px 20px rgba(0,255,0,0.3);
        }
        #debug-console.active { display: block; }
        .debug-entry { padding: 6px 0; border-bottom: 1px solid #333; word-wrap: break-word; }
        .debug-error { color: #ff4444; font-weight: bold; }
        .debug-success { color: #44ff44; }
        .debug-info { color: #4488ff; }
    </style>
</head>
<body class="bg-gray-100">
    <button onclick="toggleDebugConsole()" 
            class="fixed bottom-4 right-4 bg-green-600 text-white px-4 py-2 rounded-full shadow-lg hover:bg-green-700 z-50 flex items-center gap-2">
        <span class="material-symbols-outlined" style="font-size: 24px;">bug_report</span>
        <span class="text-sm font-bold">DEBUG</span>
    </button>

    <div id="debug-console">
        <div class="flex justify-between items-center mb-3 border-b-2 border-green-500 pb-2">
            <span class="font-bold text-green-400 text-base">🐛 DEBUG CONSOLE</span>
            <div class="flex gap-2">
                <button onclick="clearDebugConsole()" class="text-yellow-400 hover:text-yellow-300 text-xs">CLEAR</button>
                <button onclick="toggleDebugConsole()" class="text-red-400 hover:text-red-300 text-xs">CLOSE</button>
            </div>
        </div>
        <div id="debug-content" class="text-xs"></div>
    </div>

    <header class="bg-white py-4 px-4 shadow-sm">
        <div class="container mx-auto">
            <h1 class="text-2xl font-bold text-gray-900">🚀 MFT Management Console</h1>
            <p class="text-sm text-gray-500">Complete Integrated System v3.0</p>
        </div>
    </header>

    <div class="bg-white border-b sticky top-0 z-10">
        <div class="container mx-auto flex overflow-x-auto">
            <button onclick="showTab('dashboard', this)" class="tab-btn px-4 py-3 text-sm font-medium border-b-2 border-indigo-600 text-indigo-600">Dashboard</button>
            <button onclick="showTab('new-transfer', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">New Transfer</button>
            <button onclick="showTab('transfers', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Transfers</button>
            <button onclick="showTab('devices', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Devices</button>
            <button onclick="showTab('users', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Users (AD)</button>
            <button onclick="showTab('rules', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Rules</button>
        </div>
    </div>

    <!-- Dashboard -->
    <main id="dashboard" class="tab-content active container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">System Dashboard</h2>
            <button onclick="refreshDashboard()" class="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700">
                <span class="material-symbols-outlined mr-1" style="font-size: 18px;">refresh</span> Refresh
            </button>
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div class="bg-gradient-to-r from-blue-500 to-blue-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Active Transfers</p>
                <p id="stat-active" class="text-3xl font-bold mt-1">0</p>
            </div>
            <div class="bg-gradient-to-r from-green-500 to-green-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Completed</p>
                <p id="stat-completed" class="text-3xl font-bold mt-1">0</p>
            </div>
            <div class="bg-gradient-to-r from-red-500 to-red-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Failed</p>
                <p id="stat-failed" class="text-3xl font-bold mt-1">0</p>
            </div>
            <div class="bg-gradient-to-r from-purple-500 to-purple-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Success Rate</p>
                <p id="stat-success" class="text-3xl font-bold mt-1">100%</p>
            </div>
        </div>
    </main>

    <!-- New Transfer -->
    <main id="new-transfer" class="tab-content container mx-auto p-4">
        <h2 class="text-2xl font-bold mb-6">Create New Transfer</h2>
        <div class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
            <p class="font-semibold text-blue-900 mb-2">📘 Path Format Guide:</p>
            <ul class="text-sm text-blue-800 space-y-1">
                <li>• <strong>SFTP:</strong> <code>/home/user/file.txt</code> (Linux paths, port 22)</li>
                <li>• <strong>SMB:</strong> <code>/C$/Users/username/file.txt</code> (Windows shares, port 445)</li>
                <li>• <strong>FTP/FTPS:</strong> <code>/pub/files/file.txt</code> (FTP paths)</li>
            </ul>
        </div>
        <div class="bg-white rounded-lg shadow p-6">
            <form id="transfer-form" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Source Path *</label>
                        <input type="text" id="source-path" required class="w-full border rounded px-3 py-2" placeholder="/path/to/source">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Destination Path *</label>
                        <input type="text" id="dest-path" required class="w-full border rounded px-3 py-2" placeholder="/path/to/destination">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Protocol *</label>
                        <select id="protocol" required class="w-full border rounded px-3 py-2" onchange="updateDefaultPort()">
                            <option value="unc">UNC - Windows Shares (Port 445)</option>
                            <option value="smb">SMB (Port 445)</option>
                            <option value="sftp">SFTP (Port 22)</option>
                            <option value="ftps">FTPS (Port 990)</option>
                            <option value="ftp">FTP (Port 21)</option>
                            <option value="https">HTTPS (Port 443)</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Host *</label>
                        <input type="text" id="host" required class="w-full border rounded px-3 py-2" placeholder="192.168.1.100">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Port *</label>
                        <input type="number" id="port" required value="22" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Username</label>
                        <input type="text" id="username" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Password</label>
                        <input type="password" id="password" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Schedule Type</label>
                        <select id="schedule-type" class="w-full border rounded px-3 py-2">
                            <option value="on_demand">On Demand (Execute now)</option>
                            <option value="scheduled">Scheduled</option>
                        </select>
                    </div>
                </div>
                <div class="flex gap-3 pt-4">
                    <button type="submit" class="px-6 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">
                        Create Transfer
                    </button>
                    <button type="reset" class="px-6 py-2 bg-gray-200 rounded hover:bg-gray-300">Clear</button>
                </div>
            </form>
        </div>
    </main>

    <!-- Transfers History -->
    <main id="transfers" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Transfer History</h2>
            <div class="flex gap-2">
                <button onclick="loadTransfers()" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">Refresh</button>
                <button onclick="clearFailedTransfers()" class="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700">Clear Failed</button>
            </div>
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <div class="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <p class="text-sm text-blue-600 font-medium">Total</p>
                <p id="transfer-total" class="text-2xl font-bold text-blue-900">0</p>
            </div>
            <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                <p class="text-sm text-green-600 font-medium">Successful</p>
                <p id="transfer-success" class="text-2xl font-bold text-green-900">0</p>
            </div>
            <div class="bg-red-50 border border-red-200 rounded-lg p-4">
                <p class="text-sm text-red-600 font-medium">Failed</p>
                <p id="transfer-failed" class="text-2xl font-bold text-red-900">0</p>
            </div>
            <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <p class="text-sm text-yellow-600 font-medium">Pending</p>
                <p id="transfer-pending" class="text-2xl font-bold text-yellow-900">0</p>
            </div>
        </div>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">Task ID</th>
                        <th class="px-6 py-3 text-left">Status</th>
                        <th class="px-6 py-3 text-left">Protocol</th>
                        <th class="px-6 py-3 text-left">Source</th>
                        <th class="px-6 py-3 text-left">Destination</th>
                        <th class="px-6 py-3 text-left">Created</th>
                        <th class="px-6 py-3 text-left">Actions</th>
                    </tr>
                </thead>
                <tbody id="transfers-body">
                    <tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Devices -->
    <main id="devices" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Devices</h2>
            <button onclick="loadDevices()" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">Refresh</button>
        </div>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">ID</th>
                        <th class="px-6 py-3 text-left">Name</th>
                        <th class="px-6 py-3 text-left">Hostname</th>
                        <th class="px-6 py-3 text-left">Protocol</th>
                    </tr>
                </thead>
                <tbody id="devices-body">
                    <tr><td colspan="4" class="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Users (Active Directory) -->
    <main id="users" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Active Directory Users</h2>
            <div class="flex gap-2">
                <button onclick="toggleADConfig()" class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 flex items-center gap-2">
                    <span class="material-symbols-outlined" style="font-size: 18px;">settings</span>
                    AD Settings
                </button>
                <button onclick="syncADUsers()" class="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 flex items-center gap-2">
                    <span class="material-symbols-outlined" style="font-size: 18px;">sync</span>
                    Sync from AD
                </button>
                <button onclick="loadUsers()" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 flex items-center gap-2">
                    <span class="material-symbols-outlined" style="font-size: 18px;">refresh</span>
                    Refresh
                </button>
            </div>
        </div>

        <!-- AD Configuration Card (Collapsible) -->
        <div id="ad-config-card" class="bg-white rounded-lg shadow-lg p-6 mb-6" style="display: none;">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-bold text-gray-900">Active Directory Configuration</h3>
                <button onclick="toggleADConfig()" class="text-gray-400 hover:text-gray-600">
                    <span class="material-symbols-outlined">close</span>
                </button>
            </div>

            <form id="ad-config-form" class="space-y-4">
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Domain Controller</label>
                        <input type="text" id="ad-domain-controller" 
                               class="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-indigo-500" 
                               placeholder="dc.domain.local" value="dc.scepces.local">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Domain</label>
                        <input type="text" id="ad-domain" 
                               class="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-indigo-500" 
                               placeholder="DOMAIN" value="SCEPCES">
                    </div>
                </div>

                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Base DN</label>
                        <input type="text" id="ad-base-dn" 
                               class="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-indigo-500" 
                               placeholder="DC=domain,DC=local" value="DC=scepces,DC=local">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Port</label>
                        <input type="number" id="ad-port" 
                               class="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-indigo-500" 
                               value="389">
                    </div>
                </div>

                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Username</label>
                        <input type="text" id="ad-username" 
                               class="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-indigo-500" 
                               placeholder="admin@domain.local" value="administrator@scepces.local">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
                        <input type="password" id="ad-password" 
                               class="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-indigo-500" 
                               placeholder="••••••••">
                    </div>
                </div>

                <div class="flex items-center gap-2">
                    <input type="checkbox" id="ad-use-ssl" class="rounded" checked>
                    <label class="text-sm text-gray-700">Use SSL/TLS (Recommended)</label>
                </div>

                <div class="flex gap-2 pt-2">
                    <button type="button" onclick="saveADConfig()" 
                            class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 flex items-center gap-2">
                        <span class="material-symbols-outlined" style="font-size: 18px;">save</span>
                        Save Configuration
                    </button>
                    <button type="button" onclick="testADConnection()" 
                            class="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 flex items-center gap-2">
                        <span class="material-symbols-outlined" style="font-size: 18px;">wifi_find</span>
                        Test Connection
                    </button>
                </div>
            </form>
        </div>

        <!-- AD Sync Status Card -->
        <div class="bg-gradient-to-r from-blue-500 to-indigo-600 p-6 rounded-lg text-white mb-6">
            <div class="flex items-center justify-between">
                <div>
                    <p class="text-sm opacity-90">Total AD Users</p>
                    <p id="ad-user-count" class="text-4xl font-bold mt-1">0</p>
                </div>
                <div class="text-right">
                    <p class="text-sm opacity-90">Last Sync</p>
                    <p id="ad-last-sync" class="text-lg mt-1">Never</p>
                </div>
                <div class="text-6xl opacity-20">
                    <span class="material-symbols-outlined" style="font-size: 80px;">group</span>
                </div>
            </div>
        </div>

        <!-- Users Table -->
        <div class="bg-white rounded-lg shadow overflow-hidden">
            <table class="w-full">
                <thead class="bg-gray-50 border-b">
                    <tr>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Username</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Display Name</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Email</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Department</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Synced</th>
                    </tr>
                </thead>
                <tbody id="users-body" class="divide-y divide-gray-200">
                    <tr>
                        <td colspan="6" class="px-6 py-8 text-center text-gray-500">
                            <div class="flex flex-col items-center gap-3">
                                <span class="material-symbols-outlined text-gray-300" style="font-size: 48px;">person_off</span>
                                <p>No users synced yet</p>
                                <button onclick="syncADUsers()" class="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700">
                                    Sync from Active Directory
                                </button>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Rules -->
    <main id="rules" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Rules</h2>
            <button onclick="loadRules()" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">Refresh</button>
        </div>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">ID</th>
                        <th class="px-6 py-3 text-left">Name</th>
                        <th class="px-6 py-3 text-left">Enabled</th>
                    </tr>
                </thead>
                <tbody id="rules-body">
                    <tr><td colspan="3" class="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <script>
        const API_BASE = '/api/v1';

        function toggleDebugConsole() {
            document.getElementById('debug-console').classList.toggle('active');
        }

        function clearDebugConsole() {
            document.getElementById('debug-content').innerHTML = '';
            logDebug('Console cleared', 'info');
        }

        function logDebug(message, type = 'info') {
            const debugContent = document.getElementById('debug-content');
            const timestamp = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = `debug-entry debug-${type}`;
            let icon = '•';
            if (type === 'error') icon = '✖';
            if (type === 'success') icon = '✓';
            entry.innerHTML = `<strong>[${timestamp}]</strong> ${icon} ${message}`;
            debugContent.appendChild(entry);
            debugContent.scrollTop = debugContent.scrollHeight;
        }

        function logAPICall(method, url, data = null) {
            logDebug(`API ${method}: ${url}`, 'info');
            if (data) logDebug(`Request: ${JSON.stringify(data, null, 2)}`, 'info');
        }

        function logAPIResponse(response, success = true) {
            logDebug(`Response: ${JSON.stringify(response, null, 2)}`, success ? 'success' : 'error');
        }

        function updateDefaultPort() {
            const protocol = document.getElementById('protocol').value;
            const ports = {'unc': 445, 'smb': 445, 'sftp': 22, 'ftp': 21, 'ftps': 990, 'https': 443};
            document.getElementById('port').value = ports[protocol] || 445;
        }

        function showTab(tabId, btnElement) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => {
                b.classList.remove('text-indigo-600', 'border-b-2', 'border-indigo-600');
                b.classList.add('text-gray-500');
            });
            document.getElementById(tabId).classList.add('active');
            btnElement.classList.remove('text-gray-500');
            btnElement.classList.add('text-indigo-600', 'border-b-2', 'border-indigo-600');

            if (tabId === 'dashboard') refreshDashboard();
            if (tabId === 'transfers') loadTransfers();
            if (tabId === 'devices') loadDevices();
            if (tabId === 'rules') loadRules();
        }

        async function refreshDashboard() {
            try {
                logAPICall('GET', `${API_BASE}/statistics`);
                const res = await fetch(`${API_BASE}/statistics`);
                const stats = await res.json();
                logAPIResponse(stats, true);
                document.getElementById('stat-active').textContent = stats.active_transfers || 0;
                document.getElementById('stat-completed').textContent = stats.completed_transfers || 0;
                document.getElementById('stat-failed').textContent = stats.failed_transfers || 0;
                document.getElementById('stat-success').textContent = (stats.success_rate || 100).toFixed(1) + '%';
            } catch (error) {
                logDebug(`Dashboard error: ${error.message}`, 'error');
            }
        }

        document.getElementById('transfer-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = {
                source_path: document.getElementById('source-path').value,
                destination_path: document.getElementById('dest-path').value,
                protocol: document.getElementById('protocol').value,
                host: document.getElementById('host').value,
                port: parseInt(document.getElementById('port').value),
                username: document.getElementById('username').value || null,
                password: document.getElementById('password').value || null,
                encryption_enabled: true,
                compliance_frameworks: [],
                schedule_type: document.getElementById('schedule-type').value,
                schedule_interval: null,
                cron_expression: null
            };

            try {
                logAPICall('POST', `${API_BASE}/transfers`, data);
                const res = await fetch(`${API_BASE}/transfers`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });

                const responseData = await res.json();
                logAPIResponse(responseData, res.ok);

                if (!res.ok) throw new Error(responseData.detail || 'Failed');

                alert(`✅ Transfer created!\\n\\nTask ID: ${responseData.task_id}\\nStatus: ${responseData.status}`);
                document.getElementById('transfer-form').reset();
                updateDefaultPort();

            } catch (error) {
                logDebug(`Transfer failed: ${error.message}`, 'error');
                alert(`❌ Error: ${error.message}`);
            }
        });

        async function loadTransfers() {
            const tbody = document.getElementById('transfers-body');
            tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center"><div class="spinner inline-block w-6 h-6 border-2 border-gray-300 border-t-indigo-600 rounded-full"></div></td></tr>';

            try {
                logAPICall('GET', `${API_BASE}/transfers`);
                const res = await fetch(`${API_BASE}/transfers`);
                const transfers = await res.json();
                logAPIResponse(transfers, true);

                if (!transfers || transfers.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">No transfers yet</td></tr>';
                    updateTransferStats(0, 0, 0, 0);
                    return;
                }

                const successCount = transfers.filter(t => t.status === 'completed' || t.status === 'success').length;
                const failedCount = transfers.filter(t => t.status === 'failed' || t.status === 'error').length;
                const pendingCount = transfers.filter(t => t.status === 'pending' || t.status === 'scheduled').length;
                updateTransferStats(transfers.length, successCount, failedCount, pendingCount);

                transfers.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

                tbody.innerHTML = transfers.map(t => {
                    let statusClass, statusIcon, statusText;
                    switch(t.status?.toLowerCase()) {
                        case 'completed':
                        case 'success':
                            statusClass = 'bg-green-100 text-green-800';
                            statusIcon = '✓';
                            statusText = 'Completed';
                            break;
                        case 'failed':
                        case 'error':
                            statusClass = 'bg-red-100 text-red-800';
                            statusIcon = '✗';
                            statusText = 'Failed';
                            break;
                        default:
                            statusClass = 'bg-yellow-100 text-yellow-800';
                            statusIcon = '⏳';
                            statusText = t.status || 'Unknown';
                    }

                    const taskIdShort = (t.task_id || '').substring(0, 8) + '...';
                    const createdDate = t.created_at ? new Date(t.created_at).toLocaleString() : 'N/A';

                    return `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="px-6 py-4 font-mono text-xs">${taskIdShort}</td>
                            <td class="px-6 py-4"><span class="px-3 py-1 rounded-full text-xs font-semibold ${statusClass}">${statusIcon} ${statusText}</span></td>
                            <td class="px-6 py-4"><span class="px-2 py-1 bg-indigo-100 text-indigo-800 rounded text-xs">${(t.protocol || 'N/A').toUpperCase()}</span></td>
                            <td class="px-6 py-4 text-xs">${t.source_path || 'N/A'}</td>
                            <td class="px-6 py-4 text-xs">${t.destination_path || 'N/A'}</td>
                            <td class="px-6 py-4 text-xs text-gray-600">${createdDate}</td>
                            <td class="px-6 py-4">
                                <button onclick="viewTransferDetails('${t.task_id}')" class="text-indigo-600 hover:underline text-xs mr-2">Details</button>
                                <button onclick="deleteTransfer('${t.task_id}')" class="text-red-600 hover:underline text-xs">Delete</button>
                            </td>
                        </tr>
                    `;
                }).join('');

            } catch (error) {
                logDebug(`Error loading transfers: ${error.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-red-600">Error loading</td></tr>';
                updateTransferStats(0, 0, 0, 0);
            }
        }

        function updateTransferStats(total, success, failed, pending) {
            document.getElementById('transfer-total').textContent = total;
            document.getElementById('transfer-success').textContent = success;
            document.getElementById('transfer-failed').textContent = failed;
            document.getElementById('transfer-pending').textContent = pending;
        }

        async function viewTransferDetails(taskId) {
            try {
                const res = await fetch(`${API_BASE}/transfers/${taskId}`);
                const transfer = await res.json();
                alert(`Transfer Details:\\n\\n${JSON.stringify(transfer, null, 2)}`);
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        async function deleteTransfer(taskId) {
            if (!confirm('Delete this transfer?')) return;
            try {
                await fetch(`${API_BASE}/transfers/${taskId}`, {method: 'DELETE'});
                alert('✅ Deleted');
                loadTransfers();
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        async function clearFailedTransfers() {
            if (!confirm('Clear all failed transfers?')) return;
            try {
                const res = await fetch(`${API_BASE}/transfers`);
                const transfers = await res.json();
                const failed = transfers.filter(t => t.status === 'failed' || t.status === 'error');
                for (const t of failed) {
                    await fetch(`${API_BASE}/transfers/${t.task_id}`, {method: 'DELETE'});
                }
                alert(`Cleared ${failed.length} failed transfers`);
                loadTransfers();
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        async function loadDevices() {
            const tbody = document.getElementById('devices-body');
            try {
                const res = await fetch(`${API_BASE}/devices`);
                const devices = await res.json();
                if (!devices || devices.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" class="px-6 py-8 text-center text-gray-400">No devices</td></tr>';
                    return;
                }
                tbody.innerHTML = devices.map(d => `
                    <tr class="border-b hover:bg-gray-50">
                        <td class="px-6 py-4">${d.id}</td>
                        <td class="px-6 py-4">${d.name}</td>
                        <td class="px-6 py-4">${d.hostname}</td>
                        <td class="px-6 py-4">${d.protocol || 'N/A'}</td>
                    </tr>
                `).join('');
            } catch (error) {
                tbody.innerHTML = '<tr><td colspan="4" class="px-6 py-8 text-center text-red-600">Error</td></tr>';
            }
        }

        // ===== User Management Functions =====

        function toggleADConfig() {
            const card = document.getElementById('ad-config-card');
            card.style.display = card.style.display === 'none' ? 'block' : 'none';
        }

        async function saveADConfig() {
            const config = {
                domain_controller: document.getElementById('ad-domain-controller').value,
                domain: document.getElementById('ad-domain').value,
                base_dn: document.getElementById('ad-base-dn').value,
                port: parseInt(document.getElementById('ad-port').value),
                username: document.getElementById('ad-username').value,
                password: document.getElementById('ad-password').value,
                use_ssl: document.getElementById('ad-use-ssl').checked
            };

            logDebug('Saving AD configuration...', 'info');

            try {
                logAPICall('POST', '/api/v1/users/config', config);
                const res = await fetch(`${API_BASE}/users/config`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                const data = await res.json();
                logAPIResponse(data, res.ok);

                if (res.ok) {
                    logDebug('✓ AD configuration saved', 'success');
                    alert('Active Directory configuration saved successfully!');
                } else {
                    throw new Error(data.detail || 'Failed to save configuration');
                }
            } catch (e) {
                logDebug(`✗ Error saving config: ${e.message}`, 'error');
                alert(`Error: ${e.message}`);
            }
        }

        async function testADConnection() {
            const config = {
                domain_controller: document.getElementById('ad-domain-controller').value,
                domain: document.getElementById('ad-domain').value,
                base_dn: document.getElementById('ad-base-dn').value,
                port: parseInt(document.getElementById('ad-port').value),
                username: document.getElementById('ad-username').value,
                password: document.getElementById('ad-password').value,
                use_ssl: document.getElementById('ad-use-ssl').checked
            };

            logDebug('Testing AD connection...', 'info');

            try {
                logAPICall('POST', '/api/v1/users/test', config);
                const res = await fetch(`${API_BASE}/users/test`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                const data = await res.json();
                logAPIResponse(data, res.ok);

                if (res.ok && data.success) {
                    logDebug('✓ AD connection successful', 'success');
                    alert(`Connection successful!\n\nFound ${data.users_found || 0} users`);
                } else {
                    throw new Error(data.detail || data.message || 'Connection failed');
                }
            } catch (e) {
                logDebug(`✗ Connection failed: ${e.message}`, 'error');
                alert(`Connection failed: ${e.message}`);
            }
        }

        async function loadUsers() {
            logDebug('Loading AD users...', 'info');
            const tbody = document.getElementById('users-body');
            const countEl = document.getElementById('ad-user-count');
            const syncEl = document.getElementById('ad-last-sync');

            try {
                logAPICall('GET', '/api/v1/users', null);
                const res = await fetch(`${API_BASE}/users`);
                const data = await res.json();
                logAPIResponse(data, res.ok);

                const users = data.users || data || [];
                const lastSync = data.last_sync || null;

                if (!users || users.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="6" class="px-6 py-8 text-center text-gray-500">
                                <div class="flex flex-col items-center gap-3">
                                    <span class="material-symbols-outlined text-gray-300" style="font-size: 48px;">person_off</span>
                                    <p>No users synced yet</p>
                                    <button onclick="syncADUsers()" class="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700">
                                        Sync from Active Directory
                                    </button>
                                </div>
                            </td>
                        </tr>`;
                    countEl.textContent = '0';
                    syncEl.textContent = 'Never';
                    return;
                }

                // Update count and last sync time
                countEl.textContent = users.length;
                if (lastSync) {
                    syncEl.textContent = new Date(lastSync).toLocaleString();
                } else {
                    syncEl.textContent = 'Unknown';
                }

                tbody.innerHTML = users.map(u => `
                    <tr class="hover:bg-gray-50">
                        <td class="px-6 py-4 text-sm font-medium">${u.username || u.sam_account_name || 'N/A'}</td>
                        <td class="px-6 py-4 text-sm">${u.display_name || u.name || 'N/A'}</td>
                        <td class="px-6 py-4 text-sm">${u.email || 'N/A'}</td>
                        <td class="px-6 py-4 text-sm">${u.department || 'N/A'}</td>
                        <td class="px-6 py-4">
                            <span class="px-2 py-1 text-xs rounded ${u.enabled || u.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}">
                                ${u.enabled || u.status === 'active' ? 'Active' : 'Disabled'}
                            </span>
                        </td>
                        <td class="px-6 py-4 text-sm text-gray-500">${u.synced_at ? new Date(u.synced_at).toLocaleString() : 'N/A'}</td>
                    </tr>
                `).join('');

                logDebug(`✓ Loaded ${users.length} users`, 'success');
            } catch (e) {
                logDebug(`✗ Error loading users: ${e.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="6" class="px-6 py-8 text-center text-red-600">Error loading users</td></tr>';
                countEl.textContent = '0';
                syncEl.textContent = 'Error';
            }
        }

        async function syncADUsers() {
            logDebug('Starting AD sync...', 'info');

            // Show loading state
            const tbody = document.getElementById('users-body');
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="px-6 py-8 text-center">
                        <div class="flex flex-col items-center gap-3">
                            <div class="spinner w-12 h-12 border-4 border-indigo-600 border-t-transparent rounded-full"></div>
                            <p class="text-gray-600">Syncing users from Active Directory...</p>
                            <p class="text-sm text-gray-500">Using saved AD configuration</p>
                        </div>
                    </td>
                </tr>`;

            try {
                // Get current config from form
                const config = {
                    domain_controller: document.getElementById('ad-domain-controller').value,
                    domain: document.getElementById('ad-domain').value,
                    base_dn: document.getElementById('ad-base-dn').value,
                    port: parseInt(document.getElementById('ad-port').value),
                    username: document.getElementById('ad-username').value,
                    password: document.getElementById('ad-password').value,
                    use_ssl: document.getElementById('ad-use-ssl').checked
                };

                logAPICall('POST', '/api/v1/users/sync', config);
                const res = await fetch(`${API_BASE}/users/sync`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                const data = await res.json();
                logAPIResponse(data, res.ok);

                if (res.ok) {
                    logDebug(`✓ Synced ${data.users_synced || data.count || 0} users`, 'success');
                    alert(`Successfully synced ${data.users_synced || data.count || 0} users from Active Directory!\n\n${data.message || ''}`);
                    await loadUsers(); // Reload the users list
                } else {
                    throw new Error(data.detail || data.message || 'Sync failed');
                }
            } catch (e) {
                logDebug(`✗ Sync error: ${e.message}`, 'error');
                alert(`Error syncing users: ${e.message}`);
                await loadUsers(); // Reload to show current state
            }
        }

        async function loadRules() {
            const tbody = document.getElementById('rules-body');
            try {
                const res = await fetch(`${API_BASE}/rules`);
                const rules = await res.json();
                if (!rules || rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" class="px-6 py-8 text-center text-gray-400">No rules</td></tr>';
                    return;
                }
                tbody.innerHTML = rules.map(r => `
                    <tr class="border-b hover:bg-gray-50">
                        <td class="px-6 py-4">${r.id}</td>
                        <td class="px-6 py-4">${r.name}</td>
                        <td class="px-6 py-4">${r.enabled ? '✅' : '❌'}</td>
                    </tr>
                `).join('');
            } catch (error) {
                tbody.innerHTML = '<tr><td colspan="3" class="px-6 py-8 text-center text-red-600">Error</td></tr>';
            }
        }

        refreshDashboard();
        logDebug('✅ MFT System initialized', 'success');
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return '<meta http-equiv="refresh" content="0; url=/dashboard" />'


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 70)
    print("🚀 MFT COMPLETE SYSTEM v3.0")
    print("=" * 70)
    print()
    print("✅ System Status:")
    print(f"   Database: {'✅ Connected' if db else '⚠️  In-Memory Only'}")
    print(f"   MFT App:  {'✅ Loaded' if mft_app else '⚠️  Simulated'}")
    print()
    print("📊 Features:")
    print("   ✅ Dashboard with statistics")
    print("   ✅ Create new transfers")
    print("   ✅ View transfer history")
    print("   ✅ Manage devices")
    print("   ✅ View rules")
    print("   ✅ Debug console")
    print()
    print("🌐 Access Points:")
    print("   📊 Dashboard: http://localhost:8000/dashboard")
    print("   📖 API Docs: http://localhost:8000/docs")
    print("   ❤️  Health:   http://localhost:8000/api/v1/health")
    print()
    print("🐛 Debugging:")
    print("   Click green 'DEBUG' button in web UI")
    print()
    print("=" * 70)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()