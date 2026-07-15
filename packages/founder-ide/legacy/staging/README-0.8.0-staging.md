# Founder Stack Lite (interim)

This is the **interim** Founder Stack deliverable, built on a machine that does
**not** have the full VSCodium toolchain (jq, 7-Zip, Rustup, Inno Setup, Python
3.11) installed. It gives founders the two working pieces **today**:

1. **Founder Node 0.8.0** — `Founder-Node-0.8.0-win-x64.exe` (electron-builder
   NSIS installer). Local vault + metadata sync tray app.
2. **Founder OS Chat extension** — `founder-ide-extension-0.1.0.vsix`. Registers
   the Founder OS AI Gateway as a first-class chat model provider in VS Code,
   Cursor, VSCodium, and Windsurf.

The **full** `Founder-Stack-Setup-<v>.exe` (which also bundles a rebranded
VSCodium fork called "Founder IDE") is not in this package — that requires the
VSCodium build, which needs the missing toolchain above plus a ~50 GB VSCodium
checkout and 45–90 min clean build time. See
`packages/founder-ide/RELEASES.md` for the full process.

## Install

Double-click `install-founder-stack-lite.ps1` (right-click -> Run with
PowerShell), or in a PowerShell terminal:

```powershell
.\install-founder-stack-lite.ps1
```

This will:

1. Silently install Founder Node (per-user, no elevation) via the NSIS
   installer.
2. Detect any Cursor / VSCodium / VS Code install on your machine and install
   the `.vsix` into it with `--install-extension --force`.

Flags:

- `-SkipNodeInstall` — only install the extension.
- `-SkipExtensionInstall` — only install Founder Node.

## Manual install (if the script does not detect your editor)

```powershell
# Founder Node
.\Founder-Node-0.8.0-win-x64.exe

# Extension (pick your editor)
cursor --install-extension .\founder-ide-extension-0.1.0.vsix
codium --install-extension .\founder-ide-extension-0.1.0.vsix
code   --install-extension .\founder-ide-extension-0.1.0.vsix
```

## Next steps after install

1. Launch **Founder Node** (Start Menu) and complete pairing — this writes
   `~/FounderVault/node-config.json` with your `nodeId` / `nodeToken` /
   `apiBaseUrl`.
2. Open your editor. The **Founder OS** chat participant and language-model
   provider appear automatically (activation is `onStartupFinished`).
3. Start chatting. The extension streams tokens from your Founder OS AI
   Gateway through your node.

## What's missing vs. the full Founder Stack

| Piece | Lite (this) | Full |
|--|--|--|
| Founder Node | yes (0.8.0) | yes |
| Founder OS Chat ext | yes (0.1.0, installed into existing editor) | yes (built-in to Founder IDE) |
| Founder IDE (rebranded VSCodium) | **no** | yes |
| Single .exe bundle | **no** (two files + script) | yes (`Founder-Stack-Setup-<v>.exe`) |
| Open VSX default gallery | n/a | yes |
| Side-by-side with VS Code | n/a (uses your existing editor) | yes (fresh GUIDs) |

The gap is the VSCodium fork build. That needs a Windows machine with the
toolchain listed at the top of this file. Once built, the Inno Setup script at
`packages/founder-ide/installer/founder-stack.iss` composes both apps into one
`Founder-Stack-Setup-<v>.exe`.
