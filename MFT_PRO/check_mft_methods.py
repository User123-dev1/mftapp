#!/usr/bin/env python3
"""
MFT Application Diagnostic Tool
Checks what methods are available and their signatures
"""

import inspect

print("=" * 70)
print("🔍 MFT Application Diagnostic Tool")
print("=" * 70)
print()

try:
    from mft_application import MFTApplication

    print("✅ MFTApplication imported successfully!")
    print()

    # Create instance
    mft_app = MFTApplication()
    print("✅ MFTApplication instance created")
    print()

    # Get all methods
    all_methods = dir(mft_app)

    # Filter out private methods
    public_methods = [m for m in all_methods if not m.startswith('_')]

    print(f"📋 Found {len(public_methods)} public methods:")
    print()

    for method in public_methods:
        attr = getattr(mft_app, method)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
                print(f"   ✓ {method}{sig}")
            except:
                print(f"   ✓ {method}()")
        else:
            print(f"   • {method} (attribute)")

    print()
    print("=" * 70)
    print("🔍 Checking for transfer-related methods:")
    print("=" * 70)
    print()

    transfer_methods = [
        'execute_transfer',
        'transfer_file',
        'start_transfer',
        'run_transfer',
        'perform_transfer',
        'do_transfer',
        'transfer',
        'copy_file',
        'send_file',
        'upload_file',
        'download_file'
    ]

    found_methods = []
    for method_name in transfer_methods:
        if hasattr(mft_app, method_name):
            method = getattr(mft_app, method_name)
            try:
                sig = inspect.signature(method)
                print(f"   ✅ {method_name}{sig} - FOUND!")
                found_methods.append((method_name, sig))
            except:
                print(f"   ✅ {method_name}() - FOUND! (signature unavailable)")
                found_methods.append((method_name, None))
        else:
            print(f"   ❌ {method_name}() - not found")

    print()
    if found_methods:
        print(f"✅ Your MFTApplication has these transfer methods:")
        print()
        for method_name, sig in found_methods:
            if sig:
                print(f"   📝 {method_name}{sig}")
                print()
                # Parse parameters
                params = list(sig.parameters.keys())
                if params and params[0] == 'self':
                    params = params[1:]
                if params:
                    print(f"      Required parameters: {', '.join(params)}")
            else:
                print(f"   📝 {method_name}()")
            print()

        print("💡 Recommendation:")
        method_name, sig = found_methods[0]
        print(f"   Use: mft_app.{method_name}()")
        if sig:
            params = list(sig.parameters.keys())
            if params and params[0] == 'self':
                params = params[1:]
            if params:
                print(f"   Parameters: {', '.join(params)}")
    else:
        print("⚠️  No standard transfer methods found!")
        print()
        print("💡 Possible solutions:")
        print("   1. Check if transfer is done through a different workflow")
        print("   2. Look at the actual method names above")
        print("   3. The transfer might need protocol-specific methods")

    print()
    print("=" * 70)

except ImportError as e:
    print(f"❌ Error importing MFTApplication: {e}")
    print()
    print("💡 Make sure mft_application.py is in the same directory")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback

    traceback.print_exc()

print()
print("=" * 70)