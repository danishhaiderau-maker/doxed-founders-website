; founder-stack.iss
;
; Inno Setup script for the BUNDLED "Founder Stack" installer.
; Composes Founder IDE (VSCodium downstream, Inno Setup) + Founder Node
; (electron-builder NSIS) into ONE .exe that lays down both apps.
;
; Build with:
;   iscc packages\founder-ide\installer\founder-stack.iss
;
; Or via the orchestrator:
;   .\packages\founder-ide\installer\build-stack-installer.ps1
;
; Design: docs/FOUNDER-IDE-FORK-PLAN.md §6.3.
;
; Inputs (place these in the build staging dir before running iscc):
;   {#FOUNDER_IDE_SETUP}   - Founder-IDE-Setup-x64-<v>.exe  (from build/build-founder-ide.sh)
;   {#FOUNDER_NODE_SETUP}  - Founder-Node-<v>-win-x64.exe   (from apps/founder-node: npm run pack:win)
;
; Outputs:
;   dist\Founder-Stack-Setup-<v>.exe  (one installer, two apps)
;
; The two apps are installed side-by-side and each gets its own uninstall
; entry (their own AppId / appId). Founder Node can optionally auto-start on
; login (registry Run key, gated by a checkbox).

#ifndef FOUNDER_STACK_VERSION
  #define FOUNDER_STACK_VERSION "0.1.0"
#endif

#ifndef FOUNDER_IDE_SETUP
  #define FOUNDER_IDE_SETUP  "..\..\..\staging\Founder-IDE-Setup-x64.exe"
#endif

#ifndef FOUNDER_NODE_SETUP
  #define FOUNDER_NODE_SETUP "..\..\..\staging\Founder-Node-win-x64.exe"
#endif

[Setup]
AppId={{A01A0D39-2EE3-4C82-8A2B-24AFA46E22B9}
AppName=Founder Stack
AppVersion={#FOUNDER_STACK_VERSION}
AppPublisher=Doxxed Crypto
AppPublisherURL=https://doxxedcrypto.digital
AppSupportURL=https://doxxedcrypto.digital/docs/founder-stack
AppUpdatesURL=https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest
AppCopyright=Copyright (C) Doxxed Crypto
DefaultDirName={localappdata}\Founder Stack
DefaultGroupName=Founder Stack
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Founder-Stack-Setup-{#FOUNDER_STACK_VERSION}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
DisableDirPage=yes
DisableReadyPage=yes
Uninstallable=yes
CreateUninstallRegKey=yes
; The bundle itself is uninstallable and removes the Start Menu group, but
; the two sub-installers retain their own Add/Remove Programs entries so
; users can update them independently.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Stage the two sub-installers inside our bundle so we can ExecWait them.
Source: "{#FOUNDER_IDE_SETUP}";  DestDir: "{tmp}"; Flags: deleteafterinstall nocompression
Source: "{#FOUNDER_NODE_SETUP}"; DestDir: "{tmp}"; Flags: deleteafterinstall nocompression

[Run]
; --- Founder IDE -------------------------------------------------------------
; /SILENT = Inno Setup silent install (Founder IDE's own installer is Inno).
; /CURRENTUSER = install to per-user dir (no admin elevation).
Filename: "{tmp}\Founder-IDE-Setup-x64.exe"; \
  Parameters: "/SILENT /CURRENTUSER /NORESTART /SP-"; \
  StatusMsg: "Installing Founder IDE..."; \
  Flags: waituntilterminated

; --- Founder Node ------------------------------------------------------------
; electron-builder NSIS uses /S for silent. /allusers=0 -> per-user.
Filename: "{tmp}\Founder-Node-win-x64.exe"; \
  Parameters: "/S /allusers=0"; \
  StatusMsg: "Installing Founder Node..."; \
  Flags: waituntilterminated

; --- Optional: launch Founder Node after install (gated by checkbox) --------
Filename: "{code:FounderNodeExe}"; \
  Description: "Start Founder Node now (pairs your vault)"; \
  Flags: postinstall nowait skipifsilent unchecked runascurrentuser

[Icons]
; One Start Menu group for the bundle, plus shortcuts to the two apps.
Name: "{group}\Founder IDE";  Filename: "{code:FounderIdeExe}"
Name: "{group}\Founder Node"; Filename: "{code:FounderNodeExe}"
Name: "{group}\Founder Stack README"; Filename: "https://doxxedcrypto.digital/docs/founder-stack"

[UninstallRun]
; Best-effort uninstall of the two sub-apps. These invoke each app's own
; uninstaller silently. Paths use the per-user install locations.
Filename: "{code:FounderIdeUninstaller}";   Parameters: "/SILENT /CURRENTUSER"; Flags: waituntilterminated; RunOnceId: "UninstallFounderIde"
Filename: "{code:FounderNodeUninstaller}";  Parameters: "/S";                     Flags: waituntilterminated; RunOnceId: "UninstallFounderNode"

[UninstallDelete]
Type: dirifempty; Name: "{localappdata}\Founder Stack"

[Registry]
; Optional auto-start for Founder Node on login (per-user Run key).
; Disabled by default — the checkbox in [Tasks] opts in.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "FounderNode"; ValueData: "{code:FounderNodeExe}"; \
  Flags: uninsdeletevalue; Tasks: autostartnode

[Tasks]
Name: "autostartnode"; Description: "Start Founder Node automatically when I log in to Windows"; GroupDescription: "Startup:"

[Code]
function FounderIdeExe(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder IDE\Founder IDE.exe');
end;

function FounderNodeExe(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder Node\Founder Node.exe');
end;

function FounderIdeUninstaller(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder IDE\unins000.exe');
end;

function FounderNodeUninstaller(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder Node\Uninstall Founder Node.exe');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not FileExists(ExpandConstant('{#FOUNDER_IDE_SETUP}')) then begin
    MsgBox('Founder IDE setup not found:'#13#10'{#FOUNDER_IDE_SETUP}'#13#10#13#10'Run build-founder-ide.ps1 first.', mbError, MB_OK);
    Result := False;
  end;
  if not FileExists(ExpandConstant('{#FOUNDER_NODE_SETUP}')) then begin
    MsgBox('Founder Node setup not found:'#13#10'{#FOUNDER_NODE_SETUP}'#13#10#13#10'Run npm run pack:win in apps\founder-node first.', mbError, MB_OK);
    Result := False;
  end;
end;
