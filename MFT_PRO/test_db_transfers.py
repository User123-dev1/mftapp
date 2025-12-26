"""Test database file transfers table"""
import sys
import sqlite3
from database import get_database

def test_database():
    print("Testing database initialization...")

    # Initialize database
    db = get_database()
    print("[OK] Database initialized successfully")

    # Check if file_transfers table exists
    conn = sqlite3.connect('mft_database.db')
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_transfers'")
    result = cursor.fetchone()

    if result:
        print("[OK] file_transfers table exists")

        # Get table structure
        cursor.execute("PRAGMA table_info(file_transfers)")
        cols = cursor.fetchall()
        print(f"[OK] Table has {len(cols)} columns:")
        for col in cols:
            print(f"   - {col[1]} ({col[2]})")
    else:
        print("[FAIL] file_transfers table does NOT exist")
        return False

    # Clean up any existing test data
    cursor.execute("DELETE FROM file_transfers WHERE task_id = 'test-123'")
    conn.commit()
    conn.close()

    # Test creating a file transfer
    from database import FileTransfer
    print("\nTesting file transfer creation...")

    transfer = FileTransfer(
        task_id="test-123",
        source_path="/test/source.txt",
        destination_path="/test/dest.txt",
        protocol="sftp",
        status="pending",
        file_size=1024
    )

    transfer_id = db.create_file_transfer(transfer)
    print(f"[OK] Created file transfer with ID: {transfer_id}")

    # Test retrieving the transfer
    retrieved = db.get_file_transfer("test-123")
    if retrieved:
        print(f"[OK] Retrieved transfer: {retrieved.task_id} - {retrieved.status}")
    else:
        print("[FAIL] Failed to retrieve transfer")
        return False

    # Test updating the transfer
    db.update_file_transfer("test-123", status="completed", file_size=2048)
    updated = db.get_file_transfer("test-123")
    if updated and updated.status == "completed" and updated.file_size == 2048:
        print(f"[OK] Updated transfer: status={updated.status}, size={updated.file_size}")
    else:
        print("[FAIL] Failed to update transfer")
        return False

    # Test statistics
    stats = db.get_transfer_statistics(hours=24)
    print(f"\n[OK] Transfer statistics: {stats}")

    print("\n[SUCCESS] All tests passed!")
    return True

if __name__ == "__main__":
    success = test_database()
    sys.exit(0 if success else 1)
