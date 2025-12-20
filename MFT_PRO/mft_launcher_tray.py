#!/usr/bin/env python3
"""
MFT System Tray Launcher
Wraps the Flask app in a desktop window with system tray support
"""

import webview
import threading
import time
import sys
import os
from pathlib import Path

# Add the MFT_PRO directory to the path
sys.path.insert(0, str(Path(__file__).parent))

# Import the Flask app
from mft_system_with_rules_ui import app, logger

class MFTTrayApp:
    """MFT Application with system tray support"""

    def __init__(self):
        self.window = None
        self.server_thread = None
        self.running = False

    def start_flask(self):
        """Start the Flask server in background"""
        logger.info("🚀 Starting Flask server...")
        app.run(host='127.0.0.1', port=5000, use_reloader=False, debug=False)

    def on_closing(self):
        """Handle window close event - minimize to tray instead"""
        if self.window:
            # Hide window instead of closing
            self.window.hide()
            logger.info("💠 Application minimized to system tray")
            return False  # Prevent window from closing
        return True

    def on_shown(self):
        """Called when window is shown"""
        logger.info("✅ MFT Application window opened")

    def show_window(self):
        """Show the application window"""
        if self.window:
            self.window.show()

    def exit_app(self):
        """Properly exit the application"""
        logger.warning("🛑 Shutting down MFT Application...")
        self.running = False
        if self.window:
            self.window.destroy()
        # Kill the Flask server
        os._exit(0)

    def run(self):
        """Run the application"""
        # Start Flask in background thread
        self.server_thread = threading.Thread(target=self.start_flask, daemon=True)
        self.server_thread.start()

        # Wait for Flask to start
        time.sleep(2)

        logger.info("="*80)
        logger.info("🚀 MFT SYSTEM - SYSTEM TRAY MODE")
        logger.info("="*80)
        logger.info("✅ Flask server started on http://127.0.0.1:5000")
        logger.info("✅ Creating desktop window...")
        logger.info("💡 TIP: Close the window to minimize to system tray")
        logger.info("💡 TIP: Use Admin > Shutdown Server to fully exit (requires admin password)")
        logger.info("="*80)

        # Create desktop window with system tray
        self.window = webview.create_window(
            '🚀 MFT Professional System',
            'http://127.0.0.1:5000',
            width=1400,
            height=900,
            resizable=True,
            text_select=True
        )

        # Set event handlers
        self.window.events.closing += self.on_closing
        self.window.events.shown += self.on_shown

        self.running = True

        # Start the webview (this blocks until window is closed)
        webview.start(debug=False)

def main():
    """Main entry point"""
    app_instance = MFTTrayApp()

    try:
        app_instance.run()
    except KeyboardInterrupt:
        logger.info("🛑 Keyboard interrupt received")
        app_instance.exit_app()
    except Exception as e:
        logger.error(f"❌ Application error: {e}")
        import traceback
        traceback.print_exc()
        app_instance.exit_app()

if __name__ == '__main__':
    main()
