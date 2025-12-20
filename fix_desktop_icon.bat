@echo off
REM Fix Desktop Icon - Updates the MFT System desktop shortcut
REM Run this to fix the desktop icon to launch without showing console

echo ========================================
echo MFT System - Desktop Icon Fixer
echo ========================================
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Please run as Administrator
    echo Right-click this file and select "Run as administrator"
    pause
    exit /b 1
)

set INSTALL_DIR=C:\MFT-System

REM Check if installation exists
if not exist "%INSTALL_DIR%" (
    echo ERROR: MFT System not found at: %INSTALL_DIR%
    echo Please install MFT System first.
    pause
    exit /b 1
)

REM Copy PowerShell launcher if it exists in current directory
if exist "launch_mft.ps1" (
    copy /Y "launch_mft.ps1" "%INSTALL_DIR%\"
    echo PowerShell launcher copied
)

echo.
echo Creating desktop shortcut with hidden console...

REM Create a wrapper VBS that launches PowerShell hidden
(
    echo Set objShell = CreateObject^("WScript.Shell"^)
    echo objShell.Run "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\Program Files\MFT-System\launch_mft.ps1""", 0, False
) > "%INSTALL_DIR%\launch_mft_wrapper.vbs"

REM Create desktop shortcut
if exist "%INSTALL_DIR%\mft_icon.ico" (
    powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%PUBLIC%\Desktop\MFT System.lnk');$s.TargetPath='%INSTALL_DIR%\launch_mft_wrapper.vbs';$s.WorkingDirectory='%INSTALL_DIR%';$s.IconLocation='%INSTALL_DIR%\mft_icon.ico';$s.Description='MFT Professional System';$s.Save()"
    echo Desktop shortcut created with custom icon
) else (
    powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%PUBLIC%\Desktop\MFT System.lnk');$s.TargetPath='%INSTALL_DIR%\launch_mft_wrapper.vbs';$s.WorkingDirectory='%INSTALL_DIR%';$s.Description='MFT Professional System';$s.Save()"
    echo Desktop shortcut created
)

echo.
echo ========================================
echo Desktop Icon Fixed!
echo ========================================
echo.
echo The MFT System desktop icon will now:
echo - Launch without showing console window
echo - Auto-open browser to http://localhost:5000
echo - Check if service is running first
echo.
echo Test it: Double-click "MFT System" on your desktop
echo.
pause
