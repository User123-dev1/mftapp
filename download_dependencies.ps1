# Download all Python dependencies as wheel files for offline installation
# Run this on a machine with internet access to prepare offline package

param(
    [switch]$Force
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Downloading Python Dependencies" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Create wheels directory
$wheelsDir = "wheels"
if (Test-Path $wheelsDir) {
    if ($Force) {
        Write-Host "Removing existing wheels directory..." -ForegroundColor Yellow
        Remove-Item $wheelsDir -Recurse -Force
    } else {
        Write-Host "Wheels directory already exists. Use -Force to recreate." -ForegroundColor Yellow
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $wheelsDir | Out-Null

# Ensure pip is up to date
Write-Host "Updating pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Download all dependencies as wheel files
Write-Host ""
Write-Host "Downloading dependencies from PyPI..." -ForegroundColor Yellow
Write-Host ""

try {
    pip download -r MFT_PRO\requirements.txt -d $wheelsDir --only-binary=:all: --python-version 39 --platform win_amd64

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "✅ Dependencies Downloaded!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""

    # Show downloaded files
    $wheels = Get-ChildItem -Path $wheelsDir -Filter "*.whl"
    Write-Host "Downloaded $($wheels.Count) wheel files:" -ForegroundColor Cyan
    Write-Host ""

    $totalSize = 0
    foreach ($wheel in $wheels) {
        $size = [math]::Round($wheel.Length / 1KB, 2)
        $totalSize += $wheel.Length
        Write-Host "  $($wheel.Name) ($size KB)" -ForegroundColor Gray
    }

    Write-Host ""
    Write-Host "Total size: $([math]::Round($totalSize / 1MB, 2)) MB" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "These wheel files enable COMPLETELY OFFLINE installation!" -ForegroundColor Green
    Write-Host "The package will work even when:" -ForegroundColor Yellow
    Write-Host "  ✓ Internet is blocked" -ForegroundColor Gray
    Write-Host "  ✓ PyPI is inaccessible" -ForegroundColor Gray
    Write-Host "  ✓ Corporate firewall blocks downloads" -ForegroundColor Gray
    Write-Host "  ✓ Air-gapped environments" -ForegroundColor Gray
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "❌ Error downloading dependencies" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Make sure you have internet access and pip is working." -ForegroundColor Yellow
    exit 1
}
