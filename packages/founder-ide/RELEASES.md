# Founder IDE — releases

## 0.9.3 (Void fork) — ONE-APP TEST CANDIDATE

> **Status: locally verified, pending CI/signing/production deployment.**
> Founder IDE is now the only user-facing application. The existing Founder
> Node runtime is embedded under `resources/founder-relay` and runs hidden as
> the IDE's website and local-infrastructure connector.

### What changed

- **One installed app.** The Windows pipeline packages the unpacked relay into
  Founder IDE before creating the IDE installer. There is no second Node
  sub-installer, Apps entry, Start Menu item, tray, or updater.
- **Automatic background relay.** The IDE extension starts the embedded
  runtime before its chat UI initializes, strips the extension host's
  `ELECTRON_RUN_AS_NODE` flag, and records a secret-free startup trace.
- **Shared Founder ID connection.** X/device-code authorization writes the
  shared `FounderVault` identity used by chat, website sync, IPC, and the
  background relay.
- **Upgrade cleanup.** The bootstrapper uninstalls legacy standalone Founder
  Node builds, removes obsolete Founder Stack files/shortcuts/startup entries,
  and preserves `FounderVault` and IDE settings.
- **One update/uninstall owner.** Founder IDE owns relay updates and removes
  both the embedded payload and its login entry on uninstall.
- **Installer truthfulness retained.** Public/Hybrid/Private selection still
  fails closed when optional Private-mode binaries are not bundled.

### Verification

- Extension: 71 tests passing.
- Embedded relay: 33 tests passing.
- API/dispatch/auth: 169 tests passing.
- Authenticated live named-pipe read returned 25 entries from the open
  workspace.
- Local status endpoint and embedded IDE handshake verified.
- Inno Setup one-app bootstrapper syntax compile verified.

### Expected artifacts

- `Founder-IDE-Setup-0.9.3.exe`
- `Founder-IDE-Setup-0.9.3.exe.sha256`
- `founder-ide-0.9.3-sbom.spdx.json`

### External gates

- Azure Trusted Signing credentials/account validation.
- Green clean-runner Windows installer build and clean-VM install.
- Production API/Railway rollout for the X/device-code routes.
- Production website-to-laptop staging smoke and soak test.

---

## 0.9.2 (Void fork) — RC — PENDING CI GREEN + SIGNING

> **Status: Release Candidate.** Re-bundles Founder Node (which 0.9.1
> incorrectly removed), wires Azure Trusted Signing in CI, adds an SBOM and
> a sigstore build-provenance attestation, and makes the installer's
> deployment-mode wizard truthful about what's actually bundled. NOT YET
> SHIPPED — pending a green `build-founder-ide.yml` run that exercises the
> `code.iss` `#ifexist` fix (commit `020b67b5`) end-to-end plus a signed
> outer `.exe` once the Azure Trusted Signing account is provisioned.

### What changed

- **FIX: `code.iss` `#ifexist` guard (commit `020b67b5`).** The inner
  installer step (`gulp vscode-win32-x64-user-setup`) had been aborting with
  `No files found matching ...VSCode-win32-x64\tools\*` because the
  open-source gulp targets don't emit the `tools\` directory (Microsoft's
  remote-tunnel CLI is only produced by VS Code's official Azure pipeline).
  The overlay's `build/win32/code.iss` now wraps the
  `Source: "tools\*"` line in an ISPP `#ifexist "tools\*"` guard, mirroring
  the existing `#ifdef AppxPackageFullname` guard on the `appx\*` line.
  `scripts/apply-founder-customizations.ps1` asserts the guard is present as
  a regression check.
- **FOUNDER NODE RE-BUNDLED (commit `daecf5d5`).** 0.9.1 removed Founder
  Node from the bundle on the assumption that the IDE talks to the Gateway
  API directly. That assumption is wrong: the IDE Gateway client requires
  `~/FounderVault/node-config.json`, which only Founder Node creates on
  first pairing. Without Founder Node in the bundle, the IDE's AI features
  are dead-on-arrival for new users. Founder Node is now installed in every
  deployment mode (it is required, not optional). The outer `founder-stack.iss`
  stages `Founder-Node-win-x64.exe` as a sub-installer and invokes it
  silently after the IDE install.
- **TRUTHFUL DEPLOYMENT MODES (commit `daecf5d5`).** If the Forgejo +
  cloudflared binaries are not staged in the build, the Private option in
  the wizard is annotated `[UNAVAILABLE]` and a one-time info dialog
  explains why. `NextButtonClick` hard-refuses to proceed if the user
  somehow selects Private anyway. We never silently skip a component the
  user selected.
- **AZURE TRUSTED SIGNING IN CI (commit `a9b5bc34`).** Both the inner
  `FounderIDESetup.exe` (signed in place before iscc bundles it) and the
  outer `Founder-Stack-Setup-<v>.exe` are signed via
  `azure/artifact-signing-action@v2` on release builds. A `signtool verify`
  step hard-fails the build if the outer exe is unsigned on a release
  build. Requires 6 `AZURE_*` repo secrets — see
  `docs/CODE-SIGNING-GUIDE.md` §0. **External blocker:** Azure Trusted
  Signing account provisioning takes hours-days of Microsoft identity
  review.
- **SBOM + BUILD PROVENANCE (commit `a9b5bc34`).** An SPDX SBOM is
  generated by `anchore/sbom-action@v0` and uploaded as a release asset.
  A sigstore attestation is created by `actions/attest-build-provenance@v1`,
  verifiable downstream with
  `gh attestation verify <file> --repo <org>/<repo>`.

### Build artifacts (pending green CI)

- `Founder-Stack-Setup-0.9.2.exe` — signed outer bundle (IDE + Founder Node
  + optional Forgejo/cloudflared). SHA-256 and size will be filled in by
  the CI run.
- `Founder-Stack-Setup-0.9.2.exe.sha256` — sidecar hash.
- `founder-stack-0.9.2-sbom.spdx.json` — SPDX SBOM.
- sigstore attestation — verifiable via `gh attestation verify`.

### Known limitations (0.9.2)

- **Signing is external-gated.** Until the Azure Trusted Signing account
  completes Microsoft identity validation, the signing step is a no-op and
  the build produces an unsigned exe (the `signtool verify` step is
  advisory-only on dev builds). See `docs/CODE-SIGNING-GUIDE.md` §0.
- **Private-mode binaries still not bundled.** The `private_core` component
  (Forgejo + cloudflared) is gated by `#ifexist` + `Check:` functions and
  the wizard's Private option is annotated `[UNAVAILABLE]`. Users who want
  Private mode must add the binaries later via Founder IDE. This is a
  known gap, not a regression.

### Build commands

```powershell
# Trigger a CI run (artifact-only, no release):
gh workflow run build-founder-ide.yml `
  --ref codex/founder-ide-installer-ci `
  -f version=0.9.2 -f skip_release=true

# Or via the orchestrator locally (takes hours):
.\packages\founder-ide\installer\build-stack-installer.ps1 -Version 0.9.2
```

---

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
