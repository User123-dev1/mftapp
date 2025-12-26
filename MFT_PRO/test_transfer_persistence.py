"""
Test Transfer Persistence After Restart
Verifies that transfers are saved to database and loaded back on restart
"""
import asyncio
import logging
import os
import sys
import tempfile
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from database import get_database, FileTransfer
from mft_application import MFTApplication, TransferConfig, TransferProtocol
from state_manager import StateManager

async def test_persistence():
    """Test that transfers persist across restarts"""
    logger.info("=" * 80)
    logger.info("TESTING TRANSFER PERSISTENCE AFTER RESTART")
    logger.info("=" * 80)

    # Create temp directory for test
    test_dir = tempfile.mkdtemp(prefix="mft_persist_test_")
    state_dir = os.path.join(test_dir, "state")

    # Create test files
    source_file = os.path.join(test_dir, "test_source.txt")
    dest_file = os.path.join(test_dir, "test_dest.txt")

    with open(source_file, 'w') as f:
        f.write("Test content for persistence test\n")

    logger.info(f"Test directory: {test_dir}")

    # STEP 1: Create transfers and save to database
    logger.info("\n[STEP 1] Creating MFT Application (First Instance)...")
    state_manager1 = StateManager(state_dir)
    mft_app1 = MFTApplication(state_manager=state_manager1)

    logger.info("[STEP 1] Executing test transfer...")
    config = TransferConfig(
        protocol=TransferProtocol.UNC,
        host="localhost",
        port=22,
        encryption_enabled=False
    )

    task_id = await mft_app1.transfer_file(
        source_path=source_file,
        destination_path=dest_file,
        config=config
    )

    logger.info(f"[STEP 1] Transfer completed: {task_id}")

    # Verify it's in memory
    stats1 = mft_app1.get_statistics()
    logger.info(f"[STEP 1] Stats after transfer:")
    logger.info(f"  Active: {stats1['active_transfers']}")
    logger.info(f"  Completed: {stats1['completed_transfers']}")
    logger.info(f"  Failed: {stats1['failed_transfers']}")

    # Verify it's in database
    db = get_database()
    db_transfer = db.get_file_transfer(task_id)
    if db_transfer:
        logger.info(f"[OK] Transfer found in database: {db_transfer.task_id}")
        logger.info(f"     Status: {db_transfer.status}")
        logger.info(f"     File size: {db_transfer.file_size} bytes")
    else:
        logger.error(f"[FAIL] Transfer NOT found in database!")
        return False

    # STEP 2: Simulate restart by creating NEW instance
    logger.info("\n[STEP 2] Simulating restart - Creating NEW MFT Application...")
    del mft_app1  # Delete first instance
    del state_manager1

    state_manager2 = StateManager(state_dir)
    mft_app2 = MFTApplication(state_manager=state_manager2)

    logger.info("[STEP 2] MFT Application restarted")

    # Verify transfers loaded from database
    stats2 = mft_app2.get_statistics()
    logger.info(f"[STEP 2] Stats after restart:")
    logger.info(f"  Active: {stats2['active_transfers']}")
    logger.info(f"  Completed: {stats2['completed_transfers']}")
    logger.info(f"  Failed: {stats2['failed_transfers']}")

    if stats2['completed_transfers'] > 0:
        logger.info(f"[OK] Successfully loaded {stats2['completed_transfers']} completed transfers from database")

        # Verify the actual transfer details
        transfer_status = mft_app2.get_transfer_status(task_id)
        if transfer_status:
            logger.info(f"[OK] Transfer details retrieved:")
            logger.info(f"     Task ID: {transfer_status['task_id']}")
            logger.info(f"     Status: {transfer_status['status']}")
            logger.info(f"     Source: {transfer_status['source_path']}")
            logger.info(f"     Destination: {transfer_status['destination_path']}")
            logger.info(f"     File size: {transfer_status['file_size']} bytes")
        else:
            logger.error(f"[FAIL] Could not retrieve transfer details for {task_id}")
            return False
    else:
        logger.error(f"[FAIL] No transfers loaded from database after restart!")
        return False

    # STEP 3: Verify database has the transfer
    logger.info("\n[STEP 3] Verifying database directly...")
    all_transfers = db.get_file_transfers(limit=100)
    logger.info(f"[OK] Database contains {len(all_transfers)} total transfers")

    completed_transfers = db.get_file_transfers(status='completed', limit=100)
    logger.info(f"[OK] Database contains {len(completed_transfers)} completed transfers")

    # Cleanup
    logger.info("\n[CLEANUP] Removing test directory...")
    import shutil
    shutil.rmtree(test_dir, ignore_errors=True)

    # Success!
    logger.info("\n" + "=" * 80)
    logger.info("[SUCCESS] TRANSFER PERSISTENCE TEST PASSED!")
    logger.info("=" * 80)
    logger.info("Transfers are properly saved to database and loaded on restart!")
    logger.info("")

    return True

async def main():
    """Main entry point"""
    try:
        success = await test_persistence()
        return 0 if success else 1
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
