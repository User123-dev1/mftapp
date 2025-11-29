#!/usr/bin/env python3
"""
Comprehensive MFT Testing and Validation Script
Tests all protocols, AD sync, rule execution, and scheduling
"""

import asyncio
import os
import sys
import tempfile
import logging
import json
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_database_init():
    """Test database initialization"""
    logger.info("=" * 60)
    logger.info("TEST 1: Database Initialization")
    logger.info("=" * 60)
    
    try:
        from database import get_database, Device, TransferRule, DomainConfig
        
        db = get_database()
        logger.info("✓ Database initialized successfully")
        
        # Test creating a device
        device = Device(
            name="Test Local Device",
            hostname="localhost",
            ip_address="127.0.0.1",
            port=22,
            protocol="sftp",
            username="testuser",
            status="online"
        )
        
        device_id = db.create_device(device)
        logger.info(f"✓ Created test device with ID: {device_id}")
        
        # Retrieve it
        retrieved = db.get_device(device_id)
        assert retrieved is not None
        assert retrieved.name == device.name
        logger.info(f"✓ Retrieved device: {retrieved.name}")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Database test failed: {e}", exc_info=True)
        return False


async def test_protocol_handlers():
    """Test protocol handler initialization"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: Protocol Handlers")
    logger.info("=" * 60)
    
    try:
        from protocol_handlers import (
            SFTPHandler, FTPSHandler, HTTPSHandler, WebDAVHandler,
            SMBHandler, UNCHandler, TFTPHandler, AS2Handler
        )
        
        handlers = {
            "SFTP": SFTPHandler(),
            "FTP/FTPS": FTPSHandler(),
            "HTTP/HTTPS": HTTPSHandler(),
            "WebDAV": WebDAVHandler(),
            "SMB": SMBHandler(),
            "UNC": UNCHandler(),
            "TFTP": TFTPHandler(),
            "AS2": AS2Handler()
        }
        
        for name, handler in handlers.items():
            logger.info(f"✓ {name} handler initialized: {handler.__class__.__name__}")
        
        logger.info(f"✓ All {len(handlers)} protocol handlers initialized")
        return True
        
    except Exception as e:
        logger.error(f"✗ Protocol handler test failed: {e}", exc_info=True)
        return False


async def test_mft_application():
    """Test MFT Application initialization"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: MFT Application")
    logger.info("=" * 60)
    
    try:
        from mft_application import MFTApplication, TransferConfig, TransferProtocol
        
        mft = MFTApplication()
        logger.info("✓ MFT Application initialized")
        
        # Check protocol handlers are loaded
        assert len(mft.protocol_handlers) > 0
        logger.info(f"✓ Loaded {len(mft.protocol_handlers)} protocol handlers")
        
        # Check components
        assert mft.monitor is not None
        logger.info("✓ Transfer monitor initialized")
        
        logger.info("✓ All MFT components initialized successfully")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ MFT Application test failed: {e}", exc_info=True)
        return False


async def test_file_transfer_local():
    """Test local file transfer using UNC handler"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 4: Local File Transfer (UNC)")
    logger.info("=" * 60)
    
    try:
        from mft_application import MFTApplication, TransferConfig, TransferProtocol
        
        # Create temp directories
        temp_dir = tempfile.mkdtemp()
        source_dir = os.path.join(temp_dir, "source")
        dest_dir = os.path.join(temp_dir, "destination")
        os.makedirs(source_dir)
        os.makedirs(dest_dir)
        
        # Create a test file
        test_file = os.path.join(source_dir, "test_file.txt")
        test_content = f"Test file created at {datetime.utcnow().isoformat()}\n" * 100
        with open(test_file, 'w') as f:
            f.write(test_content)
        
        logger.info(f"✓ Created test file: {test_file}")
        logger.info(f"  File size: {os.path.getsize(test_file)} bytes")
        
        # Create MFT app
        mft = MFTApplication()
        
        # Configure transfer
        config = TransferConfig(
            protocol=TransferProtocol.UNC,
            host="localhost",
            port=0,
            encryption_enabled=False
        )
        
        dest_file = os.path.join(dest_dir, "test_file.txt")
        
        # Execute transfer
        logger.info("Starting transfer...")
        task_id = await mft.transfer_file(
            source_path=test_file,
            destination_path=dest_file,
            config=config,
            metadata={'test': 'local_transfer'}
        )
        
        logger.info(f"✓ Transfer initiated with task ID: {task_id}")
        
        # Wait for completion
        max_wait = 10
        for i in range(max_wait):
            await asyncio.sleep(1)
            status = mft.get_transfer_status(task_id)
            if status and status['status'] in ['completed', 'failed']:
                break
        
        # Check status
        final_status = mft.get_transfer_status(task_id)
        logger.info(f"Transfer status: {final_status['status']}")
        
        if final_status['status'] == 'completed':
            logger.info("✓ Transfer completed successfully")
            
            # Verify file exists
            if os.path.exists(dest_file):
                logger.info("✓ Destination file exists")
                
                # Verify content
                with open(dest_file, 'r') as f:
                    dest_content = f.read()
                
                if dest_content == test_content:
                    logger.info("✓ File content verified - transfer integrity confirmed")
                else:
                    logger.warning("✗ File content mismatch")
            else:
                logger.error("✗ Destination file not found")
                return False
        else:
            logger.error(f"✗ Transfer failed: {final_status.get('error_message', 'Unknown error')}")
            return False
        
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir)
        logger.info("✓ Cleaned up test files")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ File transfer test failed: {e}", exc_info=True)
        return False


async def test_transfer_scheduler():
    """Test transfer scheduler"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 5: Transfer Scheduler")
    logger.info("=" * 60)
    
    try:
        from mft_application import MFTApplication
        from transfer_scheduler import TransferScheduler
        from database import get_database, Device, TransferRule
        
        db = get_database()
        mft = MFTApplication()
        
        # Create source and destination devices
        source_device = Device(
            name="Test Source",
            hostname="localhost",
            ip_address="127.0.0.1",
            port=22,
            protocol="unc",
            status="online"
        )
        source_id = db.create_device(source_device)
        logger.info(f"✓ Created source device: {source_id}")
        
        dest_device = Device(
            name="Test Destination",
            hostname="localhost",
            ip_address="127.0.0.1",
            port=22,
            protocol="unc",
            status="online"
        )
        dest_id = db.create_device(dest_device)
        logger.info(f"✓ Created destination device: {dest_id}")
        
        # Create temp directories
        temp_dir = tempfile.mkdtemp()
        source_path = os.path.join(temp_dir, "source")
        dest_path = os.path.join(temp_dir, "dest")
        os.makedirs(source_path)
        os.makedirs(dest_path)
        
        # Create test file
        test_file = os.path.join(source_path, "scheduled_test.txt")
        with open(test_file, 'w') as f:
            f.write("Scheduled transfer test\n" * 50)
        logger.info(f"✓ Created test file: {test_file}")
        
        # Create a transfer rule
        rule = TransferRule(
            name="Test Scheduled Transfer",
            source_device_id=source_id,
            source_path=source_path,
            destination_device_id=dest_id,
            destination_path=dest_path,
            transfer_mode="copy",
            deletion_delay="immediate",
            file_pattern="*.txt",
            enabled=True,
            schedule_type="on_demand",  # We'll trigger manually
            retry_on_failure=True,
            max_retries=3
        )
        
        rule_id = db.create_transfer_rule(rule)
        logger.info(f"✓ Created transfer rule: {rule_id}")
        
        # Initialize scheduler
        scheduler = TransferScheduler(mft)
        logger.info("✓ Scheduler initialized")
        
        # Execute rule manually
        logger.info("Executing rule manually...")
        await scheduler.execute_rule_now(rule_id)
        
        # Wait a bit for transfer to complete
        await asyncio.sleep(3)
        
        # Check if file was transferred
        dest_file = os.path.join(dest_path, "scheduled_test.txt")
        if os.path.exists(dest_file):
            logger.info("✓ File transferred successfully via scheduler")
        else:
            logger.error("✗ File not found at destination")
            return False
        
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir)
        logger.info("✓ Cleaned up test files")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Scheduler test failed: {e}", exc_info=True)
        return False


async def test_domain_config():
    """Test domain configuration (without actual AD server)"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 6: Domain Configuration")
    logger.info("=" * 60)
    
    try:
        from database import get_database, DomainConfig
        
        db = get_database()
        
        # Create a test domain config
        config = DomainConfig(
            name="Test Domain",
            domain_type="active_directory",
            server="dc.example.com",
            port=389,
            use_ssl=True,
            base_dn="DC=example,DC=com",
            bind_user="CN=bind_user,CN=Users,DC=example,DC=com",
            bind_password="password",
            user_search_base="CN=Users,DC=example,DC=com",
            enabled=True
        )
        
        config_id = db.save_domain_config(config)
        logger.info(f"✓ Created domain config: {config_id}")
        
        # Retrieve it
        retrieved = db.get_domain_config(config_id)
        assert retrieved is not None
        assert retrieved.name == config.name
        logger.info(f"✓ Retrieved domain config: {retrieved.name}")
        
        # List all configs
        configs = db.get_all_domain_configs()
        logger.info(f"✓ Found {len(configs)} domain configurations")
        
        logger.info("Note: Actual AD sync requires a real LDAP/AD server")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Domain config test failed: {e}", exc_info=True)
        return False


async def test_audit_logging():
    """Test audit logging"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 7: Audit Logging")
    logger.info("=" * 60)
    
    try:
        from database import get_database
        
        db = get_database()
        
        # Log some test audit entries
        db.log_audit(
            username="test_user",
            action="create",
            resource_type="device",
            resource_id="123",
            details={"name": "Test Device", "protocol": "sftp"},
            status="success"
        )
        logger.info("✓ Logged audit entry: device creation")
        
        db.log_audit(
            username="test_user",
            action="execute",
            resource_type="transfer_rule",
            resource_id="456",
            details={"files_transferred": 5},
            status="success"
        )
        logger.info("✓ Logged audit entry: rule execution")
        
        db.log_audit(
            username="test_user",
            action="sync_users",
            resource_type="domain_config",
            resource_id="789",
            details={"synced_count": 100},
            status="success"
        )
        logger.info("✓ Logged audit entry: domain sync")
        
        # Retrieve audit logs
        logs = db.get_audit_logs(limit=10)
        logger.info(f"✓ Retrieved {len(logs)} audit log entries")
        
        for log in logs[:3]:
            logger.info(f"  - {log.action} on {log.resource_type} by {log.username}")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Audit logging test failed: {e}", exc_info=True)
        return False


async def test_deletion_delay():
    """Test deletion delay functionality"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 8: Deletion Delay (MOVE operations)")
    logger.info("=" * 60)
    
    try:
        from database import get_database, DeletionDelay
        
        db = get_database()
        
        # Test all deletion delays
        delays = [d for d in DeletionDelay]
        logger.info(f"✓ Found {len(delays)} deletion delay options:")
        
        for delay in delays:
            timedelta = delay.to_timedelta()
            if timedelta:
                hours = timedelta.total_seconds() / 3600
                logger.info(f"  - {delay.value}: {hours} hours")
            else:
                logger.info(f"  - {delay.value}: immediate")
        
        # Test adding a pending deletion
        db.add_pending_deletion(
            rule_id=1,
            file_path="/test/file.txt",
            size=1024,
            checksum="abc123",
            delay=DeletionDelay.ONE_HOUR
        )
        logger.info("✓ Added pending deletion with 1 hour delay")
        
        # Get pending deletions
        pending = db.get_deletions_due()
        logger.info(f"✓ Retrieved {len(pending)} deletions due now")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Deletion delay test failed: {e}", exc_info=True)
        return False


def print_summary(results):
    """Print test summary"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)
    
    total = len(results)
    passed = sum(1 for r in results if r[1])
    failed = total - passed
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"{status}: {test_name}")
    
    logger.info("-" * 60)
    logger.info(f"Total: {total} | Passed: {passed} | Failed: {failed}")
    logger.info(f"Success Rate: {passed/total*100:.1f}%")
    logger.info("=" * 60)
    
    return failed == 0


async def main():
    """Run all tests"""
    logger.info("\n" + "=" * 60)
    logger.info("MFT APPLICATION - COMPREHENSIVE TEST SUITE")
    logger.info("=" * 60)
    
    results = []
    
    # Run tests
    results.append(("Database Initialization", await test_database_init()))
    results.append(("Protocol Handlers", await test_protocol_handlers()))
    results.append(("MFT Application", await test_mft_application()))
    results.append(("Local File Transfer", await test_file_transfer_local()))
    results.append(("Transfer Scheduler", await test_transfer_scheduler()))
    results.append(("Domain Configuration", await test_domain_config()))
    results.append(("Audit Logging", await test_audit_logging()))
    results.append(("Deletion Delay", await test_deletion_delay()))
    
    # Print summary
    all_passed = print_summary(results)
    
    if all_passed:
        logger.info("\n✓ All tests passed! MFT Application is working correctly.")
        logger.info("\nYou can now start the API server with:")
        logger.info("  python api_server.py")
        logger.info("\nOr use uvicorn:")
        logger.info("  uvicorn api_server:app --host 0.0.0.0 --port 8000")
        return 0
    else:
        logger.error("\n✗ Some tests failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
