# MFT System - Distribution Package Builder Guide

## Creating the Deployment Package

This guide shows how to build a complete, offline-capable installation package for deployment to other systems.

### Prerequisites

- Completed MFT System source code
- NSSM binary (for Windows service support)
- Your custom icon file (optional)

### Step 1: Download NSSM

Download NSSM (Non-Sucking Service Manager) for bundling:

```powershell
# Download NSSM
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "nssm.zip"

# Extract
Expand-Archive -Path "nssm.zip" -DestinationPath "nssm_temp" -Force

# Copy the 64-bit version
Copy-Item "nssm_temp\nssm-2.24\win64\nssm.exe" -Destination "." -Force

# Clean up
Remove-Item "nssm.zip", "nssm_temp" -Recurse -Force

Write-Host "✅ NSSM downloaded and ready" -ForegroundColor Green
```

### Step 2: Build the Distribution Package

```powershell
cd C:\Users\bbaid\PycharmProjects\mftapp

# Pull latest changes
git pull origin claude/fix-rules-tab-display-01N1oJAxaZD1XPw2PaQx4VpZ

# Create distribution directory
Remove-Item -Path "dist\mft-system-v1.0.2" -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "dist\mft-system-v1.0.2"

# Copy application files
Copy-Item -Path "MFT_PRO" -Destination "dist\mft-system-v1.0.2\" -Recurse -Force

# Copy installer and launchers
Copy-Item -Path "install.bat" -Destination "dist\mft-system-v1.0.2\" -Force
Copy-Item -Path "launch_mft.ps1" -Destination "dist\mft-system-v1.0.2\" -Force
Copy-Item -Path "launch_mft_hidden.vbs" -Destination "dist\mft-system-v1.0.2\" -Force

# Copy NSSM (IMPORTANT for auto-start service!)
Copy-Item -Path "nssm.exe" -Destination "dist\mft-system-v1.0.2\" -Force

# Copy your custom icon (if you have one)
Copy-Item -Path "mft_icon.ico" -Destination "dist\mft-system-v1.0.2\" -Force -ErrorAction SilentlyContinue

# Copy documentation
Copy-Item -Path "SETUP_README.txt" -Destination "dist\mft-system-v1.0.2\" -Force

# Create the ZIP package
Compress-Archive -Path "dist\mft-system-v1.0.2" -DestinationPath "mft-system-v1.0.2-complete.zip" -Force

Write-Host "✅ Package created: mft-system-v1.0.2-complete.zip" -ForegroundColor Green
```

### Step 3: Verify Package Contents

The package should contain:

```
mft-system-v1.0.2/
├── MFT_PRO/              (all application files)
├── install.bat           (automated installer)
├── launch_mft.ps1        (PowerShell launcher)
├── launch_mft_hidden.vbs (VBScript launcher)
├── nssm.exe             (service manager - IMPORTANT!)
├── mft_icon.ico         (custom icon)
└── SETUP_README.txt     (user instructions)
```

Verify:
```powershell
Expand-Archive -Path "mft-system-v1.0.2-complete.zip" -DestinationPath "verify_temp" -Force
dir "verify_temp\mft-system-v1.0.2"

# Check for NSSM (critical!)
if (Test-Path "verify_temp\mft-system-v1.0.2\nssm.exe") {
    Write-Host "✅ NSSM included" -ForegroundColor Green
} else {
    Write-Host "❌ WARNING: NSSM missing!" -ForegroundColor Red
}

# Cleanup
Remove-Item "verify_temp" -Recurse -Force
```

### Step 4: Test the Package

**Test on a clean system (or VM):**

1. Extract `mft-system-v1.0.2-complete.zip`
2. Double-click `install.bat`
3. Wait for installation
4. Verify:
   - Desktop icon appears
   - Service is running: `sc query MFT-System`
   - Web interface works: http://localhost:5000
   - Desktop icon launches browser (no console)

### For Offline/Air-Gapped Networks

If deploying to systems without internet access, you can pre-download Python dependencies:

```powershell
# Create a wheels directory
New-Item -ItemType Directory -Force -Path "dist\mft-system-v1.0.2\wheels"

# Download all dependencies as wheel files
pip download -r MFT_PRO\requirements.txt -d "dist\mft-system-v1.0.2\wheels"

# Update install.bat to use local wheels:
# Change: pip install -r requirements.txt
# To:     pip install --no-index --find-links=wheels -r requirements.txt
```

### Package Checklist

Before distributing, verify:

- [ ] NSSM.exe is included (enables auto-start service)
- [ ] install.bat has auto-elevation (line 8-14)
- [ ] Custom icon is included (mft_icon.ico)
- [ ] SETUP_README.txt is included
- [ ] MFT_PRO folder has all files
- [ ] requirements.txt has all dependencies
- [ ] Tested on clean Windows system

### Distribution

The final package (`mft-system-v1.0.2-complete.zip`) can be:

- Copied to USB drives
- Distributed via file shares
- Deployed via GPO
- Emailed to users
- Stored on network shares

**Package size:** ~250 KB (without wheels), ~50 MB (with offline wheels)

### User Instructions (Summary)

Provide users with these simple steps:

1. Extract ZIP file
2. Double-click `install.bat`
3. Wait for completion
4. Double-click "MFT System" desktop icon

That's it! Everything is automated.

---

## Maintenance Updates

To create updated packages:

1. Make changes to source code
2. Commit and push to Git
3. Update version number in install.bat
4. Run package build script (Step 2)
5. Test the new package
6. Distribute to users

Users can run the installer again to upgrade (it will replace existing installation).
