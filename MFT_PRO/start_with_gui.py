#!/usr/bin/env python3
"""
MFT Application Startup Script
Starts the API server and automatically opens the web dashboard
"""

import subprocess
import time
import webbrowser
import os
import sys
from pathlib import Path


def main():
    print("=" * 70)
    print("🚀 MFT Application Startup")
    print("=" * 70)
    print()

    # Check if dashboard HTML exists
    dashboard_path = Path(__file__).parent / "mft_dashboard.html"

    if not dashboard_path.exists():
        print("❌ Error: mft_dashboard.html not found!")
        print(f"   Expected location: {dashboard_path}")
        print()
        print("Please make sure mft_dashboard.html is in the same directory as this script.")
        input("\nPress Enter to exit...")
        return

    print("✅ Found dashboard HTML file")
    print(f"   Location: {dashboard_path}")
    print()

    # Check if API server file exists
    api_server_path = Path(__file__).parent / "api_server.py"
    if not api_server_path.exists():
        print("❌ Error: api_server.py not found!")
        print(f"   Expected location: {api_server_path}")
        print()
        print("Please make sure api_server.py is in the same directory as this script.")
        input("\nPress Enter to exit...")
        return

    print("✅ Found API server file")
    print()

    # Start the API server
    print("📡 Starting API server...")
    print("   Running: python api_server.py")
    print()

    try:
        # Start API server as subprocess
        if sys.platform == "win32":
            # Windows
            api_process = subprocess.Popen(
                ["python", "api_server.py"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            # Linux/Mac
            api_process = subprocess.Popen(
                ["python3", "api_server.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

        print("✅ API server started (PID: {})".format(api_process.pid))
        print()

        # Wait a moment for server to start
        print("⏳ Waiting for API server to initialize...")
        time.sleep(3)

        # Open the dashboard in default browser
        print("🌐 Opening web dashboard in your browser...")
        dashboard_url = f"file:///{dashboard_path.absolute().as_posix()}"
        webbrowser.open(dashboard_url)

        print()
        print("=" * 70)
        print("✅ SUCCESS! MFT Application is now running!")
        print("=" * 70)
        print()
        print("📊 Your web dashboard should open automatically in your browser")
        print()
        print("If it didn't open, manually open this file:")
        print(f"   {dashboard_path.absolute()}")
        print()
        print("🔗 API Server: http://localhost:8000")
        print("📖 API Docs:   http://localhost:8000/docs")
        print()
        print("=" * 70)
        print("⚠️  IMPORTANT:")
        print("   - The WEB DASHBOARD is the GUI (should open in browser)")
        print("   - Do NOT go to localhost:8000 (that's just API docs)")
        print("   - Keep this window open while using the application")
        print("=" * 70)
        print()
        print("Press Ctrl+C to stop the application...")
        print()

        # Keep running
        try:
            api_process.wait()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down...")
            api_process.terminate()
            print("✅ Application stopped")

    except FileNotFoundError:
        print("❌ Error: Python not found!")
        print("   Make sure Python is installed and in your PATH")
        input("\nPress Enter to exit...")
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()