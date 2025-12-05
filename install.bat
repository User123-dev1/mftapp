@echo off
REM MFT Professional System - Windows Installation Script
REM Usage: Run as Administrator

REM Change to the directory where this script is located
cd /d "%~dp0"

echo ========================================
echo MFT Professional System - Installer
echo ========================================
echo.
echo Working directory: %CD%
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Please run as Administrator
    echo Right-click install.bat and select "Run as administrator"
    pause
    exit /b 1
)

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

REM Create installation directory
set INSTALL_DIR=C:\Program Files\MFT-System
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

REM Create Windows service (optional, requires NSSM)
echo.
echo Checking for NSSM (for Windows service installation)...
where nssm >nul 2>&1
if %errorLevel% equ 0 (
    echo NSSM found. Installing Windows service...
    nssm install MFT-System "%INSTALL_DIR%\venv\Scripts\python.exe" "%INSTALL_DIR%\mft_system_with_rules_ui.py"
    nssm set MFT-System AppDirectory "%INSTALL_DIR%"
    nssm set MFT-System DisplayName "MFT Professional System"
    nssm set MFT-System Description "Managed File Transfer System with Enterprise Features"
    nssm set MFT-System Start SERVICE_AUTO_START
    nssm set MFT-System AppStdout "C:\ProgramData\MFT-System\logs\mft-system.log"
    nssm set MFT-System AppStderr "C:\ProgramData\MFT-System\logs\mft-system-error.log"
    echo Service installed successfully!
) else (
    echo NSSM not found. Skipping service installation.
    echo To install as a service, download NSSM from https://nssm.cc/
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
set SCRIPT_PATH=%~dp0
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%PUBLIC%\Desktop\MFT System.lnk');$s.TargetPath='%INSTALL_DIR%\start-mft-system.bat';$s.WorkingDirectory='%INSTALL_DIR%';$s.Save()"

echo.
echo ========================================
echo Installation Complete!
echo ========================================
echo.
echo Next steps:
echo.
echo 1. Edit configuration:
echo    notepad "C:\ProgramData\MFT-System\config\config.env"
echo.
echo 2. Start the service (if installed):
echo    net start MFT-System
echo    or
echo    Double-click the desktop shortcut "MFT System"
echo.
echo 3. Or manually start:
echo    "%INSTALL_DIR%\start-mft-system.bat"
echo.
echo 4. Access the web interface:
echo    http://localhost:5000
echo.
echo Default login:
echo    Username: sysadmin
echo    Password: Admin@123
echo    (CHANGE THIS IMMEDIATELY!)
echo.
echo ========================================
pause
