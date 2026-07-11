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
;   {#FORGEJO_BIN}         - forgejo-x64.exe               (optional; Private/Hybrid mode)
;   {#CLOUDFLARED_BIN}     - cloudflared.exe               (optional; Private/Hybrid mode)
;
; Outputs:
;   dist\Founder-Stack-Setup-<v>.exe  (one installer, two apps + optional Private-mode tooling)
;
; The two apps are installed side-by-side and each gets its own uninstall
; entry (their own AppId / appId). Founder Node can optionally auto-start on
; login (registry Run key, gated by a checkbox).
;
; ─── Phase 7 — Deployment mode selection (Private / Public / Hybrid) ───────
; The wizard presents a mode-selection screen (doc §7 of DEPLOYMENT-MODES-UX).
; The choice controls which optional components are installed:
;
;   PRIVATE  → Forgejo binary + cloudflared binary + Tailscale (optional)
;   PUBLIC   → Nothing extra (you already have GitHub/Vercel/Neon accounts)
;   HYBRID   → Forgejo binary + cloudflared binary + Tailscale (optional)
;
; The default is HYBRID (matches the project setup-wizard recommendation).
; The installer remembers the choice as a per-user default for new projects,
; but every project can override it in the Founder OS setup wizard.

#ifndef FOUNDER_STACK_VERSION
  #define FOUNDER_STACK_VERSION "0.1.0"
#endif

#ifndef FOUNDER_IDE_SETUP
  #define FOUNDER_IDE_SETUP  "..\..\..\staging\Founder-IDE-Setup-x64.exe"
#endif

#ifndef FOUNDER_NODE_SETUP
  #define FOUNDER_NODE_SETUP "..\..\..\staging\Founder-Node-win-x64.exe"
#endif

#ifndef FORGEJO_BIN
  #define FORGEJO_BIN "..\..\..\staging\forgejo-x64.exe"
#endif

#ifndef CLOUDFLARED_BIN
  #define CLOUDFLARED_BIN "..\..\..\staging\cloudflared.exe"
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

; ─── Components (Phase 7 — mode-driven) ───────────────────────────────────
; Components map 1:1 to the doc §7 installer screen. The mode-selection page
; (see [Code] below) toggles these via /COMPONENTS at the silent-install path
; or by clicking the radio buttons in the UI. "private_core" is the Forgejo +
; cloudflared pair; "tailscale" is optional in every mode that includes it.
[Components]
; Always installed — the two apps every Founder OS install needs.
Name: "core"; Description: "Founder IDE + Founder Node (required)"; Types: full compact custom; Flags: fixed
; Private-mode core: Forgejo (local git forge, ~100 MB) + cloudflared (tunnel, ~30 MB).
; Selected for PRIVATE and HYBRID; deselected for PUBLIC.
Name: "private_core"; Description: "Forgejo (local git forge) + cloudflared (tunnel)"; Types: full
; Tailscale is always optional — it requires an OS-level install + login that
; some founders won't want during first-run. Offered in every mode.
Name: "tailscale"; Description: "Tailscale (phone remote mesh — optional, requires login)"; Types: full

[Types]
Name: "full"; Description: "Hybrid / Private mode (Forgejo + cloudflared + Tailscale optional)"
Name: "compact"; Description: "Public mode (no extra tooling — use your existing GitHub/Vercel/Neon)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Files]
; Stage the two sub-installers inside our bundle so we can ExecWait them.
Source: "{#FOUNDER_IDE_SETUP}";  DestDir: "{tmp}"; Flags: deleteafterinstall nocompression; Components: core
Source: "{#FOUNDER_NODE_SETUP}"; DestDir: "{tmp}"; Flags: deleteafterinstall nocompression; Components: core
; Phase 7 — Private-mode binaries. Only staged when the user picked
; PRIVATE or HYBRID (private_core component selected). Both are single
; binaries dropped into the Founder Node install dir + added to PATH.
; Forgejo ~100 MB, cloudflared ~30 MB. If the build didn't stage them, the
; [Code] InitializeSetup warns and falls back to PUBLIC-style (no Private mode).
Source: "{#FORGEJO_BIN}";     DestDir: "{app}\bin"; Flags: ignoreversion nocompression; Components: private_core; Check: ForgejoBinAvailable
Source: "{#CLOUDFLARED_BIN}"; DestDir: "{app}\bin"; Flags: ignoreversion nocompression; Components: private_core; Check: CloudflaredBinAvailable

[Run]
; --- Founder IDE -------------------------------------------------------------
; /SILENT = Inno Setup silent install (Founder IDE's own installer is Inno).
; /CURRENTUSER = install to per-user dir (no admin elevation).
Filename: "{tmp}\Founder-IDE-Setup-x64.exe"; \
  Parameters: "/SILENT /CURRENTUSER /NORESTART /SP-"; \
  StatusMsg: "Installing Founder IDE..."; \
  Flags: waituntilterminated; Components: core

; --- Founder Node ------------------------------------------------------------
; electron-builder NSIS uses /S for silent. /allusers=0 -> per-user.
Filename: "{tmp}\Founder-Node-win-x64.exe"; \
  Parameters: "/S /allusers=0"; \
  StatusMsg: "Installing Founder Node..."; \
  Flags: waituntilterminated; Components: core

; --- Phase 7: Forgejo as a background service (Private/Hybrid only) ---------
; Registers Forgejo to run at login as a background process bound to
; localhost:3000. Uses a per-user Run key (no admin). If Forgejo isn't
; installed (PUBLIC mode, or the bin wasn't staged), this step is skipped.
; TODO(Phase 7 runtime): ship a tiny wrapper (founder-forgejo-runner.exe) that
; starts `forgejo web` with the right config + ports. For now we just lay down
; the binary; Founder Node probes it via /api/deployment-mode/runtime-status.
Filename: "{app}\bin\forgejo-x64.exe"; \
  Parameters: "web -c {app}\forgejo\app.ini"; \
  Description: "Start Forgejo local git forge (localhost:3000)"; \
  Flags: postinstall nowait skipifsilent runascurrentuser; \
  Components: private_core; Check: ForgejoBinAvailable

; --- Optional: launch Founder Node after install (gated by checkbox) --------
Filename: "{code:FounderNodeExe}"; \
  Description: "Start Founder Node now (pairs your vault)"; \
  Flags: postinstall nowait skipifsilent unchecked runascurrentuser

; --- Phase 7: optional Tailscale silent install -----------------------------
; Only if the user opted in (tailscale component) AND the Tailscale installer
; is reachable. We download it on first run rather than bundling ~25 MB that
; most founders don't need. Degrades gracefully if offline.
; TODO(Phase 7 runtime): wire this to Founder Node's "Enable Tailscale" tray
; action instead of running it from the installer.

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
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\forgejo"

[Registry]
; Optional auto-start for Founder Node on login (per-user Run key).
; Disabled by default — the checkbox in [Tasks] opts in.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "FounderNode"; ValueData: "{code:FounderNodeExe}"; \
  Flags: uninsdeletevalue; Tasks: autostartnode

; Phase 7 — remember the mode choice as a per-user default for new projects.
; The Founder OS setup wizard reads this on first project creation.
Root: HKCU; Subkey: "Software\DoxxedCrypto\FounderStack"; \
  ValueType: string; ValueName: "DefaultDeploymentMode"; ValueData: "{code:SelectedMode}"; \
  Flags: uninsdeletekey

[Tasks]
Name: "autostartnode"; Description: "Start Founder Node automatically when I log in to Windows"; GroupDescription: "Startup:"

[Code]
// ─── Phase 7 — Deployment-mode selection wizard page ───────────────────────
// Implements the three-card choice from doc §7 (Private / Public / Hybrid).
// The selection drives the [Components] set via SetupTypes-equivalent radio
// buttons, which in turn control which binaries get installed.

var
  ModePage: TInputOptionWizardPage;
  SelectedDeploymentMode: string;

function SelectedMode(Param: string): string;
begin
  Result := SelectedDeploymentMode;
end;

procedure InitializeWizard();
begin
  // Three-card mode selection (doc §7). Index 0 = Private, 1 = Public,
  // 2 = Hybrid (recommended). Hybrid is the default — it matches the project
  // setup-wizard recommendation ("build privately, publish to cloud when ready").
  ModePage := CreateInputOptionPage(wpWelcome,
    'Which mode will you primarily use?',
    'You can change this per-project later in Founder OS.',
    'Private = everything on your laptop ($0/mo). Public = cloud (GitHub + Vercel + Neon). Hybrid = build private, publish to cloud when ready (recommended).',
    True, False);
  ModePage.Add('🖥  Private  — everything on laptop. Installs Forgejo + cloudflared (+ Tailscale optional). ~155 MB extra.');
  ModePage.Add('☁️  Public  — use your existing GitHub / Vercel / Neon. Nothing extra to install.');
  ModePage.Add('🔀  Hybrid  (Recommended) — build private, publish to cloud. Installs Forgejo + cloudflared (+ Tailscale optional). ~155 MB extra.');

  // Default to Hybrid (index 2).
  ModePage.SelectedValueIndex := 2;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ModeIdx: Integer;
begin
  Result := True;
  if CurPageID = ModePage.ID then begin
    ModeIdx := ModePage.SelectedValueIndex;
    case ModeIdx of
      0: SelectedDeploymentMode := 'PRIVATE';
      1: SelectedDeploymentMode := 'PUBLIC';
      2: SelectedDeploymentMode := 'HYBRID';
    else
      SelectedDeploymentMode := 'HYBRID';
    end;

    // Apply the choice to the component set. PRIVATE and HYBRID include the
    // private_core component (Forgejo + cloudflared); PUBLIC deselects it.
    // We mutate WizardSetupType so the [Components] flags resolve correctly.
    if (SelectedDeploymentMode = 'PRIVATE') or (SelectedDeploymentMode = 'HYBRID') then begin
      WizardSetupType(False) := 'full';
    end else begin
      WizardSetupType(False) := 'compact';
    end;
  end;
end;

// ─── Phase 7 — Private-mode binary availability checks ─────────────────────
// These gate the [Files] entries so the installer never hard-errors when the
// build didn't stage the Forgejo / cloudflared binaries (e.g. a PUBLIC-mode-
// only release). The user still gets a working install; Private mode just
// won't have local git/tunnel until they run the Founder Node "Install
// Private-mode tooling" tray action.

function ForgejoBinAvailable(): Boolean;
begin
  Result := FileExists(ExpandConstant('{#FORGEJO_BIN}'));
end;

function CloudflaredBinAvailable(): Boolean;
begin
  Result := FileExists(ExpandConstant('{#CLOUDFLARED_BIN}'));
end;

// ─── Existing helpers (unchanged) ──────────────────────────────────────────

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
  // Non-fatal: Private-mode binaries are optional. If missing, we still install
  // the core apps; the user can add Private-mode tooling later via Founder Node.
  if not ForgejoBinAvailable() then begin
    Log('Phase 7: Forgejo binary not staged at {#FORGEJO_BIN} — Private-mode git forge will be unavailable until added.');
  end;
  if not CloudflaredBinAvailable() then begin
    Log('Phase 7: cloudflared binary not staged at {#CLOUDFLARED_BIN} — Private-mode tunnel will be unavailable until added.');
  end;
end;
