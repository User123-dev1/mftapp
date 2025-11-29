#!/usr/bin/env python3
"""
Quick Fix Script - Identifies the problem and applies the fix
"""

import os
import glob

print("=" * 80)
print("🔍 MFT TRANSFER FIX - DIAGNOSTIC & REPAIR")
print("=" * 80)

# Find all Python files that might be the server
print("\n1️⃣ Finding server files...")
server_files = []
for pattern in ['mft_system*.py', 'api_server*.py', '*_server.py']:
    files = glob.glob(pattern)
    server_files.extend(files)

if not server_files:
    print("❌ No server files found!")
    print("   Looking for: mft_system*.py, api_server*.py, *_server.py")
    print("\n💡 Solution:")
    print("   1. Download mft_system_complete.py")
    print("   2. Save it in your project directory")
    print("   3. Run: python mft_system_complete.py")
else:
    print(f"✅ Found {len(server_files)} server file(s):")
    for f in server_files:
        print(f"   - {f}")

# Check each file for the await issue
print("\n2️⃣ Checking for 'await' issue...")
problematic_files = []
fixed_files = []

for filepath in server_files:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if it calls transfer_file
        if 'mft_app.transfer_file' in content:
            # Check if it has await
            if 'await mft_app.transfer_file' in content:
                print(f"   ✅ {filepath}: Has 'await' - GOOD!")
                fixed_files.append(filepath)
            else:
                print(f"   ❌ {filepath}: Missing 'await' - BROKEN!")
                problematic_files.append(filepath)

                # Show the problematic line
                for i, line in enumerate(content.split('\n'), 1):
                    if 'mft_app.transfer_file' in line and 'await' not in line:
                        print(f"      Line {i}: {line.strip()}")
    except Exception as e:
        print(f"   ⚠️  Error reading {filepath}: {e}")

# Show results
print("\n3️⃣ DIAGNOSIS:")
print("=" * 80)

if problematic_files:
    print(f"❌ FOUND PROBLEM in {len(problematic_files)} file(s):")
    for f in problematic_files:
        print(f"   - {f}")
    print("\n🔧 HOW TO FIX:")
    print("   1. STOP the running server (CTRL+C)")
    print("   2. Download the FIXED mft_system_complete.py")
    print("   3. Replace or rename your old file")
    print("   4. Run: python mft_system_complete.py")
    print("\n💡 Or manually add 'await' before 'mft_app.transfer_file(...)'")

elif fixed_files:
    print(f"✅ All {len(fixed_files)} file(s) look good!")
    print("\nBut you're still getting the error?")
    print("\n🔍 Possible causes:")
    print("   1. You're running a DIFFERENT file")
    print("   2. The server wasn't restarted after updating")
    print("   3. There's a cached .pyc file")
    print("\n🔧 Solutions:")
    print("   1. Stop server (CTRL+C)")
    print("   2. Delete __pycache__ folders")
    print("   3. Restart: python mft_system_complete.py")
else:
    print("⚠️  No files call transfer_file - might be using different API")

# Check for missing libraries
print("\n4️⃣ Checking Python libraries...")
try:
    import paramiko

    print("   ✅ paramiko installed")
except ImportError:
    print("   ❌ paramiko NOT installed")
    print("      Install: pip install paramiko --break-system-packages")

try:
    from smb import SMBConnection

    print("   ✅ pysmb installed")
except ImportError:
    print("   ❌ pysmb NOT installed")
    print("      Install: pip install pysmb --break-system-packages")

# Check if mft_application exists
print("\n5️⃣ Checking MFT Application...")
if os.path.exists('mft_application.py'):
    print("   ✅ mft_application.py found")
else:
    print("   ❌ mft_application.py NOT found")
    print("      This file is required!")

# Check if protocol_handlers exists
if os.path.exists('protocol_handlers.py'):
    print("   ✅ protocol_handlers.py found")

    # Check if it has the fixes
    try:
        with open('protocol_handlers.py', 'r', encoding='utf-8') as f:
            handler_content = f.read()

        if 'normalize_unc_path' in handler_content:
            print("      ✅ Has UNC path normalization fix")
        else:
            print("      ⚠️  Missing UNC path normalization fix")
            print("         Download protocol_handlers_fixed.py")
    except:
        pass
else:
    print("   ❌ protocol_handlers.py NOT found")
    print("      Download protocol_handlers_fixed.py")

# Final recommendations
print("\n" + "=" * 80)
print("🎯 FINAL RECOMMENDATIONS:")
print("=" * 80)

if problematic_files:
    print("\n🚨 CRITICAL: You have files with the 'await' bug!")
    print("\n   ACTION REQUIRED:")
    print("   1. Download mft_system_complete.py from Claude")
    print("   2. Stop your server (CTRL+C)")
    print("   3. Run the new file: python mft_system_complete.py")
    print("   4. Create a test transfer")
    print("   5. Files should now actually copy!")
else:
    print("\n✅ Code looks good!")
    print("\n   If still not working:")
    print("   1. Make sure you restarted the server")
    print("   2. Clear __pycache__: rm -rf __pycache__")
    print("   3. Install missing libraries (see above)")
    print("   4. Check console output when creating transfer")

print("\n" + "=" * 80)
print("📞 Share this output with Claude for more help!")
print("=" * 80)