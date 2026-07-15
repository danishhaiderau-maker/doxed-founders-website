# Founder IDE — releases

## 0.9.0 (Void fork) — Phase 5 — BUILT

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