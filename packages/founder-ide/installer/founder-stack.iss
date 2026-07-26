; founder-stack.iss
;
; Bootstrap installer for the single Founder IDE application.
; The Founder Node runtime is embedded inside Founder IDE as a hidden relay.
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
;   {#FOUNDER_IDE_SETUP}   - Founder-IDE-Setup-x64-<v>.exe  (from gulp vscode-win32-x64-user-setup)
;   {#FORGEJO_BIN}         - forgejo-x64.exe               (optional; Private/Hybrid mode)
;   {#CLOUDFLARED_BIN}     - cloudflared.exe               (optional; Private/Hybrid mode)
;
; Outputs:
;   dist\Founder-IDE-Setup-<v>.exe
;
; The inner IDE installer owns the only Apps entry and uninstaller.
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
;
; If the Forgejo + cloudflared binaries are not staged in the build context
; (a Public-only release), the Private option is annotated "[UNAVAILABLE]"
; at runtime and a one-time info dialog explains why. NextButtonClick
; hard-refuses to proceed if the user somehow selects Private anyway. We
; never silently skip a component the user selected.

#ifndef FOUNDER_STACK_VERSION
  #define FOUNDER_STACK_VERSION "0.1.0"
#endif

#ifndef FOUNDER_IDE_SETUP
  #define FOUNDER_IDE_SETUP  "..\..\..\staging\Founder-IDE-Setup-x64.exe"
#endif

#ifndef FORGEJO_BIN
  #define FORGEJO_BIN "..\..\..\staging\forgejo-x64.exe"
#endif

#ifndef CLOUDFLARED_BIN
  #define CLOUDFLARED_BIN "..\..\..\staging\cloudflared.exe"
#endif

#ifndef FOUNDER_WORKBENCH_PATCH
  #define FOUNDER_WORKBENCH_PATCH "..\..\..\staging\founder-workbench.desktop.main.js"
#endif

#ifndef FOUNDER_WORKBENCH_STYLES_PATCH
  #define FOUNDER_WORKBENCH_STYLES_PATCH "..\..\..\staging\founder-workbench.desktop.main.css"
#endif

#ifndef FOUNDER_PRODUCT_PATCH
  #define FOUNDER_PRODUCT_PATCH "..\..\..\staging\founder-product.json"
#endif

#ifndef FOUNDER_HUB_PATCH
  #define FOUNDER_HUB_PATCH "..\..\..\staging\founder-hub.js"
#endif

#ifndef FOUNDER_COMPRESSION
  #define FOUNDER_COMPRESSION "lzma2/ultra64"
#endif

#ifndef FOUNDER_SOLID_COMPRESSION
  #define FOUNDER_SOLID_COMPRESSION "yes"
#endif

#ifndef FOUNDER_INSTALLER_SUFFIX
  #define FOUNDER_INSTALLER_SUFFIX ""
#endif

[Setup]
AppId={{A01A0D39-2EE3-4C82-8A2B-24AFA46E22B9}
AppName=Founder IDE
AppVersion={#FOUNDER_STACK_VERSION}
AppPublisher=Doxxed Crypto
AppPublisherURL=https://doxxedcrypto.digital
AppSupportURL=https://doxxedcrypto.digital/docs/founder-stack
AppUpdatesURL=https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest
AppCopyright=Copyright (C) Doxxed Crypto
DefaultDirName={tmp}\FounderIDEBootstrap
DefaultGroupName=Founder IDE
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Founder-IDE-Setup-{#FOUNDER_STACK_VERSION}{#FOUNDER_INSTALLER_SUFFIX}
Compression={#FOUNDER_COMPRESSION}
SolidCompression={#FOUNDER_SOLID_COMPRESSION}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
DisableDirPage=yes
DisableReadyPage=yes
CreateAppDir=no
Uninstallable=no
CreateUninstallRegKey=no
; The bootstrapper does not create a second installed product.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ─── Components (Phase 7 — mode-driven) ───────────────────────────────────
; Components map 1:1 to the doc §7 installer screen. The mode-selection page
; (see [Code] below) toggles these via /COMPONENTS at the silent-install path
; or by clicking the radio buttons in the UI. "private_core" is the Forgejo +
; cloudflared pair; "tailscale" is optional in every mode that includes it.
[Components]
; Always installed — Founder IDE (required, the editor itself).
Name: "core"; Description: "Founder IDE (required)"; Types: full compact custom; Flags: fixed
; Private-mode core: Forgejo (local git forge, ~100 MB) + cloudflared (tunnel, ~30 MB).
; Selected for PRIVATE and HYBRID; deselected for PUBLIC. Disabled at the
; wizard level if the binaries are not staged in this build.
Name: "private_core"; Description: "Forgejo (local git forge) + cloudflared (tunnel)"; Types: full
; Tailscale is always optional — it requires an OS-level install + login that
; some founders won't want during first-run. Offered in every mode.
Name: "tailscale"; Description: "Tailscale (phone remote mesh — optional, requires login)"; Types: full

[Types]
Name: "full"; Description: "Hybrid / Private mode (Forgejo + cloudflared + Tailscale optional)"
Name: "compact"; Description: "Public mode (no extra tooling — use your existing GitHub/Vercel/Neon)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Files]
; Stage the Founder IDE sub-installer inside our bundle so we can ExecWait it.
Source: "{#FOUNDER_IDE_SETUP}";  DestDir: "{tmp}"; Flags: deleteafterinstall nocompression; Components: core
; A warm payload may have been compiled before a small Founder shell repair.
; The bootstrapper overlays the source-controlled, fail-closed workbench after
; the inner installer finishes so a cache cannot silently resurrect that bug.
Source: "{#FOUNDER_WORKBENCH_PATCH}"; DestDir: "{tmp}"; DestName: "founder-workbench.desktop.main.js"; Flags: deleteafterinstall; Components: core
Source: "{#FOUNDER_WORKBENCH_STYLES_PATCH}"; DestDir: "{tmp}"; DestName: "founder-workbench.desktop.main.css"; Flags: deleteafterinstall; Components: core
Source: "{#FOUNDER_PRODUCT_PATCH}"; DestDir: "{tmp}"; DestName: "founder-product.json"; Flags: deleteafterinstall; Components: core
Source: "{#FOUNDER_HUB_PATCH}"; DestDir: "{tmp}"; DestName: "founder-hub.js"; Flags: deleteafterinstall; Components: core
; Phase 7 — Private-mode binaries. Only staged when the build actually has
; them on disk. ISCC validates Source paths at COMPILE time, so we use a
; preprocessor #ifexist check to skip the lines entirely when the binaries
; aren't present (PUBLIC-only release). At install time the matching Check:
; functions below also gate the Components selection.
#ifexist "{#FORGEJO_BIN}"
  Source: "{#FORGEJO_BIN}";     DestDir: "{localappdata}\Programs\Founder IDE\resources\founder-tools\bin"; Flags: ignoreversion nocompression; Components: private_core; Check: ForgejoBinAvailable
#endif
#ifexist "{#CLOUDFLARED_BIN}"
  Source: "{#CLOUDFLARED_BIN}"; DestDir: "{localappdata}\Programs\Founder IDE\resources\founder-tools\bin"; Flags: ignoreversion nocompression; Components: private_core; Check: CloudflaredBinAvailable
#endif

[Run]
; --- Founder IDE -------------------------------------------------------------
; /SILENT = Inno Setup silent install (Founder IDE's own installer is Inno).
; /CURRENTUSER = install to per-user dir (no admin elevation).
Filename: "{tmp}\Founder-IDE-Setup-x64.exe"; \
  Parameters: "/SILENT /CURRENTUSER /NORESTART /SP-"; \
  StatusMsg: "Installing Founder IDE..."; \
  Flags: waituntilterminated; Components: core; \
  AfterInstall: FinalizeFounderInstall

; --- Phase 7: Forgejo as a background service (Private/Hybrid only) ---------
; Registers Forgejo to run at login as a background process bound to
; localhost:3000. Uses a per-user Run key (no admin). If Forgejo isn't
; staged (PUBLIC mode, or the bin wasn't staged), this step is skipped.
; TODO(Phase 7 runtime): ship a tiny wrapper (founder-forgejo-runner.exe) that
; starts `forgejo web` with the right config + ports. For now we just lay down
; the binary; Founder IDE probes it via /api/deployment-mode/runtime-status.
Filename: "{localappdata}\Programs\Founder IDE\resources\founder-tools\bin\forgejo-x64.exe"; \
  Parameters: "web -c ""{localappdata}\Programs\Founder IDE\resources\founder-tools\forgejo\app.ini"""; \
  Description: "Start Forgejo local git forge (localhost:3000)"; \
  Flags: postinstall nowait skipifsilent runascurrentuser; \
  Components: private_core; Check: ForgejoBinAvailable

; --- Phase 7: optional Tailscale silent install -----------------------------
; Only if the user opted in (tailscale component) AND the Tailscale installer
; is reachable. We download it on first run rather than bundling ~25 MB that
; most founders don't need. Degrades gracefully if offline.
; TODO(Phase 7 runtime): wire this to a "Enable Tailscale" action instead of
; running it from the installer.

[InstallDelete]
; One-app migration: remove legacy user-facing products after their standalone
; Node uninstaller has run in PrepareToInstall. FounderVault is never touched.
; Replace the embedded relay atomically on upgrades. Inno Setup otherwise
; preserves files removed from later relay builds, including retired artwork.
Type: filesandordirs; Name: "{localappdata}\Programs\Founder IDE\resources\founder-relay"
Type: filesandordirs; Name: "{localappdata}\Programs\Founder Node"
Type: filesandordirs; Name: "{localappdata}\Founder Stack"
Type: filesandordirs; Name: "{localappdata}\FounderIDE"
Type: filesandordirs; Name: "{localappdata}\founder node-updater"
Type: files; Name: "{userdesktop}\Founder Node.lnk"
Type: files; Name: "{userdesktop}\Founder Stack.lnk"
Type: files; Name: "{userprograms}\Founder Node.lnk"
Type: filesandordirs; Name: "{userprograms}\Founder Stack"
Type: files; Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Founder Node.lnk"

[Registry]
; Phase 7 — remember the mode choice as a per-user default for new projects.
; The Founder OS setup wizard reads this on first project creation.
Root: HKCU; Subkey: "Software\DoxxedCrypto\FounderStack"; \
  ValueType: string; ValueName: "DefaultDeploymentMode"; ValueData: "{code:SelectedMode}"; \
  Flags: uninsdeletekey

[Tasks]
; No installer-visible tasks. The inner Founder IDE installer owns shortcuts,
; uninstall behavior, and the embedded relay runtime.

[Code]
// ─── Phase 7 — Deployment-mode selection wizard page ───────────────────────
// Implements the three-card choice from doc §7 (Private / Public / Hybrid).
// The selection drives the [Components] set via SetupTypes-equivalent radio
// buttons, which in turn control which binaries get installed.
//
// Truthfulness contract (0.9.2):
//   - If the Forgejo + cloudflared binaries are NOT staged in this build, the
//     Private option is annotated "[UNAVAILABLE]" and a one-time info dialog
//     explains why. NextButtonClick hard-refuses to proceed if the user
//     somehow selects Private anyway. We never silently skip a component the
//     user selected.
//   - The Founder relay is embedded in Founder IDE in every mode. It is not a
//     second installed application and is not tied to the mode selection.

var
  ModePage: TInputOptionWizardPage;
  SelectedDeploymentMode: string;

function SelectedMode(Param: string): string;
begin
  if SelectedDeploymentMode <> '' then begin
    Result := SelectedDeploymentMode;
  end else if WizardIsComponentSelected('private_core') then begin
    Result := 'HYBRID';
  end else begin
    Result := 'PUBLIC';
  end;
end;

// Returns True if the Forgejo + cloudflared binaries are present in this
// build. Used to disable the Private radio button when they are missing.
// Forward declarations: the actual function bodies live further down in
// the [Code] section, but ISCC's Pascal parser requires identifiers to be
// declared before use. Without these it aborts with
// "Unknown identifier 'ForgejoBinAvailable'" at this call site.
function ForgejoBinAvailable(): Boolean; forward;
function CloudflaredBinAvailable(): Boolean; forward;

function PrivateModeBinariesAvailable(): Boolean;
begin
  Result := ForgejoBinAvailable() and CloudflaredBinAvailable();
end;

procedure InitializeWizard();
var
  PrivateHint: string;
begin
  // Three-card mode selection (doc §7). Index 0 = Private, 1 = Public,
  // 2 = Hybrid (recommended). Hybrid is the default — it matches the project
  // setup-wizard recommendation ("build privately, publish to cloud when ready")
  // only when the required private-mode binaries are actually bundled.
  ModePage := CreateInputOptionPage(wpWelcome,
    'Which mode will you primarily use?',
    'You can change this per-project later in Founder OS.',
    'Private = everything on your laptop ($0/mo). Public = cloud (GitHub + Vercel + Neon). Hybrid = build private, publish to cloud when ready (recommended). The secure website relay is built into Founder IDE in every mode.',
    True, False);
  if PrivateModeBinariesAvailable() then begin
    PrivateHint := '';
  end else begin
    PrivateHint := '   [UNAVAILABLE — Forgejo + cloudflared binaries not bundled in this build]';
  end;
  ModePage.Add('🖥  Private  — everything on laptop. Installs Forgejo + cloudflared (+ Tailscale optional). ~155 MB extra.' + PrivateHint);
  ModePage.Add('☁️  Public  — use your existing GitHub / Vercel / Neon. Nothing extra to install.');
  ModePage.Add('🔀  Hybrid  (Recommended) — build private, publish to cloud. Installs Forgejo + cloudflared (+ Tailscale optional). ~155 MB extra.');

  // Default to Hybrid (index 2). If Private binaries are missing we also
  // silently fall back to Hybrid (the user can still pick Public).
  if PrivateModeBinariesAvailable() then begin
    ModePage.SelectedValueIndex := 2;
    SelectedDeploymentMode := 'HYBRID';
  end else begin
    ModePage.SelectedValueIndex := 1;
    SelectedDeploymentMode := 'PUBLIC';
  end;

  // If the Private binaries are missing, pop a one-time info dialog so the
  // user understands why Private is unavailable.
  if (not WizardSilent()) and (not PrivateModeBinariesAvailable()) then begin
    MsgBox('Private and Hybrid modes require Forgejo + cloudflared binaries which are not bundled in this build.'#13#10#13#10'Public mode is fully available. You can add private-mode tooling later from Founder IDE.', mbInformation, MB_OK);
  end;
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

    // Refuse to proceed if the user somehow selected Private but the binaries
    // are missing. (The option is annotated [UNAVAILABLE] and an info dialog
    // explained why at page entry, but this is the belt-and-suspenders check.)
    if ((SelectedDeploymentMode = 'PRIVATE') or (SelectedDeploymentMode = 'HYBRID')) and (not PrivateModeBinariesAvailable()) then begin
      MsgBox('Private and Hybrid modes require Forgejo + cloudflared binaries which are not bundled in this build.'#13#10#13#10'Choose Public mode for this installer.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    // Apply the choice to the component set. PRIVATE and HYBRID include the
    // private_core component (Forgejo + cloudflared); PUBLIC deselects it.
    // WizardSetupType() is read-only per Inno Setup docs, so we drive the
    // TypesCombo directly: full=0, compact=1, custom=2 (see [Types] section).
    if (SelectedDeploymentMode = 'PRIVATE') or (SelectedDeploymentMode = 'HYBRID') then begin
      WizardForm.TypesCombo.ItemIndex := 0;
    end else begin
      WizardForm.TypesCombo.ItemIndex := 1;
    end;
    WizardForm.TypesCombo.OnChange(WizardForm.TypesCombo);
  end;
end;

// ─── Phase 7 — Private-mode binary availability checks ─────────────────────
// These gate the [Files] entries so the installer never hard-errors when the
// build didn't stage the Forgejo / cloudflared binaries (e.g. a PUBLIC-mode-
// only release). The user still gets a working install; Private mode just
// won't have local git/tunnel until they add Private-mode tooling later.

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

procedure WriteFounderReleaseMarker;
var
  MarkerPath: string;
begin
  MarkerPath := ExpandConstant(
    '{localappdata}\Programs\Founder IDE\founder-release.json'
  );
  if not SaveStringToFile(
    MarkerPath,
    '{"version":"{#FOUNDER_STACK_VERSION}"}',
    False
  ) then begin
    RaiseException('Could not write Founder IDE release identity: ' + MarkerPath);
  end;
  Log('Founder IDE release identity written: {#FOUNDER_STACK_VERSION}');
end;

procedure InstallFounderWorkbenchPatch;
var
  SourcePath: string;
  DestinationPath: string;
begin
  SourcePath := ExpandConstant('{tmp}\founder-workbench.desktop.main.js');
  DestinationPath := ExpandConstant(
    '{localappdata}\Programs\Founder IDE\resources\app\out\vs\workbench\workbench.desktop.main.js'
  );
  if not FileExists(SourcePath) then begin
    RaiseException('Founder IDE workbench correction is missing: ' + SourcePath);
  end;
  if not CopyFile(SourcePath, DestinationPath, False) then begin
    RaiseException('Could not install the Founder IDE workbench correction: ' + DestinationPath);
  end;
  Log('Founder IDE workbench correction installed.');
end;

procedure InstallFounderProductPatch;
var
  SourcePath: string;
  DestinationPath: string;
begin
  SourcePath := ExpandConstant('{tmp}\founder-product.json');
  DestinationPath := ExpandConstant(
    '{localappdata}\Programs\Founder IDE\resources\app\product.json'
  );
  if not FileExists(SourcePath) then begin
    RaiseException('Founder IDE integrity manifest is missing: ' + SourcePath);
  end;
  if not CopyFile(SourcePath, DestinationPath, False) then begin
    RaiseException('Could not install the Founder IDE integrity manifest: ' + DestinationPath);
  end;
  Log('Founder IDE integrity manifest installed.');
end;

procedure InstallFounderWorkbenchStylesPatch;
var
  SourcePath: string;
  DestinationPath: string;
begin
  SourcePath := ExpandConstant('{tmp}\founder-workbench.desktop.main.css');
  DestinationPath := ExpandConstant(
    '{localappdata}\Programs\Founder IDE\resources\app\out\vs\workbench\workbench.desktop.main.css'
  );
  if not FileExists(SourcePath) then begin
    RaiseException('Founder IDE scoped stylesheet is missing: ' + SourcePath);
  end;
  if not CopyFile(SourcePath, DestinationPath, False) then begin
    RaiseException('Could not install the Founder IDE scoped stylesheet: ' + DestinationPath);
  end;
  Log('Founder IDE scoped stylesheet installed.');
end;

procedure InstallFounderHubPatch;
var
  SourcePath: string;
  DestinationPath: string;
begin
  SourcePath := ExpandConstant('{tmp}\founder-hub.js');
  DestinationPath := ExpandConstant(
    '{localappdata}\Programs\Founder IDE\resources\app\extensions\founder-ide-extension\out\founder-hub.js'
  );
  if not FileExists(SourcePath) then begin
    RaiseException('Founder navigation correction is missing: ' + SourcePath);
  end;
  if not CopyFile(SourcePath, DestinationPath, False) then begin
    RaiseException('Could not install the Founder navigation correction: ' + DestinationPath);
  end;
  Log('Founder IDE navigation correction installed.');
end;

procedure InstallFounderShortcuts;
var
  TargetPath: string;
  WorkingDirectory: string;
  DesktopShortcut: string;
  StartMenuShortcut: string;
  TaskbarShortcut: string;
begin
  TargetPath := FounderIdeExe('');
  WorkingDirectory := ExtractFileDir(TargetPath);
  DesktopShortcut := ExpandConstant('{userdesktop}\Founder IDE.lnk');
  StartMenuShortcut := ExpandConstant('{userprograms}\Founder IDE.lnk');
  TaskbarShortcut := ExpandConstant(
    '{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Founder IDE.lnk'
  );

  if not FileExists(TargetPath) then begin
    RaiseException('Founder IDE executable is missing: ' + TargetPath);
  end;

  if not ForceDirectories(ExtractFileDir(StartMenuShortcut)) then begin
    RaiseException('Could not prepare the Founder IDE Start menu folder.');
  end;
  if not ForceDirectories(ExtractFileDir(TaskbarShortcut)) then begin
    RaiseException('Could not prepare the Founder IDE taskbar folder.');
  end;

  DeleteFile(DesktopShortcut);
  DeleteFile(StartMenuShortcut);
  DeleteFile(TaskbarShortcut);

  CreateShellLink(
    DesktopShortcut,
    'Founder IDE',
    TargetPath,
    '',
    WorkingDirectory,
    TargetPath,
    0,
    SW_SHOWNORMAL
  );
  CreateShellLink(
    StartMenuShortcut,
    'Founder IDE',
    TargetPath,
    '',
    WorkingDirectory,
    TargetPath,
    0,
    SW_SHOWNORMAL
  );
  CreateShellLink(
    TaskbarShortcut,
    'Founder IDE',
    TargetPath,
    '',
    WorkingDirectory,
    TargetPath,
    0,
    SW_SHOWNORMAL
  );

  Log('Founder IDE shortcuts created for the current user.');
end;

procedure FinalizeFounderInstall;
begin
  InstallFounderWorkbenchPatch;
  InstallFounderWorkbenchStylesPatch;
  InstallFounderProductPatch;
  InstallFounderHubPatch;
  InstallFounderShortcuts;
  WriteFounderReleaseMarker;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not FileExists(ExpandConstant('{#FOUNDER_IDE_SETUP}')) then begin
    MsgBox('Founder IDE setup not found:'#13#10'{#FOUNDER_IDE_SETUP}'#13#10#13#10'Run build-founder-ide.ps1 first.', mbError, MB_OK);
    Result := False;
  end;
  // Non-fatal: Private-mode binaries are optional. If missing, we still install
  // the core app; the user can add Private-mode tooling later
  // via Founder IDE. The wizard will have disabled the Private radio button.
  if not ForgejoBinAvailable() then begin
    Log('Phase 7: Forgejo binary not staged at {#FORGEJO_BIN} — Private mode will be disabled in the wizard.');
  end;
  if not CloudflaredBinAvailable() then begin
    Log('Phase 7: cloudflared binary not staged at {#CLOUDFLARED_BIN} — Private mode will be disabled in the wizard.');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  LegacyNodeUninstaller: string;
  ResultCode: Integer;
begin
  Result := '';
  LegacyNodeUninstaller := ExpandConstant(
    '{localappdata}\Programs\Founder Node\Uninstall Founder Node.exe'
  );
  if FileExists(LegacyNodeUninstaller) then begin
    Log('One-app migration: uninstalling legacy standalone Founder Node.');
    if not Exec(
      LegacyNodeUninstaller,
      '/S',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) then begin
      Log('One-app migration: legacy Founder Node uninstaller could not be launched.');
    end else if ResultCode <> 0 then begin
      Log(
        'One-app migration: legacy Founder Node uninstaller returned ' +
        IntToStr(ResultCode) + '; cleanup will continue.'
      );
    end;
  end;

  RegDeleteValue(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Run',
    'Founder Node'
  );
  RegDeleteKeyIncludingSubkeys(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A01A0D39-2EE3-4C82-8A2B-24AFA46E22B9}_is1'
  );
end;
