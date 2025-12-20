# MFT Professional System - PowerShell Startup Script
# This script activates the virtual environment and starts the MFT system

# Change to script directory
Set-Location -Path $PSScriptRoot

Write-Host "========================================"
Write-Host "MFT Professional System - Starting"
Write-Host "========================================"
Write-Host ""

# Check if virtual environment exists
if (-not (Test-Path "venv\Scripts\Activate.ps1")) {
    Write-Host "ERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please run install.bat first to set up the system."
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Activate virtual environment
Write-Host "Activating virtual environment..."
& "venv\Scripts\Activate.ps1"

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to activate virtual environment" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "Starting MFT System..." -ForegroundColor Green
Write-Host ""
Write-Host "Web Interface: http://127.0.0.1:5000" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop the server"
Write-Host "========================================"
Write-Host ""

# Start the MFT system
python MFT_PRO\mft_system_with_rules_ui.py

# Deactivate on exit
deactivate
Read-Host "Press Enter to exit"
