# Founder IDE — VSCodium downstream build orchestration

This directory does **not** contain a clone of VSCodium (that would be ~50 GB).
It contains the **branding patches, build scripts, and bundled installer** that
turn a VSCodium checkout into "Founder IDE" and compose it with Founder Node
into a single "Founder Stack" Windows installer.

> Design source: [`docs/FOUNDER-IDE-FORK-PLAN.md`](../../docs/FOUNDER-IDE-FORK-PLAN.md)
> — read §1 (VSCodium fork process), §6 (build + installer + bundling), §7
> (strip/keep/add) before changing anything here.

## Layout

```
packages/founder-ide/
├── README.md                      # this file
├── RELEASES.md                    # release process + download URLs
├── build/
│   ├── build-founder-ide.sh       # main build (Git Bash) — wraps VSCodium's dev/build.sh
│   ├── build-founder-ide.ps1      # PowerShell wrapper
│   ├── prepare-founder-ide.sh     # branding patches (jq + sed) run after prepare_vscode.sh
│   └── brand-product.json.patch   # jq filter for product.json branding
├── assets/
│   ├── README.md                  # where to place icons, splash, etc.
│   └── product.json.template      # our product.json overrides
├── installer/
│   ├── founder-stack.iss          # Inno Setup script for the BUNDLED installer
│   └── build-stack-installer.ps1  # orchestrates: ext .vsix → Founder IDE → Founder Node → bundle
└── config/
    └── build-env.sh               # branding env vars (APP_NAME, BINARY_NAME, ORG_NAME, ...)
```

## How a build works (high level)

1. You start with a checkout of our VSCodium downstream
   (`github.com/doxxedcrypto/founder-ide`) — a clone of VSCodium's build-script
   repo, on a pinned tag, with this directory's patches layered on top.
2. `config/build-env.sh` exports the Founder IDE branding env vars that
   VSCodium's `dev/build.sh` reads (`APP_NAME`, `BINARY_NAME`, `ORG_NAME`,
   `ASSETS_REPOSITORY`, `VSCODE_QUALITY`).
3. `build/prepare-founder-ide.sh` runs **after** VSCodium's `prepare_vscode.sh`
   and rewrites `product.json` (via `jq` using
   `build/brand-product.json.patch`) and `build/win32/code.iss` (via `sed`) to
   our branding, fresh GUIDs, Open VSX gallery, and built-in Founder OS chat
   extension.
4. `build/build-founder-ide.sh` invokes VSCodium's `dev/build.sh` (Git Bash).
   Output lands in `VSCode/.../win32-x64/`:
   - `Founder-IDE-Setup-x64.exe` (NSIS user installer)
   - `Founder-IDE-x64-portable.zip` (portable)
5. `installer/build-stack-installer.ps1` orchestrates the full Founder Stack:
   builds the chat extension `.vsix`, builds Founder IDE, builds Founder Node,
   then composes both via `installer/founder-stack.iss` (Inno Setup / `iscc`)
   into `Founder-Stack-Setup-<v>.exe`.

## Prerequisites (Windows, one-time)

```powershell
winget install --id Git.Git -e            # Git Bash + sed/grep/find
winget install --id Python.Python.3.11 -e
winget install --id jqlang.jq -e
winget install --id 7zip.7zip -e
winget install --id JRSoftware.InnoSetup -e
# Node: match VSCodium's .nvmrc via nvm-windows
# Rustup (some native modules): https://rustup.rs
```

Disk: ~50 GB free. Clean build time: ~45–90 min. See §6.4 of the design report.

## What is NOT done here

- We do **not** clone VSCodium in this repo. That's a build-time step on a
  machine with the disk + toolchain. See `RELEASES.md` for the manual steps.
- We do **not** commit the VSCodium source. The patches in this directory are
  additive and run against a pinned VSCodium tag at build time.

## Side-by-side with VS Code / VSCodium

The `win32AppId` and `win32x64UserAppId` GUIDs in
`assets/product.json.template` are **fresh** (generated 2026-07-10) and do not
collide with VSCodium or VS Code. Founder IDE installs alongside both.

## License

MIT. VSCodium is MIT; Microsoft's `vscode` source is MIT; Founder Node is the
user's. The bundled installer preserves VSCodium's `LICENSE.txt` in the install
dir. We default to **Open VSX** (not the Microsoft marketplace) to stay clear
of Microsoft's marketplace Terms of Use — see §3.3 / §7 of the design report.
