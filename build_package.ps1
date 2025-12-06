# MFT System - Automated Package Builder
# Creates a complete deployment package with all dependencies

param(
    [string]$Version = "1.0.2",
    [switch]$Offline  # Include Python wheels for offline installation
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "MFT System Package Builder v$Version" -ForegroundColor Cyan
if ($Offline) {
    Write-Host "(OFFLINE MODE - Including Python wheels)" -ForegroundColor Yellow
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Download NSSM if not present
if (-not (Test-Path "nssm.exe")) {
    Write-Host "📥 Downloading NSSM..." -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "nssm.zip"
        Expand-Archive -Path "nssm.zip" -DestinationPath "nssm_temp" -Force
        Copy-Item "nssm_temp\nssm-2.24\win64\nssm.exe" -Destination "." -Force
        Remove-Item "nssm.zip", "nssm_temp" -Recurse -Force
        Write-Host "✅ NSSM downloaded" -ForegroundColor Green
    }
    catch {
        Write-Host "⚠️  Could not download NSSM automatically" -ForegroundColor Yellow
        Write-Host "   Please download manually from https://nssm.cc/" -ForegroundColor Yellow
        Write-Host "   and place nssm.exe in this directory" -ForegroundColor Yellow
        pause
        exit 1
    }
} else {
    Write-Host "✅ NSSM already present" -ForegroundColor Green
}

# Step 2: Create distribution directory
Write-Host ""
Write-Host "📦 Creating distribution package..." -ForegroundColor Yellow

$distDir = "dist\mft-system-v$Version"
Remove-Item -Path $distDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $distDir | Out-Null

# Step 3: Copy files
Write-Host "   Copying application files..." -ForegroundColor Gray
Copy-Item -Path "MFT_PRO" -Destination "$distDir\" -Recurse -Force

Write-Host "   Copying installer scripts..." -ForegroundColor Gray
Copy-Item -Path "install.bat" -Destination "$distDir\" -Force
Copy-Item -Path "launch_mft.ps1" -Destination "$distDir\" -Force
Copy-Item -Path "launch_mft_hidden.vbs" -Destination "$distDir\" -Force

Write-Host "   Copying NSSM..." -ForegroundColor Gray
Copy-Item -Path "nssm.exe" -Destination "$distDir\" -Force

Write-Host "   Copying documentation..." -ForegroundColor Gray
Copy-Item -Path "SETUP_README.txt" -Destination "$distDir\" -Force

# Copy icon if exists
if (Test-Path "mft_icon.ico") {
    Write-Host "   Copying custom icon..." -ForegroundColor Gray
    Copy-Item -Path "mft_icon.ico" -Destination "$distDir\" -Force
}

# Copy wheels for offline installation if requested
if ($Offline) {
    Write-Host ""
    Write-Host "🔒 Preparing offline installation..." -ForegroundColor Yellow

    if (-not (Test-Path "wheels")) {
        Write-Host "   Downloading Python dependencies..." -ForegroundColor Gray
        & .\download_dependencies.ps1 -Force
    }

    if (Test-Path "wheels") {
        Write-Host "   Copying Python wheels..." -ForegroundColor Gray
        Copy-Item -Path "wheels" -Destination "$distDir\" -Recurse -Force

        $wheelCount = (Get-ChildItem -Path "$distDir\wheels" -Filter "*.whl").Count
        $wheelSize = [math]::Round((Get-ChildItem -Path "$distDir\wheels" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 2)
        Write-Host "   ✅ $wheelCount wheel files included ($wheelSize MB)" -ForegroundColor Green
        Write-Host "   📦 Package will work WITHOUT internet access!" -ForegroundColor Green
    } else {
        Write-Host "   ⚠️  Could not download wheels. Package will require internet." -ForegroundColor Yellow
    }
}

# Step 4: Create ZIP archive
Write-Host ""
Write-Host "🗜️  Creating ZIP archive..." -ForegroundColor Yellow

$zipName = "mft-system-v$Version-complete.zip"
Remove-Item -Path $zipName -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $distDir -DestinationPath $zipName -Force

# Step 5: Calculate checksum
Write-Host "🔒 Calculating checksum..." -ForegroundColor Yellow
$hash = Get-FileHash -Path $zipName -Algorithm SHA256
$hash.Hash | Out-File "$zipName.sha256" -Encoding ASCII

# Step 6: Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "✅ Package Created Successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Package: $zipName" -ForegroundColor Cyan
Write-Host "Size:    $([math]::Round((Get-Item $zipName).Length / 1MB, 2)) MB" -ForegroundColor Cyan
Write-Host "SHA256:  $($hash.Hash)" -ForegroundColor Cyan
Write-Host ""

if ($Offline) {
    Write-Host "🔒 OFFLINE MODE: Package includes Python wheels" -ForegroundColor Green
    Write-Host "   Installation will work WITHOUT internet access!" -ForegroundColor Green
    Write-Host ""
}


# Step 7: Verify contents
Write-Host "📋 Package Contents:" -ForegroundColor Yellow
Write-Host ""

Expand-Archive -Path $zipName -DestinationPath "verify_temp" -Force
$contents = Get-ChildItem -Path "verify_temp\mft-system-v$Version" -Name
foreach ($item in $contents) {
    if ($item -like "MFT_PRO") {
        Write-Host "   ✅ $item (Application files)" -ForegroundColor Green
    }
    elseif ($item -eq "nssm.exe") {
        Write-Host "   ✅ $item (Service manager - CRITICAL)" -ForegroundColor Green
    }
    elseif ($item -eq "install.bat") {
        Write-Host "   ✅ $item (Automated installer)" -ForegroundColor Green
    }
    elseif ($item -eq "mft_icon.ico") {
        Write-Host "   ✅ $item (Custom icon)" -ForegroundColor Green
    }
    else {
        Write-Host "   ✅ $item" -ForegroundColor Gray
    }
}
Remove-Item "verify_temp" -Recurse -Force

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "📤 Ready for Distribution!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "End users can now:" -ForegroundColor White
Write-Host "1. Extract the ZIP file" -ForegroundColor White
Write-Host "2. Double-click install.bat" -ForegroundColor White
Write-Host "3. Double-click desktop icon to use" -ForegroundColor White
Write-Host ""
Write-Host "The installation is fully automated and includes:" -ForegroundColor Yellow
Write-Host "- Auto-elevation (no need to right-click -> Run as admin)" -ForegroundColor Gray
Write-Host "- NSSM bundled (auto-start on boot)" -ForegroundColor Gray
Write-Host "- Hidden console (no black window)" -ForegroundColor Gray
Write-Host "- Custom desktop icon" -ForegroundColor Gray
Write-Host "- Firewall rule creation" -ForegroundColor Gray
Write-Host "- Service installation and startup" -ForegroundColor Gray

if ($Offline) {
    Write-Host "- Python wheels bundled (offline installation)" -ForegroundColor Gray
} else {
    Write-Host "! Python dependencies require internet (use -Offline flag)" -ForegroundColor Yellow
}
Write-Host ""
