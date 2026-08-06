' Launches the fly-data-sync loop with no visible console window.
' Usage: wscript.exe start-sync-loop-hidden.vbs
Option Explicit
Dim shell, fso, scriptDir, psScript
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = scriptDir & "\sync-fly-bot-data-loop.ps1"
' 0 = hidden window, False = don't wait for completion
shell.Run "powershell.exe -ExecutionPolicy Bypass -NoProfile -File """ & psScript & """", 0, False
