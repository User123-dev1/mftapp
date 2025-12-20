# MFT System - Launcher Guide

## Overview

The MFT System can be launched in multiple modes depending on your requirements:

1. **Web Mode** (Standard) - Browser-based access
2. **Desktop Mode** (Enhanced) - Native window with system tray support

---

## 🌐 Mode 1: Web Mode (Standard)

**Best for:** Server deployments, multi-user access, browser-based usage

### Launch Command:
```bash
python mft_system_with_rules_ui.py
```

### Features:
- ✅ Accessible via web browser at `http://127.0.0.1:5000`
- ✅ Multi-user support - all users see same instance
- ✅ Login system with local and domain authentication
- ✅ Admin-protected shutdown via web interface
- ✅ Works on any system with Python and a browser

### Shutdown:
- **Users:** Can logout but cannot shutdown server
- **Admins:** Use `Admin > Shutdown Server` menu (requires password)

---

## 🖥️ Mode 2: Desktop Mode with System Tray (Enhanced)

**Best for:** Single-user workstations, desktop applications, background operation

### Prerequisites:
```bash
# Install desktop dependencies
pip install -r requirements_desktop.txt

# On Linux, you may also need:
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.0
```

### Launch Command:
```bash
python mft_launcher_enhanced.py
```

### Features:
- ✅ Native desktop window (no browser needed)
- ✅ System tray icon with menu
- ✅ Minimize to tray on window close
- ✅ Quick access via tray icon
- ✅ Visual notifications
- ✅ All web mode features included

### Window Behavior:
- **Close Window (X button):** Minimizes to system tray (app keeps running)
- **Tray Menu > Show:** Restore window from tray
- **Tray Menu > Hide:** Minimize window to tray
- **Tray Menu > Exit:** Closes app immediately (⚠️ no admin check)

### Shutdown Options:

#### Option 1: Admin-Protected Shutdown (Recommended)
1. Open the application window
2. Login as admin
3. Navigate to `Admin > Shutdown Server`
4. Enter admin password
5. Confirm shutdown

**Benefits:**
- ✅ Requires admin authentication
- ✅ Saves all data properly
- ✅ Logs shutdown event in audit trail
- ✅ Graceful shutdown of all services

#### Option 2: Tray Exit (Quick but unprotected)
1. Right-click system tray icon
2. Click "Exit (No Admin Check)"

**Caution:**
- ⚠️ No admin authentication required
- ⚠️ May interrupt active transfers
- ⚠️ Use only when necessary

---

## 🔒 Security Features

### Admin-Protected Shutdown
- Only administrators can shutdown the server via web interface
- Requires password re-verification
- All shutdown attempts are logged in audit trail
- Prevents accidental data loss

### User Permissions
- **Regular Users:** Can use the system but cannot shutdown
- **Administrators:** Full access including shutdown capability
- **System Admin:** Local superuser account (cannot be disabled)

---

## 📋 Comparison Table

| Feature | Web Mode | Desktop Mode |
|---------|----------|--------------|
| Browser Required | ✅ Yes | ❌ No (native window) |
| System Tray | ❌ No | ✅ Yes |
| Minimize to Tray | ❌ No | ✅ Yes |
| Multi-User Access | ✅ Yes | ✅ Yes (via URL) |
| Admin Shutdown | ✅ Yes | ✅ Yes |
| Quick Exit | ❌ No | ⚠️ Yes (via tray) |
| Auto-Start | Manual | Can configure OS |
| Background Operation | Terminal only | ✅ System tray |

---

## 🚀 Quick Start Guide

### For Server/Multi-User Deployment:
```bash
# 1. Start in web mode
python mft_system_with_rules_ui.py

# 2. Open browser
# Visit: http://127.0.0.1:5000

# 3. Login
# Default: sysadmin / Admin@123
```

### For Desktop/Single-User Deployment:
```bash
# 1. Install desktop dependencies
pip install -r requirements_desktop.txt

# 2. Launch desktop mode
python mft_launcher_enhanced.py

# 3. Window opens automatically
# Login: sysadmin / Admin@123

# 4. Use system tray
# - Minimize to tray on close
# - Right-click tray icon for options
```

---

## 🛡️ Best Practices

### General
1. Always use admin-protected shutdown when possible
2. Create separate admin accounts (don't use sysadmin for daily work)
3. Enable Active Directory for enterprise deployments
4. Review audit logs regularly

### Web Mode
1. Configure firewall if exposing to network
2. Change default admin password immediately
3. Use HTTPS in production (configure reverse proxy)

### Desktop Mode
1. Use tray "Exit" only when admin shutdown isn't available
2. Configure OS to auto-start on login if needed
3. Keep system tray visible for quick access

---

## 🐛 Troubleshooting

### Desktop Mode Won't Start
**Issue:** Missing dependencies
**Solution:**
```bash
pip install pywebview pystray pillow
```

### System Tray Icon Not Showing
**Issue:** pystray not installed or Linux dependencies missing
**Solution (Linux):**
```bash
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0
pip install pystray pillow
```

### Can't Shutdown Server
**Issue:** Not logged in as admin
**Solution:** Login with admin credentials first

### Port 5000 Already in Use
**Issue:** Another Flask app running
**Solution:**
```bash
# Find and kill process
lsof -ti:5000 | xargs kill -9
```

---

## 📞 Support

For issues or questions:
1. Check the audit log for error details
2. Review system logs in the Activity Log tab
3. Verify admin permissions for shutdown operations
4. Consult the main README for general MFT features

---

## 🔧 Advanced Configuration

### Custom Port
Edit `mft_launcher_enhanced.py`:
```python
app.run(host='127.0.0.1', port=8080, ...)  # Change port
```

### Auto-Start on Login (Windows)
1. Create shortcut to `mft_launcher_enhanced.py`
2. Place in: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`

### Auto-Start on Login (Linux)
Create `~/.config/autostart/mft-system.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=MFT System
Exec=/usr/bin/python3 /path/to/mft_launcher_enhanced.py
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
```

---

## ⚖️ License & Credits

MFT Professional System
Managed File Transfer with Enterprise Features

Built with Flask, PyWebView, and pystray
