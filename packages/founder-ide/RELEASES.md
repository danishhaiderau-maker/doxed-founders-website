# Founder IDE — releases

## 0.9.1 (Void fork) — FIX — BUILT

> **Status: BUILT (unsigned).** Fixes the launcher-exe regression in 0.9.0
> and drops Founder Node from the bundle (the IDE talks to the Gateway API
> directly, so a paired local node is no longer needed).

### What changed

- **FIX: launcher exe missing from inner installer.** The 0.9.0 build ran
  `gulp vscode-win32-x64-min-ci` with an empty `.build/electron` cache while
  the build machine had only Python 3.14 on PATH (which node-gyp rejects).
  The task errored mid-build with "Did you forget to signal async completion"
  right at the `electron()` step, producing a launcher-less
  `VSCode-win32-x64/` dir — no `Founder IDE.exe`. The subsequent
  `gulp vscode-win32-x64-user-setup` then packaged a broken inner installer,
  and the outer Founder Stack installer bundled that broken inner installer.
- **Rebuilt on Node 20.18.2 + Python 3.12.7.** Used portable Node 20.18.2
  at `C:\Users\user\node-v20.18.2\` and Python 3.12.7 at
  `C:\Users\user\Python312\`. Cleared `.build/electron`,
  `.build/extensions`, and `VSCode-win32-x64/`. Re-ran
  `gulp vscode-win32-x64-min-ci` — completed in ~40 min, exit 0.
- **Launcher present.** `VSCode-win32-x64\Founder IDE.exe` is
  190,528,000 bytes (~181.7 MB), as expected for a Void IDE Electron build.
- **Founder Node removed from the bundle.** All Founder Node references
  deleted from `packages/founder-ide/installer/founder-stack.iss`:
  - `FOUNDER_NODE_SETUP` `#define`
  - `[Files]` entry staging `Founder-Node-win-x64.exe`
  - `[Run]` entry invoking the Founder Node NSIS installer
  - `[Run]` optional "Start Founder Node now" postinstall entry
  - `[Icons]` Founder Node Start Menu shortcut
  - `[UninstallRun]` Founder Node uninstaller entry
  - `[Registry]` `FounderNode` Run-key (auto-start on login)
  - `[Tasks]` `autostartnode` task
  - `[Code]` `FounderNodeExe` and `FounderNodeUninstaller` helper functions
  - `[Code]` `InitializeSetup` Founder Node presence check
  - Updated the `[Components]` `core` description and the file header
    comment.
- **Optional Private-mode files now use `#ifexist`.** The Forgejo/cloudflared
  `[Files]` entries are wrapped in `#ifexist "{#FORGEJO_BIN}"` /
  `#ifexist "{#CLOUDFLARED_BIN}"` so ISCC doesn't fail at compile time when
  the binaries aren't staged (PUBLIC-only release). The install-time
  `Check:` functions are retained as a second gate.
- **Orchestrator script also pruned.**
  `packages/founder-ide/installer/build-stack-installer.ps1` lost its step 3
  (Founder Node build) and the `/DFOUNDER_NODE_SETUP` ISCC arg; the step
  counter went from 4/4 to 3/3.

### Build artifacts

- `Founder-IDE-Setup-x64.exe` — the Void-based Founder IDE installer
  (rebuilt with the launcher). 108,927,778 bytes (~103.9 MB).
- `Founder-Stack-Setup-0.9.1.exe` — IDE-only bundle (no Founder Node).
  Produced at
  `C:\Users\user\Desktop\Final Bots\doxedcryptofounder\packages\founder-ide\installer\dist\Founder-Stack-Setup-0.9.1.exe`.
  Size 111,025,351 bytes (~105.9 MB).
  SHA-256 `b5701ce07b1057ed2eab0db6cc7de24bb4e9ee390da634a6d6d89f58dd61bee5`.
  Also mirrored in the manifest at
  `packages/founder-ide/updates/founder-stack-updates.json`.

### Known limitations (0.9.1)

- **Unsigned.** Same external signing blocker as 0.9.0.
- **Private-mode binaries not bundled.** Same as 0.9.0 — the `private_core`
  component (Forgejo + cloudflared) is silently skipped via the
  `#ifexist` + `Check:` functions if the binaries aren't staged.
- **Existing 0.9.0 installs** of Founder Node are NOT auto-removed by the
  0.9.1 installer (the bundle has no record of ever installing it). Users
  who want Founder Node gone should uninstall it from Add/Remove Programs.
  The `~/FounderVault` is never touched by either installer.

### Build commands

```powershell
# Env (use Node 20.18.2 + Python 3.12.7):
$env:PYTHON = "C:\Users\user\Python312\python.exe"
$env:Path = "C:\Users\user\Python312;C:\Users\user\node-v20.18.2;$env:Path"

# Rebuild IDE + launcher (40-60 min):
Set-Location C:\Users\user\founder-ide-build\void-builder\vscode
npx.cmd gulp vscode-win32-x64-min-ci
npx.cmd gulp vscode-win32-x64-inno-updater
npx.cmd gulp vscode-win32-x64-user-setup

# Compose the Founder Stack bundle (IDE-only now):
$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
& $ISCC /DFOUNDER_STACK_VERSION="0.9.1" `
  /DFOUNDER_IDE_SETUP="<path-to-Founder-IDE-Setup-x64.exe>" `
  "packages\founder-ide\installer\founder-stack.iss"
```

## 0.9.0 (Void fork) — Phase 5 — BUILT (launcher regression)

> **Status: BUILT (unsigned).** The Void-based Founder IDE and the bundled
> `Founder-Stack-Setup-0.9.0.exe` were produced on 2026-07-15 with Node
> 20.18.2. The artifacts are not code-signed (signing is a separate external
> blocker — see "Known limitations" below). The update manifest
> (`packages/founder-ide/updates/founder-stack-updates.json`) marks 0.9.0 as
> `current` with the real SHA-256 and byte size filled in.

**Foundation pivot: VSCodium -> Void.** This release replaces the VSCodium-based
build with a fork of [Void](https://github.com/voideditor/void), giving Founder
IDE Cursor-level AI UX on day one: inline edit (Ctrl+K), autocomplete (FIM),
diff review, and a chat sidebar — all inherited from Void and rewired to talk
to the Founder OS Gateway.

### What changed

- **Editor foundation:** `voideditor/void` + `void-builder` instead of VSCodium.
  Void is a VS Code fork with a full AI-native UX already built.
- **AI integration:** the central chokepoint — Void's `sendLLMMessage` dispatch
  in `electron-main/llmMessage/sendLLMMessage.ts` — is rewired to call our
  Gateway (`/api/v1/chat/completions`, SSE) before falling through to Void's
  per-provider dispatch. When a Founder Node is paired
  (`~/FounderVault/node-config.json`), every AI request (chat, Ctrl+K,
  autocomplete, agent) flows through the Gateway and its Routing Engine.
- **Branding:** `prepare-founder-ide-void.sh` layers "Founder IDE" on top of
  Void the same way Void layers on top of VS Code — fresh GUIDs, Open VSX,
  our URLs, our icon.
- **Installer:** `Founder-Stack-Setup-0.9.0.exe` bundles the Void-based
  Founder IDE + Founder Node.

### Feature -> Gateway alias mapping

| Void feature | Gateway alias | Routes to |
|--|--|--|
| Autocomplete (FIM) | `founder-os-fast` | cheapest/fastest tier |
| Edit / Ctrl+K | `founder-os-code` | coding tier (GLM) |
| Chat (normal) | `founder-os-auto` | Routing Engine decides |
| Chat (agent) | `founder-os-reasoning` | deep reasoning (DeepSeek) |

### Build artifacts

- `Founder-IDE-Setup-x64.exe` — the Void-based Founder IDE installer.
  Produced at `C:\Users\user\founder-ide-build\void-builder\vscode\.build\win32-x64\user-setup\FounderIDESetup.exe`
  (named `FounderIDESetup.exe` by the VS Code Inno Setup template;
  `product.nameShort=Founder IDE`, `product.applicationName=founder-ide`,
  version `1.99.3` inherited from the VS Code base).
  Size 18,118,432 bytes (~17.3 MB).
  SHA-256 `ba9747f66e112dd3d3fab6ed424b09c2b358d7ff5efaaa8f09ecbef758e124f2`.
- `Founder-Stack-Setup-0.9.0.exe` — IDE + Founder Node bundle.
  Produced at `C:\Users\user\founder-ide-build\dist\Founder-Stack-Setup-0.9.0.exe`.
  Size 105,835,389 bytes (~100.9 MB).
  SHA-256 `97a01b76a246c8b02b90a9401a0427226ec5f9a667e660e56095ecda9995efa8`.
  PE32+ verified, `ProductName=Founder Stack`, `ProductVersion=0.9.0`,
  `CompanyName=Doxxed Crypto`, `FileDescription=Founder Stack Setup`.

### Known limitations (0.9.0)

- **Unsigned.** Neither the IDE installer nor the bundle is code-signed.
  Windows SmartScreen will warn on first run. Signing is a separate external
  blocker (cert acquisition + signtool wiring) tracked outside this release.
- **Private-mode binaries not bundled.** The bundle's `private_core` component
  (Forgejo + cloudflared) has no real binaries staged for 0.9.0; the Inno
  Setup `Check:` functions (`ForgejoBinAvailable` / `CloudflaredBinAvailable`)
  correctly return False on the target machine, so the component is silently
  skipped. PUBLIC and HYBRID-without-private-tooling installs are unaffected.
- **`founder-stack.iss` source has a latent Pascal bug** in `NextButtonClick`
  (`WizardSetupType(False) := 'full'` — `WizardSetupType` is read-only).
  The bundle for 0.9.0 was produced with a patched copy of the iss in the
  build workspace (`founder-stack-patched.iss`) that uses
  `WizardForm.TypesCombo.ItemIndex` + `OnChange` instead. The committed
  source `packages/founder-ide/installer/founder-stack.iss` is unmodified —
  parent should apply the same one-block fix before publishing.
- **Founder Node is still 0.8.0** inside the bundle (apps/founder-node/release
  was not rebuilt for 0.9.0; that work is tracked separately).

### Build commands

```powershell
# Prep + compile (60-90 min clean):
bash packages/founder-ide/build/build-founder-ide-void.sh

# Compose the Founder Stack bundle:
$ISCC = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
& $ISCC /DFOUNDER_STACK_VERSION="0.9.0" `
  /DFOUNDER_IDE_SETUP="<path-to-Founder-IDE-Setup-x64.exe>" `
  /DFOUNDER_NODE_SETUP="apps\founder-node\release\Founder-Node-0.8.0-win-x64.exe" `
  "packages\founder-ide\installer\founder-stack.iss"
```

### Side-by-side install

Fresh GUIDs (`win32AppId` `{39F6C958-...}`, `win32x64UserAppId`
`{436F5001-...}`) mean Founder IDE 0.9.0 installs alongside VS Code, VSCodium,
Void, and the old VSCodium-based Founder IDE 0.8.0 without conflict.

## 0.8.0 (VSCodium fork) — LEGACY / ARCHIVED

> **Status: LEGACY.** Superseded by the Void-based 0.9.0 build above. Do not
> present 0.8.0 as the current Founder IDE in the UI or docs.

Previous release — VSCodium-based. The full `Founder-Stack-Setup-0.8.0.exe`
could not be produced (the VSCodium toolchain was incomplete on the build
machine — missing `jq`, `7-Zip`, `Rustup`, `Inno Setup`; Python 3.14 instead
of the required 3.11). Only an interim "Lite" zip (Founder Node + extension
`.vsix` + bootstrap script) was ever shipped.

The 0.8.0 deliverables and VSCodium-specific build scripts have been
**archived under `packages/founder-ide/legacy/`** (not deleted). See
`legacy/README.md` for the archive manifest and the rollback procedure.

The Vault fork (0.9.0) inherits a complete AI-native UX instead of building
one on top of VSCodium, so the VSCodium toolchain is no longer required.