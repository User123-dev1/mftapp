# MFT System - PowerShell Hidden Launcher
# More reliable than VBScript for hiding console

$installDir = "C:\Program Files\MFT-System"
$serviceName = "MFT-System"

# Function to check if service is running
function Test-ServiceRunning {
    param([string]$ServiceName)

    try {
        $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        return ($service -and $service.Status -eq 'Running')
    }
    catch {
        return $false
    }
}

# Check if service is running
if (Test-ServiceRunning -ServiceName $serviceName) {
    # Service is running, just open browser
    Start-Process "http://localhost:5000"
}
else {
    # Service not running, start app with hidden console
    $pythonw = Join-Path $installDir "venv\Scripts\pythonw.exe"
    $app = Join-Path $installDir "mft_system_with_rules_ui.py"

    if (Test-Path $pythonw) {
        # Start with pythonw.exe (no console window)
        $processInfo = New-Object System.Diagnostics.ProcessStartInfo
        $processInfo.FileName = $pythonw
        $processInfo.Arguments = "`"$app`""
        $processInfo.WorkingDirectory = $installDir
        $processInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $processInfo.CreateNoWindow = $true

        [System.Diagnostics.Process]::Start($processInfo) | Out-Null

        # Wait 3 seconds then open browser
        Start-Sleep -Seconds 3
        Start-Process "http://localhost:5000"
    }
    else {
        [System.Windows.Forms.MessageBox]::Show(
            "MFT System not found at: $installDir`n`nPlease reinstall the application.",
            "MFT System Error",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        )
    }
}
