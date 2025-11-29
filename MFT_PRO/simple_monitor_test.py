"""
SIMPLE FILE MONITOR TEST
No dependencies on MFT application - pure local file copy
"""

import os
import time
import shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Test folders
SOURCE = "C:\\temp\\mft_test_incoming"
DEST = "C:\\temp\\mft_test_outgoing"

# Create folders
os.makedirs(SOURCE, exist_ok=True)
os.makedirs(DEST, exist_ok=True)

print("="*80)
print("📁 SIMPLE FILE MONITOR TEST")
print("="*80)
print(f"Source: {SOURCE}")
print(f"Dest: {DEST}")
print()


class SimpleFileHandler(FileSystemEventHandler):
    """Simple file handler - just copy files"""

    def on_created(self, event):
        if event.is_directory:
            return

        source_file = event.src_path
        filename = os.path.basename(source_file)
        dest_file = os.path.join(DEST, filename)

        # Wait for file to be stable
        print(f"\n📄 New file detected: {filename}")
        print(f"   Waiting 3 seconds for file stability...")
        time.sleep(3)

        # Check file still exists and is stable
        if not os.path.exists(source_file):
            print(f"   ❌ File disappeared: {filename}")
            return

        # Get file size
        size1 = os.path.getsize(source_file)
        time.sleep(1)

        if not os.path.exists(source_file):
            print(f"   ❌ File disappeared: {filename}")
            return

        size2 = os.path.getsize(source_file)

        if size1 != size2:
            print(f"   ⚠️ File still being written: {filename}")
            return

        # Copy file
        try:
            print(f"\n{'='*80}")
            print(f"🚀 COPYING FILE")
            print(f"{'='*80}")
            print(f"   Source: {source_file}")
            print(f"   Dest: {dest_file}")
            print(f"   Size: {size1:,} bytes")

            shutil.copy2(source_file, dest_file)

            # Verify
            if os.path.exists(dest_file):
                dest_size = os.path.getsize(dest_file)
                print(f"   ✅ File copied successfully!")
                print(f"   Dest size: {dest_size:,} bytes")

                if size1 == dest_size:
                    print(f"\n{'='*80}")
                    print(f"✅ COPY SUCCESSFUL!")
                    print(f"{'='*80}\n")
                else:
                    print(f"   ❌ Size mismatch: {size1} vs {dest_size}")
            else:
                print(f"   ❌ Destination file not created")

        except Exception as e:
            print(f"   ❌ Copy failed: {e}")
            import traceback
            traceback.print_exc()


# Create event handler
event_handler = SimpleFileHandler()

# Create observer
observer = Observer()
observer.schedule(event_handler, SOURCE, recursive=False)
observer.start()

print("✅ Monitoring started!")
print()
print("="*80)
print("🧪 TEST INSTRUCTIONS")
print("="*80)
print()
print("1. Open another command prompt")
print()
print("2. Copy a file to the source folder:")
print(f"   echo Test content > {SOURCE}\\test.txt")
print()
print("   OR")
print()
print(f"   copy C:\\Windows\\System32\\drivers\\etc\\hosts {SOURCE}\\")
print()
print("3. Watch this window for the copy operation!")
print()
print("4. Check destination folder:")
print(f"   dir {DEST}")
print()
print("5. Press CTRL+C to stop")
print()
print("="*80)
print("👀 MONITORING... (waiting for files)")
print("="*80)

try:
    while True:
        time.sleep(5)

        # Show status every 5 seconds
        source_files = len([f for f in os.listdir(SOURCE) if os.path.isfile(os.path.join(SOURCE, f))])
        dest_files = len([f for f in os.listdir(DEST) if os.path.isfile(os.path.join(DEST, f))])

        print(f"📊 Status: Source={source_files} files, Dest={dest_files} files")

except KeyboardInterrupt:
    print("\n\n" + "="*80)
    print("🛑 STOPPING MONITOR")
    print("="*80)
    observer.stop()
    observer.join()

    # Final count
    source_files = os.listdir(SOURCE)
    dest_files = os.listdir(DEST)

    print(f"\n📊 Final Status:")
    print(f"   Source folder: {len(source_files)} files")
    print(f"   Dest folder: {len(dest_files)} files")

    print("\n✅ Test complete!")
    print("="*80)