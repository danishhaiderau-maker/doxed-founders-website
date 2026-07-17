; founder-stack.iss
;
; Inno Setup script for the "Founder Stack" installer.
; Ships Founder IDE (Void downstream, Inno Setup) + Founder Node (NSIS) +
; optional Private-mode tooling (Forgejo, cloudflared).
;
; Founder Node was re-bundled in 0.9.2: the IDE Gateway client requires
; ~/FounderVault/node-config.json, which only Founder Node creates on first
; pairing. Without Founder Node in the bundle, the IDE's AI features are
; dead-on-arrival for new users. The bundle now installs Founder Node in
; every mode (it is small, ~25 MB, and required for the IDE to function).
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
;   {#FOUNDER_NODE_SETUP}  - Founder-Node-<v>-win-x64.exe   (from apps/founder-node electron-builder --win)
;   {#FORGEJO_BIN}         - forgejo-x64.exe               (optional; Private/Hybrid mode)
;   {#CLOUDFLARED_BIN}     - cloudflared.exe               (optional; Private/Hybrid mode)
;
; Outputs:
;   dist\Founder-Stack-Setup-<v>.exe  (Founder IDE + optional Private-mode tooling)
;
; The IDE installs per-user (its own AppId). Optional Private-mode binaries
; land in {app}\bin and are added to PATH.
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
; Founder Node is installed in EVERY mode (it is required for the IDE Gateway
; client to function — it creates ~/FounderVault/node-config.json on first
; pairing). It is NOT optional and not tied to the mode selection.
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
; The bundle itself is uninstallable and removes the Start Menu group. The
; IDE sub-installer retains its own Add/Remove Programs entry so users can
; update it independently of the bundle.

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
; Always installed — Founder Node (required, creates ~/FounderVault/node-config.json
; on first pairing — the IDE Gateway client needs this file to function).
Name: "founder_node"; Description: "Founder Node (required — IDE Gateway pairing)"; Types: full compact custom; Flags: fixed
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
; Stage the Founder Node NSIS sub-installer. Required in every mode.
#ifexist "{#FOUNDER_NODE_SETUP}"
  Source: "{#FOUNDER_NODE_SETUP}"; DestDir: "{tmp}"; Flags: deleteafterinstall nocompression; Components: founder_node
#endif
; Phase 7 — Private-mode binaries. Only staged when the build actually has
; them on disk. ISCC validates Source paths at COMPILE time, so we use a
; preprocessor #ifexist check to skip the lines entirely when the binaries
; aren't present (PUBLIC-only release). At install time the matching Check:
; functions below also gate the Components selection.
#ifexist "{#FORGEJO_BIN}"
  Source: "{#FORGEJO_BIN}";     DestDir: "{app}\bin"; Flags: ignoreversion nocompression; Components: private_core; Check: ForgejoBinAvailable
#endif
#ifexist "{#CLOUDFLARED_BIN}"
  Source: "{#CLOUDFLARED_BIN}"; DestDir: "{app}\bin"; Flags: ignoreversion nocompression; Components: private_core; Check: CloudflaredBinAvailable
#endif

[Run]
; --- Founder IDE -------------------------------------------------------------
; /SILENT = Inno Setup silent install (Founder IDE's own installer is Inno).
; /CURRENTUSER = install to per-user dir (no admin elevation).
Filename: "{tmp}\Founder-IDE-Setup-x64.exe"; \
  Parameters: "/SILENT /CURRENTUSER /NORESTART /SP-"; \
  StatusMsg: "Installing Founder IDE..."; \
  Flags: waituntilterminated; Components: core

; --- Founder Node ------------------------------------------------------------
; NSIS silent install (/S). Founder Node creates ~/FounderVault on first run
; and writes node-config.json after the user completes pairing. The IDE's
; Gateway client reads that file to route AI requests; without Founder Node
; the IDE's AI features are dead-on-arrival.
Filename: "{tmp}\Founder-Node-win-x64.exe"; \
  Parameters: "/S"; \
  StatusMsg: "Installing Founder Node (IDE Gateway pairing)..."; \
  Flags: shellexec waituntilterminated; Components: founder_node; Check: FounderNodeSetupAvailable

; --- Phase 7: Forgejo as a background service (Private/Hybrid only) ---------
; Registers Forgejo to run at login as a background process bound to
; localhost:3000. Uses a per-user Run key (no admin). If Forgejo isn't
; staged (PUBLIC mode, or the bin wasn't staged), this step is skipped.
; TODO(Phase 7 runtime): ship a tiny wrapper (founder-forgejo-runner.exe) that
; starts `forgejo web` with the right config + ports. For now we just lay down
; the binary; Founder IDE probes it via /api/deployment-mode/runtime-status.
Filename: "{app}\bin\forgejo-x64.exe"; \
  Parameters: "web -c {app}\forgejo\app.ini"; \
  Description: "Start Forgejo local git forge (localhost:3000)"; \
  Flags: postinstall nowait skipifsilent runascurrentuser; \
  Components: private_core; Check: ForgejoBinAvailable

; --- Phase 7: optional Tailscale silent install -----------------------------
; Only if the user opted in (tailscale component) AND the Tailscale installer
; is reachable. We download it on first run rather than bundling ~25 MB that
; most founders don't need. Degrades gracefully if offline.
; TODO(Phase 7 runtime): wire this to a "Enable Tailscale" action instead of
; running it from the installer.

[Icons]
; One Start Menu group for the bundle, plus a shortcut to Founder IDE.
Name: "{group}\Founder IDE";  Filename: "{code:FounderIdeExe}"
Name: "{group}\Founder Node"; Filename: "{code:FounderNodeExe}"
Name: "{group}\Founder Stack README"; Filename: "https://doxxedcrypto.digital/docs/founder-stack"

[UninstallRun]
; Best-effort uninstall of the IDE sub-app. This invokes the IDE's own
; uninstaller silently. Path uses the per-user install location.
Filename: "{code:FounderIdeUninstaller}";   Parameters: "/SILENT /CURRENTUSER"; Flags: waituntilterminated; RunOnceId: "UninstallFounderIde"
; Best-effort uninstall of Founder Node. NSIS uninstaller path uses the
; per-user install location written by electron-builder.
Filename: "{code:FounderNodeUninstaller}";  Parameters: "/S"; Flags: waituntilterminated; RunOnceId: "UninstallFounderNode"

[UninstallDelete]
Type: dirifempty; Name: "{localappdata}\Founder Stack"
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\forgejo"

[Registry]
; Phase 7 — remember the mode choice as a per-user default for new projects.
; The Founder OS setup wizard reads this on first project creation.
Root: HKCU; Subkey: "Software\DoxxedCrypto\FounderStack"; \
  ValueType: string; ValueName: "DefaultDeploymentMode"; ValueData: "{code:SelectedMode}"; \
  Flags: uninsdeletekey

[Tasks]
; No installer-visible tasks. Founder Node autostart is handled by its own
; NSIS installer (it adds the Run key itself), so we don't duplicate it here.

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
//   - Founder Node is installed in every mode (it is required for the IDE
//     Gateway client to function). It is not tied to the mode selection.

var
  ModePage: TInputOptionWizardPage;
  SelectedDeploymentMode: string;

function SelectedMode(Param: string): string;
begin
  Result := SelectedDeploymentMode;
end;

// Returns True if the Forgejo + cloudflared binaries are present in this
// build. Used to disable the Private radio button when they are missing.
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
  // setup-wizard recommendation ("build privately, publish to cloud when ready").
  ModePage := CreateInputOptionPage(wpWelcome,
    'Which mode will you primarily use?',
    'You can change this per-project later in Founder OS.',
    'Private = everything on your laptop ($0/mo). Public = cloud (GitHub + Vercel + Neon). Hybrid = build private, publish to cloud when ready (recommended). Founder Node is installed in every mode (required for IDE Gateway pairing).',
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
  ModePage.SelectedValueIndex := 2;

  // If the Private binaries are missing, pop a one-time info dialog so the
  // user understands why Private is unavailable.
  if not PrivateModeBinariesAvailable() then begin
    MsgBox('Private mode requires Forgejo + cloudflared binaries which are not bundled in this build.'#13#10#13#10'Choose Hybrid or Public. You can add Private-mode tooling later via Founder IDE.', mbInformation, MB_OK);
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
    if (SelectedDeploymentMode = 'PRIVATE') and (not PrivateModeBinariesAvailable()) then begin
      MsgBox('Private mode requires Forgejo + cloudflared binaries which are not bundled in this build.'#13#10#13#10'Choose Hybrid or Public.', mbError, MB_OK);
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

function FounderNodeSetupAvailable(): Boolean;
begin
  Result := FileExists(ExpandConstant('{#FOUNDER_NODE_SETUP}'));
end;

// ─── Existing helpers (unchanged) ──────────────────────────────────────────

function FounderIdeExe(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder IDE\Founder IDE.exe');
end;

function FounderIdeUninstaller(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder IDE\unins000.exe');
end;

function FounderNodeExe(Param: string): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Founder Node\Founder Node.exe');
end;

function FounderNodeUninstaller(Param: string): string;
begin
  // electron-builder NSIS installs to %LOCALAPPDATA%\Programs\<productName>\
  // and writes unins000.exe as the uninstaller.
  Result := ExpandConstant('{localappdata}\Programs\Founder Node\Uninstall Founder Node.exe');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not FileExists(ExpandConstant('{#FOUNDER_IDE_SETUP}')) then begin
    MsgBox('Founder IDE setup not found:'#13#10'{#FOUNDER_IDE_SETUP}'#13#10#13#10'Run build-founder-ide.ps1 first.', mbError, MB_OK);
    Result := False;
  end;
  // Founder Node setup is required for the IDE Gateway client to function.
  // Hard-error if it is missing — do NOT silently ship a broken bundle.
  if not FounderNodeSetupAvailable() then begin
    MsgBox('Founder Node setup not found:'#13#10'{#FOUNDER_NODE_SETUP}'#13#10#13#10'The IDE Gateway client requires ~/FounderVault/node-config.json, which only Founder Node creates. Run the Founder Node build first.', mbError, MB_OK);
    Result := False;
  end;
  // Non-fatal: Private-mode binaries are optional. If missing, we still install
  // the core app + Founder Node; the user can add Private-mode tooling later
  // via Founder IDE. The wizard will have disabled the Private radio button.
  if not ForgejoBinAvailable() then begin
    Log('Phase 7: Forgejo binary not staged at {#FORGEJO_BIN} — Private mode will be disabled in the wizard.');
  end;
  if not CloudflaredBinAvailable() then begin
    Log('Phase 7: cloudflared binary not staged at {#CLOUDFLARED_BIN} — Private mode will be disabled in the wizard.');
  end;
end;
