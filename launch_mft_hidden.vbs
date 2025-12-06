' MFT System - Smart Launcher
' Checks if service is running, otherwise starts app with hidden console

Dim objShell, objWMI, installDir, serviceName
Set objShell = CreateObject("WScript.Shell")
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")

installDir = "C:\Program Files\MFT-System"
serviceName = "MFT-System"

' Function to check if service exists and is running
Function IsServiceRunning(svcName)
    Dim colServices, service
    Set colServices = objWMI.ExecQuery("Select * from Win32_Service Where Name = '" & svcName & "'")

    For Each service in colServices
        If service.State = "Running" Then
            IsServiceRunning = True
            Exit Function
        End If
    Next

    IsServiceRunning = False
End Function

' Check if service is running
If IsServiceRunning(serviceName) Then
    ' Service is running, just open the browser
    objShell.Run "http://localhost:5000", 1, False
Else
    ' Service not running, start the app manually with hidden console
    Dim pythonPath, appPath
    pythonPath = installDir & "\venv\Scripts\pythonw.exe"
    appPath = installDir & "\mft_system_with_rules_ui.py"

    ' Start with pythonw.exe (no console window)
    objShell.Run """" & pythonPath & """ """ & appPath & """", 0, False

    ' Wait 3 seconds for the app to start, then open browser
    WScript.Sleep 3000
    objShell.Run "http://localhost:5000", 1, False
End If

Set objShell = Nothing
Set objWMI = Nothing
