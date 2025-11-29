#!/usr/bin/env python3
"""
MFT Application Launcher
Easy way to start the MFT application with GUI
"""

import sys
import subprocess
import time
import webbrowser
from pathlib import Path

def print_banner():
    """Print application banner"""
    banner = """
    ╔══════════════════════════════════════════════╗
    ║   MFT Application Launcher                   ║
    ║   Enterprise Managed File Transfer System    ║
    ╚══════════════════════════════════════════════╝
    """
    print(banner)

def check_dependencies():
    """Check if required dependencies are installed"""
    try:
        import fastapi
        import uvicorn
        print("✓ API dependencies found")
        return True
    except ImportError:
        print("✗ Missing dependencies. Please run: pip install -r requirements.txt")
        return False

def start_api_server():
    """Start the API server"""
    print("\n🚀 Starting API server...")
    process = subprocess.Popen(
        [sys.executable, "api_server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(3)  # Wait for server to start
    
    if process.poll() is None:
        print("✓ API server started successfully")
        print("   URL: http://localhost:8000")
        print("   API Docs: http://localhost:8000/docs")
        return process
    else:
        print("✗ Failed to start API server")
        return None

def launch_desktop_gui():
    """Launch PyQt6 desktop GUI"""
    try:
        import PyQt6
        print("\n🖥️  Launching Desktop GUI...")
        subprocess.Popen([sys.executable, "mft_gui_pyqt6.py"])
        print("✓ Desktop GUI launched")
        return True
    except ImportError:
        print("✗ PyQt6 not installed. Install with: pip install PyQt6")
        return False

def launch_web_gui():
    """Launch web GUI in browser"""
    print("\n🌐 Opening Web GUI in browser...")
    time.sleep(1)
    webbrowser.open("http://localhost:8000")
    print("✓ Web GUI opened in browser")
    return True

def main():
    """Main launcher function"""
    print_banner()
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # Menu
    print("\nSelect launch option:")
    print("1. API Server + Web GUI (recommended)")
    print("2. API Server + Desktop GUI")
    print("3. API Server + Both GUIs")
    print("4. API Server Only")
    print("5. Exit")
    
    choice = input("\nEnter choice (1-5): ").strip()
    
    if choice == "5":
        print("Goodbye!")
        sys.exit(0)
    
    # Start API server
    api_process = start_api_server()
    if not api_process:
        print("\n❌ Failed to start. Exiting...")
        sys.exit(1)
    
    try:
        # Launch GUI(s) based on choice
        if choice == "1":
            launch_web_gui()
        elif choice == "2":
            if not launch_desktop_gui():
                print("\n⚠️  Desktop GUI not available, opening web GUI instead...")
                launch_web_gui()
        elif choice == "3":
            launch_web_gui()
            launch_desktop_gui()
        elif choice == "4":
            print("\n✓ API server running")
            print("   Access at: http://localhost:8000")
            print("   API Docs: http://localhost:8000/docs")
        else:
            print("Invalid choice. Launching Web GUI...")
            launch_web_gui()
        
        print("\n" + "="*50)
        print("MFT Application is running!")
        print("="*50)
        print("\nPress Ctrl+C to stop the server\n")
        
        # Keep running
        api_process.wait()
        
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
        api_process.terminate()
        api_process.wait()
        print("✓ Stopped successfully")
        sys.exit(0)

if __name__ == "__main__":
    main()
