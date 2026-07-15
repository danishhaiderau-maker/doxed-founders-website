# Founder Stack Lite 0.8.0 (interim)

## What this is

Interim Windows deliverable built on the user's Windows machine. The **full**
`Founder-Stack-Setup-0.8.0.exe` (which bundles a rebranded VSCodium fork
"Founder IDE" + Founder Node in one Inno Setup installer) could **not** be built
on this machine — the VSCodium toolchain is incomplete (missing `jq`, `7-Zip`,
`Rustup`, `Inno Setup`, and Python is 3.14 not the 3.11 VSCodium requires).

This release ships the two pieces that **do** build today:

1. **Founder Node 0.8.0** — NSIS Windows installer (`Founder-Node-0.8.0-win-x64.exe`)
2. **Founder OS Chat extension 0.1.0** — `.vsix` for VS Code / Cursor / VSCodium / Windsurf
3. **Founder Stack Lite zip** — both of the above + a PowerShell bootstrap
   installer (`install-founder-stack-lite.ps1`) that installs Founder Node and
   wires the `.vsix` into whatever editor it detects.

## Install (Founder Stack Lite)

Unzip `Founder-Stack-Lite-0.8.0.zip` and run:

```powershell
.\install-founder-stack-lite.ps1
```

Or install the pieces manually:

```powershell
.\Founder-Node-0.8.0-win-x64.exe
code --install-extension .\founder-ide-extension-0.1.0.vsix
```

## Toolchain check (this machine)

| Tool | Status |
|--|--|
| Disk (C:) | ~156 GB free |
| Git Bash | present (2.54.0) |
| Node | 24.16.0 |
| Python | 3.14.5 (VSCodium wants 3.11) |
| jq | **missing** |
| 7-Zip | **missing** |
| Rustup | **missing** |
| Inno Setup (iscc) | **missing** |
| gh | 2.92.0 |

## Build commands used

```powershell
# Extension .vsix
cd packages\founder-ide-extension
npm run compile
npm run package
# -> founder-ide-extension-0.1.0.vsix

# Founder Node .exe (version bumped 0.7.15 -> 0.8.0)
cd apps\founder-node
npm run build
npm run pack:win
# -> release\Founder-Node-0.8.0-win-x64.exe (81.7 MB)
```

## What's missing vs. the full Founder Stack

- **Founder IDE** (rebranded VSCodium fork) — not built. Needs the VSCodium
  checkout (~50 GB) + the missing tools above + 45-90 min clean build.
- **Single .exe bundle** (`Founder-Stack-Setup-0.8.0.exe`) — needs Inno Setup
  (`iscc`) to compose Founder IDE + Founder Node via
  `packages/founder-ide/installer/founder-stack.iss`.
- **Code signing** — installers are unsigned (sign.js skipped, no cert env
  vars). SmartScreen will warn on first run. Acceptable for beta; not for
  public release.

## Next step to get the full .exe

On a Windows machine with the full toolchain (winget install Git, Python 3.11,
jq, 7-Zip, Inno Setup, Rustup), follow `packages/founder-ide/RELEASES.md`:
clone the VSCodium downstream, layer `packages/founder-ide/` patches, run
`build-founder-ide.ps1`, then `build-stack-installer.ps1` to compose
`Founder-Stack-Setup-0.8.0.exe`.

## Commit

- `b86848d2` — chore(founder-node): bump version to 0.8.0 (pushed to master)
