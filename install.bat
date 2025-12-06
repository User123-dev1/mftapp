@echo off
REM MFT Professional System - Windows Installation Script
REM Automatically elevates to Administrator if needed

REM Change to the directory where this script is located
cd /d "%~dp0"

REM Check for admin privileges and auto-elevate if needed
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ========================================
echo MFT Professional System - Installer
echo ========================================
echo.
echo Working directory: %CD%
echo.

REM Check for Python
echo Checking for Python...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Python not found
    echo Please install Python 3.8 or higher from https://www.python.org
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

REM Check Python version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo Found Python: %PYTHON_VERSION%

REM Check if MFT_PRO folder exists
if not exist "MFT_PRO" (
    echo ERROR: MFT_PRO folder not found!
    echo.
    echo Please make sure you are running this script from the extracted folder.
    echo.
    echo Expected folder structure:
    echo   mft-system-v1.0.0\
    echo   ├── MFT_PRO\
    echo   ├── install.bat  ^(you are here^)
    echo   └── other files...
    echo.
    echo Current directory: %CD%
    echo.
    pause
    exit /b 1
)

REM Create installation directory (outside Program Files to avoid permission issues)
set INSTALL_DIR=C:\MFT-System
echo.
echo Creating installation directory: %INSTALL_DIR%
mkdir "%INSTALL_DIR%" 2>nul
echo Copying files...
xcopy /E /I /Y MFT_PRO "%INSTALL_DIR%"
if %errorLevel% neq 0 (
    echo ERROR: Failed to copy files
    pause
    exit /b 1
)

REM Copy launcher scripts and icon
echo Copying launcher scripts...
if exist "launch_mft_hidden.vbs" copy /Y launch_mft_hidden.vbs "%INSTALL_DIR%"
if exist "launch_mft.ps1" copy /Y launch_mft.ps1 "%INSTALL_DIR%"
if exist "mft_icon.ico" (
    copy /Y mft_icon.ico "%INSTALL_DIR%"
    echo Custom icon copied
) else (
    echo Note: No custom icon found. Using default icon.
)

REM Create PowerShell wrapper for hidden console
(
    echo Set objShell = CreateObject^("WScript.Shell"^)
    echo objShell.Run "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File ""%INSTALL_DIR%\launch_mft.ps1""", 0, False
) > "%INSTALL_DIR%\launch_mft_wrapper.vbs"

REM Create virtual environment
echo.
echo Creating Python virtual environment...
cd /d "%INSTALL_DIR%"
python -m venv venv
if %errorLevel% neq 0 (
    echo ERROR: Failed to create virtual environment
    pause
    exit /b 1
)

REM Activate virtual environment and install dependencies
echo.
echo Installing Python dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if %errorLevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

REM Create data directories
echo.
echo Creating data directories...
mkdir "C:\ProgramData\MFT-System\state" 2>nul
mkdir "C:\ProgramData\MFT-System\local_users" 2>nul
mkdir "C:\ProgramData\MFT-System\audit" 2>nul
mkdir "C:\ProgramData\MFT-System\logs" 2>nul
mkdir "C:\ProgramData\MFT-System\config" 2>nul

REM Create default configuration
if not exist "C:\ProgramData\MFT-System\config\config.env" (
    echo.
    echo Creating default configuration...
    (
        echo # MFT System Configuration
        echo.
        echo # Flask Configuration
        echo FLASK_SECRET_KEY=change-this-to-a-random-secret-key
        echo FLASK_HOST=0.0.0.0
        echo FLASK_PORT=5000
        echo FLASK_DEBUG=False
        echo.
        echo # Data Directories
        echo MFT_STATE_DIR=C:\ProgramData\MFT-System\state
        echo MFT_USERS_DIR=C:\ProgramData\MFT-System\local_users
        echo MFT_AUDIT_DIR=C:\ProgramData\MFT-System\audit
        echo MFT_LOG_DIR=C:\ProgramData\MFT-System\logs
        echo.
        echo # Default Admin Credentials (CHANGE THESE!)
        echo MFT_ADMIN_USERNAME=sysadmin
        echo MFT_ADMIN_PASSWORD=Admin@123
    ) > "C:\ProgramData\MFT-System\config\config.env"
)

REM Create startup batch file
echo.
echo Creating startup script...
(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo call venv\Scripts\activate.bat
    echo python mft_system_with_rules_ui.py
    echo pause
) > "%INSTALL_DIR%\start-mft-system.bat"

REM Create Windows service (install NSSM if bundled)
echo.
echo Setting up Windows service for auto-start...

REM Check if bundled NSSM exists, if so install it
if exist "nssm.exe" (
    echo Installing bundled NSSM...
    copy /Y nssm.exe "%WINDIR%\System32\" >nul 2>&1
    if %errorLevel% equ 0 (
        echo NSSM installed successfully
    ) else (
        echo Warning: Could not copy NSSM to System32
    )
)

REM Now check if NSSM is available
where nssm >nul 2>&1
if %errorLevel% equ 0 (
    echo NSSM found. Installing Windows service...

    REM Remove service if it already exists
    nssm stop MFT-System >nul 2>&1
    nssm remove MFT-System confirm >nul 2>&1

    REM Install the service
    nssm install MFT-System "%INSTALL_DIR%\venv\Scripts\pythonw.exe" "%INSTALL_DIR%\mft_system_with_rules_ui.py"
    nssm set MFT-System AppDirectory "%INSTALL_DIR%"
    nssm set MFT-System DisplayName "MFT Professional System"
    nssm set MFT-System Description "Managed File Transfer System with Enterprise Features"
    nssm set MFT-System Start SERVICE_AUTO_START
    nssm set MFT-System AppStdout "C:\ProgramData\MFT-System\logs\mft-system.log"
    nssm set MFT-System AppStderr "C:\ProgramData\MFT-System\logs\mft-system-error.log"

    REM Set service to restart on failure
    nssm set MFT-System AppExit Default Restart
    nssm set MFT-System AppRestartDelay 5000

    echo Service installed successfully!

    REM Start the service automatically
    echo Starting MFT System service...
    net start MFT-System
    if %errorLevel% equ 0 (
        echo.
        echo ========================================
        echo SUCCESS! Service is running!
        echo ========================================
        echo The MFT System is now running in the background.
        echo It will automatically start when Windows boots.
        echo.
    ) else (
        echo Warning: Service installed but failed to start.
        echo You can start it manually with: net start MFT-System
    )
) else (
    echo.
    echo ========================================
    echo WARNING: NSSM not found
    echo ========================================
    echo.
    echo The MFT System will be installed but will NOT auto-start on boot.
    echo To enable auto-start:
    echo 1. Place nssm.exe in the same folder as install.bat
    echo 2. Run install.bat again
    echo.
    pause
)

REM Create firewall rule
echo.
echo Creating firewall rule...
netsh advfirewall firewall add rule name="MFT System" dir=in action=allow protocol=TCP localport=5000
if %errorLevel% equ 0 (
    echo Firewall rule created successfully
) else (
    echo Warning: Failed to create firewall rule
)

REM Create desktop shortcut
echo.
echo Creating desktop shortcuts...
if exist "%INSTALL_DIR%\launch_mft_wrapper.vbs" (
    REM Create shortcut with PowerShell hidden launcher
    if exist "%INSTALL_DIR%\mft_icon.ico" (
        powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%PUBLIC%\Desktop\MFT System.lnk');$s.TargetPath='%INSTALL_DIR%\launch_mft_wrapper.vbs';$s.WorkingDirectory='%INSTALL_DIR%';$s.IconLocation='%INSTALL_DIR%\mft_icon.ico';$s.Description='MFT Professional System - Managed File Transfer';$s.Save()"
        echo Desktop shortcut created with custom icon (hidden console)
    ) else (
        powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%PUBLIC%\Desktop\MFT System.lnk');$s.TargetPath='%INSTALL_DIR%\launch_mft_wrapper.vbs';$s.WorkingDirectory='%INSTALL_DIR%';$s.Description='MFT Professional System - Managed File Transfer';$s.Save()"
        echo Desktop shortcut created (hidden console)
    )
) else (
    REM Fallback to batch file launcher
    powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%PUBLIC%\Desktop\MFT System.lnk');$s.TargetPath='%INSTALL_DIR%\start-mft-system.bat';$s.WorkingDirectory='%INSTALL_DIR%';$s.Save()"
    echo Desktop shortcut created
)

echo.
echo ========================================
echo Installation Complete!
echo ========================================
echo.
echo Next steps:
echo.
echo 1. Edit configuration (optional):
echo    notepad "C:\ProgramData\MFT-System\config\config.env"
echo.
echo 2. The MFT System is ready to use:
echo    - If service is running: Already started in background
echo    - Or double-click "MFT System" on your desktop
echo    - Or manually: "%INSTALL_DIR%\start-mft-system.bat"
echo.
echo 3. Access the web interface:
echo    http://localhost:5000
echo.
echo 4. Default login:
echo    Username: sysadmin
echo    Password: Admin@123
echo    (CHANGE THIS IMMEDIATELY!)
echo.
echo 5. Service Management:
echo    Start:   net start MFT-System
echo    Stop:    net stop MFT-System
echo    Status:  sc query MFT-System
echo.
echo Note: The console window is hidden when running as a service
echo       or using the desktop shortcut.
echo.
echo ========================================
pause
