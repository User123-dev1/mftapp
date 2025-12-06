================================================================================
MFT PROFESSIONAL SYSTEM - INSTALLATION GUIDE
================================================================================

SIMPLE INSTALLATION (ONE CLICK):
---------------------------------
1. Extract the ZIP file to any folder
2. Double-click "install.bat"
3. Wait for installation to complete
4. Double-click "MFT System" icon on your desktop

That's it! The app is now installed and running.

================================================================================

PREREQUISITES:
--------------
- Windows 7/8/10/11 (64-bit)
- Python 3.8 or higher (https://www.python.org)
  * During Python installation, check "Add Python to PATH"
- Administrator privileges (installer will auto-request)

================================================================================

WHAT GETS INSTALLED:
--------------------
Installation Location: C:\MFT-System

The installer will automatically:
✓ Create installation folder
✓ Copy all application files
✓ Create Python virtual environment
✓ Install all dependencies
✓ Install NSSM for Windows service
✓ Create and start Windows service (auto-start on boot)
✓ Create firewall rule for port 5000
✓ Create desktop shortcut with custom icon

================================================================================

AFTER INSTALLATION:
-------------------
1. Desktop icon "MFT System" will appear
2. Double-click the icon to access the web interface
3. Browser will open to: http://localhost:5000
4. Default login:
   Username: sysadmin
   Password: Admin@123
   *** CHANGE PASSWORD IMMEDIATELY! ***

================================================================================

SERVICE MANAGEMENT:
-------------------
The MFT System runs as a Windows service and starts automatically.

Start service:   net start MFT-System
Stop service:    net stop MFT-System
Check status:    sc query MFT-System

================================================================================

OFFLINE INSTALLATION:
---------------------
This package works in isolated/air-gapped networks!

All required components are bundled:
- NSSM service manager (nssm.exe)
- All launcher scripts
- Application files

Python dependencies will be downloaded during installation.
If pip fails due to network restrictions, dependencies can be
pre-downloaded as wheel files and placed in the installation folder.

================================================================================

TROUBLESHOOTING:
----------------
Q: Installation fails with "Python not found"
A: Install Python from https://www.python.org and check "Add Python to PATH"

Q: Service doesn't start automatically
A: Make sure nssm.exe is in the same folder as install.bat before installing

Q: Desktop icon shows console window
A: Right-click the icon → Properties
   Target should be: C:\MFT-System\launch_mft_wrapper.vbs

Q: Can't access http://localhost:5000
A: Check if service is running: sc query MFT-System
   If stopped, start it: net start MFT-System

Q: Permission errors during installation
A: Right-click install.bat → Run as administrator

================================================================================

UNINSTALLATION:
---------------
1. Stop service: net stop MFT-System
2. Remove service: nssm remove MFT-System confirm
3. Delete folder: C:\MFT-System
4. Delete desktop shortcut
5. Remove firewall rule (optional):
   netsh advfirewall firewall delete rule name="MFT System"

================================================================================

SUPPORT:
--------
For issues or questions, contact your system administrator.

Version: 1.0.2
Installation Type: Automated Windows Installer

================================================================================
