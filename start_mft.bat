@echo off
REM MFT Professional System - Startup Script
REM This script activates the virtual environment and starts the MFT system

REM Change to the installation directory
cd /d "%~dp0"

echo ========================================
echo MFT Professional System - Starting
echo ========================================
echo.

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found!
    echo.
    echo Please run install.bat first to set up the system.
    echo.
    pause
    exit /b 1
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Check if activation was successful
where python >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)

echo.
echo Starting MFT System...
echo.
echo Web Interface: http://127.0.0.1:5000
echo.
echo Press Ctrl+C to stop the server
echo ========================================
echo.

REM Start the MFT system
python MFT_PRO\mft_system_with_rules_ui.py

REM If the script exits, deactivate virtual environment
deactivate
pause
