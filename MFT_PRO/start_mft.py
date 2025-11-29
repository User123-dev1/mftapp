#!/usr/bin/env python3
"""
MFT Application - Simple Startup Script
Runs all checks and starts the server
"""

import sys
import subprocess
import time

def print_header(text):
    print("\n" + "=" * 60)
    print(text)
    print("=" * 60)

def run_tests():
    """Run comprehensive tests"""
    print_header("Running System Tests")
    result = subprocess.run([sys.executable, "test_mft_complete.py"], 
                          capture_output=False)
    return result.returncode == 0

def start_server():
    """Start the API server"""
    print_header("Starting MFT API Server")
    print("\nServer will start on http://0.0.0.0:8000")
    print("\nAvailable endpoints:")
    print("  • API Docs:      http://localhost:8000/docs")
    print("  • Health Check:  http://localhost:8000/health")
    print("  • Devices:       http://localhost:8000/api/v1/devices")
    print("  • Rules:         http://localhost:8000/api/v1/rules")
    print("  • Audit Logs:    http://localhost:8000/api/v1/audit")
    print("\nPress Ctrl+C to stop the server")
    print("-" * 60)
    
    try:
        # Start with uvicorn for better performance
        subprocess.run([
            sys.executable, "-m", "uvicorn",
            "api_server:app",
            "--host", "0.0.0.0",
            "--port", "8000",
            "--log-level", "info"
        ])
    except FileNotFoundError:
        # Fallback to direct python execution
        print("\nuvicorn not found, using direct Python execution...")
        subprocess.run([sys.executable, "api_server.py"])

def main():
    print_header("MFT Application - Startup")
    print("\n📋 Checking system...")
    
    # Ask if user wants to run tests
    print("\nRun comprehensive tests first? (recommended)")
    response = input("Run tests? [Y/n]: ").strip().lower()
    
    if response != 'n':
        if not run_tests():
            print("\n⚠️  Some tests failed!")
            print("You can still start the server, but there may be issues.")
            response = input("\nStart server anyway? [y/N]: ").strip().lower()
            if response != 'y':
                print("\nExiting. Fix issues and try again.")
                return 1
    
    # Start the server
    print("\n✅ System ready!")
    time.sleep(1)
    start_server()
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n👋 Shutting down MFT Application...")
        print("Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
