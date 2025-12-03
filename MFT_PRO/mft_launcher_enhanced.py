#!/usr/bin/env python3
"""
MFT System - Enhanced Desktop Launcher with System Tray
Features:
- Desktop window with PyWebView
- System tray icon and menu
- Minimize to tray on close
- Admin-protected shutdown
"""

import webview
import threading
import time
import sys
import os
from pathlib import Path

# Try to import pystray for system tray support
try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    print("⚠️ Warning: pystray not installed. System tray support disabled.")
    print("   Install with: pip install pystray pillow")

# Add the MFT_PRO directory to the path
sys.path.insert(0, str(Path(__file__).parent))

# Import the Flask app
from mft_system_with_rules_ui import app, logger

class MFTDesktopApp:
    """MFT Application with desktop window and system tray"""

    def __init__(self):
        self.window = None
        self.tray_icon = None
        self.server_thread = None
        self.running = False
        self.tray_thread = None

    def create_tray_icon(self):
        """Create a simple icon for the system tray"""
        # Create a simple icon (64x64 blue circle with "MFT")
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), (103, 126, 234))
        dc = ImageDraw.Draw(image)

        # Draw a circle
        dc.ellipse([8, 8, width-8, height-8], fill=(118, 75, 162), outline=(255, 255, 255))

        # Draw text "MFT"
        dc.text((14, 20), "MFT", fill=(255, 255, 255))

        return image

    def start_flask(self):
        """Start the Flask server in background"""
        logger.info("🚀 Starting Flask server...")
        try:
            app.run(host='127.0.0.1', port=5000, use_reloader=False, debug=False)
        except Exception as e:
            logger.error(f"Flask server error: {e}")

    def on_closing(self):
        """Handle window close event - minimize to tray instead"""
        if self.window:
            # Hide window instead of closing
            self.window.hide()
            logger.info("💠 Application minimized to system tray")

            if HAS_TRAY and self.tray_icon:
                # Show notification
                try:
                    self.tray_icon.notify(
                        "MFT System is still running",
                        "The application is minimized to system tray. Right-click the tray icon to access options."
                    )
                except:
                    pass

            return False  # Prevent window from closing
        return True

    def show_window(self, icon=None, item=None):
        """Show the application window"""
        if self.window:
            logger.info("✅ Showing MFT window")
            self.window.show()

    def hide_window(self, icon=None, item=None):
        """Hide the application window to tray"""
        if self.window:
            logger.info("💠 Hiding MFT window to tray")
            self.window.hide()

    def exit_app(self, icon=None, item=None):
        """Exit the application (no admin check from tray)"""
        logger.warning("🛑 Exit requested from system tray")
        logger.warning("⚠️ Note: Use 'Admin > Shutdown Server' in the app for admin-protected shutdown")
        logger.warning("🛑 Shutting down MFT Application...")

        self.running = False

        # Stop tray icon
        if HAS_TRAY and self.tray_icon:
            self.tray_icon.stop()

        # Destroy window
        if self.window:
            try:
                self.window.destroy()
            except:
                pass

        # Exit
        time.sleep(0.5)
        os._exit(0)

    def create_tray_menu(self):
        """Create the system tray menu"""
        return pystray.Menu(
            item('Show MFT Window', self.show_window, default=True),
            item('Hide to Tray', self.hide_window),
            pystray.Menu.SEPARATOR,
            item('Exit (No Admin Check)', self.exit_app)
        )

    def run_tray(self):
        """Run the system tray icon"""
        if not HAS_TRAY:
            logger.warning("⚠️ System tray support not available")
            return

        try:
            icon_image = self.create_tray_icon()
            self.tray_icon = pystray.Icon(
                'mft_system',
                icon_image,
                '🚀 MFT Professional System',
                menu=self.create_tray_menu()
            )

            logger.info("✅ System tray icon created")
            # Run the tray icon (blocks)
            self.tray_icon.run()
        except Exception as e:
            logger.error(f"System tray error: {e}")

    def run(self):
        """Run the application"""
        # Start Flask in background thread
        self.server_thread = threading.Thread(target=self.start_flask, daemon=True)
        self.server_thread.start()

        # Wait for Flask to start
        time.sleep(2)

        logger.info("="*80)
        logger.info("🚀 MFT SYSTEM - ENHANCED DESKTOP MODE")
        logger.info("="*80)
        logger.info("✅ Flask server started on http://127.0.0.1:5000")
        logger.info("✅ Creating desktop window...")

        if HAS_TRAY:
            logger.info("✅ System tray support enabled")
            logger.info("💡 TIP: Close window to minimize to system tray")
            logger.info("💡 TIP: Use Admin > Shutdown Server for admin-protected shutdown")
            logger.info("💡 TIP: Right-click tray icon for quick access")
        else:
            logger.info("⚠️ System tray support disabled (install pystray)")
            logger.info("💡 TIP: Use Admin > Shutdown Server to exit")

        logger.info("="*80)

        # Start system tray in background (if available)
        if HAS_TRAY:
            self.tray_thread = threading.Thread(target=self.run_tray, daemon=True)
            self.tray_thread.start()

        # Create desktop window
        self.window = webview.create_window(
            '🚀 MFT Professional System',
            'http://127.0.0.1:5000',
            width=1400,
            height=900,
            resizable=True,
            text_select=True,
            confirm_close=False  # We handle closing ourselves
        )

        # Set event handlers
        self.window.events.closing += self.on_closing

        self.running = True

        # Start the webview (this blocks until window is closed)
        webview.start(debug=False)

def main():
    """Main entry point"""
    print("="*80)
    print("🚀 MFT PROFESSIONAL SYSTEM - DESKTOP LAUNCHER")
    print("="*80)
    print()

    # Check dependencies
    missing_deps = []
    try:
        import webview
    except ImportError:
        missing_deps.append("pywebview")

    if not HAS_TRAY:
        print("⚠️ Optional: Install pystray for system tray support")
        print("   pip install pystray pillow")
        print()

    if missing_deps:
        print("❌ Missing required dependencies:")
        for dep in missing_deps:
            print(f"   - {dep}")
        print()
        print("Install with: pip install " + " ".join(missing_deps))
        sys.exit(1)

    app_instance = MFTDesktopApp()

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
