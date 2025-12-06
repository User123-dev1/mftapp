' MFT System - Hidden Console Launcher
' This VBScript starts the MFT system without showing a console window

Dim objShell, installDir, scriptPath
Set objShell = CreateObject("WScript.Shell")

' Get the installation directory
installDir = "C:\Program Files\MFT-System"

' Change to installation directory and run the startup script hidden
scriptPath = installDir & "\venv\Scripts\python.exe"
appPath = installDir & "\mft_system_with_rules_ui.py"

' Run without showing console window (0 = hidden, False = don't wait)
objShell.Run """" & scriptPath & """ """ & appPath & """", 0, False

Set objShell = Nothing
