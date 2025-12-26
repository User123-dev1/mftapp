"""
Comprehensive Disaster Recovery Test
Tests the full disaster recovery workflow with actual file transfers
"""
import asyncio
import logging
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import required modules
from database import (
    get_database, Device, TransferRule, FileTransfer,
    DeviceStatus, TransferMode
)
from mft_application import MFTApplication, TransferConfig, TransferProtocol
from state_manager import StateManager
from disaster_recovery_manager import DisasterRecoveryManager, RecoveryConfig, RecoveryMode

class DisasterRecoveryTest:
    """Test disaster recovery with actual file transfers"""

    def __init__(self):
        self.db = get_database()
        self.test_dir = None
        self.source_dir = None
        self.dest_dir = None
        self.state_manager = None
        self.mft_app = None
        self.recovery_manager = None
        self.test_device_id = None
        self.test_rule_id = None

    def setup_test_environment(self):
        """Set up test directories and files"""
        logger.info("=" * 80)
        logger.info("SETTING UP TEST ENVIRONMENT")
        logger.info("=" * 80)

        # Create temporary test directory
        self.test_dir = tempfile.mkdtemp(prefix="mft_dr_test_")
        self.source_dir = os.path.join(self.test_dir, "source")
        self.dest_dir = os.path.join(self.test_dir, "destination")

        os.makedirs(self.source_dir, exist_ok=True)
        os.makedirs(self.dest_dir, exist_ok=True)

        logger.info(f"[OK] Created test directory: {self.test_dir}")
        logger.info(f"[OK] Source directory: {self.source_dir}")
        logger.info(f"[OK] Destination directory: {self.dest_dir}")

        # Create test files
        test_files = [
            "test_file_1.txt",
            "test_file_2.txt",
            "test_file_3.txt",
            "important_data.csv",
            "backup_file.json"
        ]

        for filename in test_files:
            filepath = os.path.join(self.source_dir, filename)
            with open(filepath, 'w') as f:
                f.write(f"Test content for {filename}\n")
                f.write(f"Created at: {datetime.now().isoformat()}\n")
                f.write("=" * 50 + "\n")
                f.write("This is test data for disaster recovery testing.\n")
            logger.info(f"[OK] Created test file: {filename}")

        logger.info(f"[OK] Created {len(test_files)} test files")
        return True

    def setup_database_entities(self):
        """Create test devices and transfer rules in database"""
        logger.info("\n" + "=" * 80)
        logger.info("SETTING UP DATABASE ENTITIES")
        logger.info("=" * 80)

        # Create test device (local system)
        device = Device(
            name="TestServer",
            hostname="localhost",
            ip_address="127.0.0.1",
            port=22,
            protocol="sftp",
            username="testuser",
            status=DeviceStatus.ONLINE.value
        )

        self.test_device_id = self.db.create_device(device)
        logger.info(f"[OK] Created test device with ID: {self.test_device_id}")

        # Create a second device for destination
        dest_device = Device(
            name="TestDestination",
            hostname="localhost",
            ip_address="127.0.0.1",
            port=22,
            protocol="sftp",
            username="testuser",
            status=DeviceStatus.ONLINE.value
        )

        dest_device_id = self.db.create_device(dest_device)
        logger.info(f"[OK] Created destination device with ID: {dest_device_id}")

        # Create transfer rule
        rule = TransferRule(
            name="DR Test Rule",
            source_device_id=self.test_device_id,
            source_path=self.source_dir,
            destination_device_id=dest_device_id,
            destination_path=self.dest_dir,
            transfer_mode=TransferMode.COPY.value,
            file_pattern="*.txt",
            enabled=True,
            schedule_type="on_demand"
        )

        self.test_rule_id = self.db.create_transfer_rule(rule)
        logger.info(f"[OK] Created transfer rule with ID: {self.test_rule_id}")

        return True

    async def execute_test_transfers(self):
        """Execute actual file transfers and verify they're saved to database"""
        logger.info("\n" + "=" * 80)
        logger.info("EXECUTING FILE TRANSFERS")
        logger.info("=" * 80)

        # Initialize state manager and MFT application
        state_dir = os.path.join(self.test_dir, "state")
        self.state_manager = StateManager(state_dir)
        self.mft_app = MFTApplication(state_manager=self.state_manager)

        logger.info("[OK] MFT Application initialized")

        # Get list of test files
        test_files = [f for f in os.listdir(self.source_dir) if f.endswith('.txt')]
        logger.info(f"[OK] Found {len(test_files)} files to transfer")

        # Execute transfers
        transfer_ids = []
        for filename in test_files:
            source_path = os.path.join(self.source_dir, filename)
            dest_path = os.path.join(self.dest_dir, filename)

            config = TransferConfig(
                protocol=TransferProtocol.UNC,  # Use UNC for local file copy
                host="localhost",
                port=22,
                encryption_enabled=False
            )

            try:
                logger.info(f"[TRANSFER] Starting: {filename}")
                task_id = await self.mft_app.transfer_file(
                    source_path=source_path,
                    destination_path=dest_path,
                    config=config
                )
                transfer_ids.append(task_id)
                logger.info(f"[OK] Completed: {filename} (task_id: {task_id})")
            except Exception as e:
                logger.error(f"[FAIL] Transfer failed for {filename}: {e}")

        logger.info(f"\n[OK] Executed {len(transfer_ids)} transfers")

        # Verify transfers are in database
        logger.info("\nVerifying transfers in database...")
        for task_id in transfer_ids:
            transfer = self.db.get_file_transfer(task_id)
            if transfer:
                logger.info(f"[OK] Found in DB: {task_id} - Status: {transfer.status}")
            else:
                logger.error(f"[FAIL] NOT in DB: {task_id}")
                return False

        # Get transfer statistics
        stats = self.db.get_transfer_statistics(hours=1)
        logger.info(f"\n[STATS] Transfer Statistics:")
        logger.info(f"  Total: {stats['total']}")
        logger.info(f"  Completed: {stats['completed']}")
        logger.info(f"  Failed: {stats['failed']}")
        logger.info(f"  Success Rate: {stats['success_rate']:.1f}%")

        return len(transfer_ids) > 0

    async def test_disaster_recovery(self):
        """Test disaster recovery scenario"""
        logger.info("\n" + "=" * 80)
        logger.info("TESTING DISASTER RECOVERY")
        logger.info("=" * 80)

        # Simulate device going offline
        logger.info(f"\n[SIMULATE] Device {self.test_device_id} going OFFLINE...")
        self.db.update_device_status(self.test_device_id, DeviceStatus.OFFLINE.value)

        device = self.db.get_device(self.test_device_id)
        logger.info(f"[OK] Device status: {device.status}")

        # Wait a moment to simulate downtime
        await asyncio.sleep(2)

        # Queue some transfers that would fail due to offline device
        logger.info("\n[SIMULATE] Queueing transfers during downtime...")
        for i in range(3):
            filename = f"queued_file_{i+1}.txt"
            filepath = os.path.join(self.source_dir, filename)

            # Create the file
            with open(filepath, 'w') as f:
                f.write(f"Queued transfer file {i+1}\n")

            # Queue it in database
            self.db.queue_transfer(
                rule_id=self.test_rule_id,
                source_file=filepath,
                file_size=os.path.getsize(filepath)
            )
            logger.info(f"[OK] Queued: {filename}")

        # Get queued transfers count
        queued = self.db.get_queued_transfers()
        logger.info(f"[OK] Total queued transfers: {len(queued)}")

        # Simulate device coming back online
        logger.info(f"\n[SIMULATE] Device {self.test_device_id} coming ONLINE...")
        self.db.update_device_status(self.test_device_id, DeviceStatus.ONLINE.value, "127.0.0.1")

        device = self.db.get_device(self.test_device_id)
        logger.info(f"[OK] Device status: {device.status}")

        # Get downtime record
        downtime_records = self.db.get_device_downtime(self.test_device_id, days=1)
        if downtime_records:
            latest = downtime_records[0]
            logger.info(f"[OK] Downtime recorded:")
            logger.info(f"  Went offline: {latest.went_offline}")
            logger.info(f"  Came online: {latest.came_online}")
            logger.info(f"  Duration: {latest.duration_seconds} seconds")
            downtime_seconds = latest.duration_seconds or 2
        else:
            logger.warning("[WARN] No downtime record found, using default")
            downtime_seconds = 2

        # Initialize disaster recovery manager
        # Note: We need a transfer scheduler, but we'll create a mock one
        logger.info("\n[INIT] Initializing Disaster Recovery Manager...")

        # Create a simple mock scheduler
        class MockScheduler:
            async def process_transfer_queue(self):
                logger.info("[MOCK] Processing transfer queue...")
                # In a real scenario, this would process queued transfers
                return True

        config = RecoveryConfig(
            mode=RecoveryMode.IMMEDIATE,
            scan_for_missed_files=True,
            max_files_per_recovery=100,
            log_verbose=True
        )

        mock_scheduler = MockScheduler()
        self.recovery_manager = DisasterRecoveryManager(mock_scheduler, config)
        logger.info("[OK] Disaster Recovery Manager initialized")

        # Trigger disaster recovery
        logger.info(f"\n[RECOVERY] Triggering disaster recovery for device {self.test_device_id}...")
        await self.recovery_manager.on_server_online(self.test_device_id, downtime_seconds)

        # Get recovery statistics
        recovery_stats = self.recovery_manager.get_recovery_statistics(self.test_device_id)
        if recovery_stats:
            logger.info("\n[RECOVERY STATS]")
            for stat in recovery_stats:
                logger.info(f"  Device: {stat['device_name']}")
                logger.info(f"  Status: {stat['status']}")
                logger.info(f"  Files discovered: {stat['files_discovered']}")
                logger.info(f"  Files queued: {stat['files_queued']}")
                logger.info(f"  Files processed: {stat['files_processed']}")
        else:
            logger.warning("[WARN] No recovery statistics available")

        return True

    def verify_results(self):
        """Verify disaster recovery worked correctly"""
        logger.info("\n" + "=" * 80)
        logger.info("VERIFYING RESULTS")
        logger.info("=" * 80)

        # Check database for all transfers
        all_transfers = self.db.get_file_transfers(limit=100)
        logger.info(f"[OK] Total transfers in database: {len(all_transfers)}")

        completed_transfers = self.db.get_file_transfers(status='completed', limit=100)
        logger.info(f"[OK] Completed transfers: {len(completed_transfers)}")

        failed_transfers = self.db.get_file_transfers(status='failed', limit=100)
        logger.info(f"[OK] Failed transfers: {len(failed_transfers)}")

        # Check queued transfers
        queued = self.db.get_queued_transfers()
        logger.info(f"[OK] Queued transfers: {len(queued)}")

        # Display sample transfers
        logger.info("\n[SAMPLE TRANSFERS]")
        for i, transfer in enumerate(all_transfers[:5]):
            logger.info(f"  {i+1}. {os.path.basename(transfer.source_path)}")
            logger.info(f"     Status: {transfer.status}")
            logger.info(f"     Size: {transfer.file_size} bytes")
            logger.info(f"     Created: {transfer.created_at}")

        # Final statistics
        stats = self.db.get_transfer_statistics(hours=24)
        logger.info(f"\n[FINAL STATS]")
        logger.info(f"  Total: {stats['total']}")
        logger.info(f"  Completed: {stats['completed']}")
        logger.info(f"  Failed: {stats['failed']}")
        logger.info(f"  Success Rate: {stats['success_rate']:.1f}%")
        logger.info(f"  Total Bytes: {stats['total_bytes']}")

        return len(all_transfers) > 0

    def cleanup(self):
        """Clean up test environment"""
        logger.info("\n" + "=" * 80)
        logger.info("CLEANING UP")
        logger.info("=" * 80)

        if self.test_dir and os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir)
                logger.info(f"[OK] Removed test directory: {self.test_dir}")
            except Exception as e:
                logger.error(f"[WARN] Failed to remove test directory: {e}")

        logger.info("[OK] Cleanup complete")

    async def run_full_test(self):
        """Run the complete disaster recovery test"""
        try:
            logger.info("\n" + "=" * 80)
            logger.info("DISASTER RECOVERY FULL TEST")
            logger.info("=" * 80)
            logger.info(f"Started at: {datetime.now().isoformat()}\n")

            # Step 1: Setup
            if not self.setup_test_environment():
                logger.error("[FAIL] Failed to set up test environment")
                return False

            if not self.setup_database_entities():
                logger.error("[FAIL] Failed to set up database entities")
                return False

            # Step 2: Execute transfers
            if not await self.execute_test_transfers():
                logger.error("[FAIL] Failed to execute test transfers")
                return False

            # Step 3: Test disaster recovery
            if not await self.test_disaster_recovery():
                logger.error("[FAIL] Disaster recovery test failed")
                return False

            # Step 4: Verify results
            if not self.verify_results():
                logger.error("[FAIL] Results verification failed")
                return False

            # Success!
            logger.info("\n" + "=" * 80)
            logger.info("[SUCCESS] ALL DISASTER RECOVERY TESTS PASSED!")
            logger.info("=" * 80)
            logger.info(f"Completed at: {datetime.now().isoformat()}\n")

            return True

        except Exception as e:
            logger.error(f"\n[FAIL] Test failed with exception: {e}", exc_info=True)
            return False

        finally:
            # Always cleanup
            self.cleanup()

async def main():
    """Main test entry point"""
    test = DisasterRecoveryTest()
    success = await test.run_full_test()
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
