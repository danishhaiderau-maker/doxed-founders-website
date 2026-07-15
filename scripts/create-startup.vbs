Set ws = CreateObject("WScript.Shell")
nodeExe = ws.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop\Final Bots\doxedcryptofounder\apps\founder-node\release\win-unpacked\Founder Node.exe"
startupDir = ws.ExpandEnvironmentStrings("%APPDATA%") & "\Microsoft\Windows\Start Menu\Programs\Startup"

Set sc = ws.CreateShortcut(startupDir & "\Founder Node.lnk")
sc.TargetPath = nodeExe
sc.WorkingDirectory = ws.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop\Final Bots\doxedcryptofounder\apps\founder-node\release\win-unpacked"
sc.Description = "Founder Node Auto-Start"
sc.Save

WScript.Echo "Created Startup shortcut: " & startupDir & "\Founder Node.lnk"
