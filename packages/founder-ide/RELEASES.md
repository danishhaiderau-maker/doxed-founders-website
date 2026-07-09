# Founder Stack — releases & downloads

## Download URL pattern

Releases are published to GitHub Releases on the Founder OS website repo:

```
https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest
```

The bundled installer asset is named:

```
Founder-Stack-Setup-<version>.exe
```

e.g. `Founder-Stack-Setup-0.1.0.exe`. The same release also publishes the
standalone Founder IDE installer and portable zip for power users who already
have Founder Node:

```
Founder-IDE-Setup-x64-<version>.exe
Founder-IDE-x64-portable-<version>.zip
```

A public landing page (on `doxxedcrypto.digital`) will redirect to the latest
release asset. Until that landing page exists, share the GitHub Releases URL
above directly.

## Versioning

`MAJOR.MINOR.PATCH` (semver), tracked in `packages/founder-ide/VERSION.txt`
(to be created on first release). The Founder Stack version = the Founder IDE
version; the bundled Founder Node version is recorded in the release notes
(it ships whatever `apps/founder-node/package.json` `version` was at build
time).

## Release process (manual — needs the build machine)

> These steps require a Windows machine with ~50 GB free, Git Bash, Node
> (VSCodium's `.nvmrc` version), Python 3.11, jq, 7-Zip, Inno Setup (`iscc`),
> and Rustup. The full clean build is ~45–90 min. We do **not** run this in CI
> yet — see "Open CI work" below.

1. **Clone our VSCodium downstream** (created once, rebased per release):
   ```bash
   git clone --recurse-submodules https://github.com/doxxedcrypto/founder-ide
   cd founder-ide
   git checkout <pinned-vscodium-tag>
   ```
2. **Layer this directory's patches** into the clone (or keep them in-tree on
   the downstream branch):
   ```bash
   # from the founder-ide clone root:
   cp -R /path/to/monorepo/packages/founder-ide/{build,assets,config,installer} .
   ```
3. **Build the Founder OS chat extension `.vsix`** (from the monorepo):
   ```powershell
   cd packages\founder-ide-extension
   npm install
   npm run package    # → founder-ide-extension-<v>.vsix
   ```
   Copy the `.vsix` next to the build so `prepare-founder-ide.sh` can wire it
   into `product.json` `builtInExtensions`.
4. **Build Founder IDE**:
   ```powershell
   .\build\build-founder-ide.ps1
   ```
   → `VSCode\...\win32-x64\Founder-IDE-Setup-x64-<v>.exe` and the portable zip.
5. **Build Founder Node** (from the monorepo):
   ```powershell
   cd apps\founder-node
   npm install
   npm run pack:win    # → release\Founder-Node-<v>-win-x64.exe
   ```
6. **Compose the Founder Stack installer**:
   ```powershell
   .\installer\build-stack-installer.ps1
   ```
   → `dist\Founder-Stack-Setup-<v>.exe` (Inno Setup bundles both apps).
7. **Smoke test** on a clean Windows VM:
   - Install `Founder-Stack-Setup-<v>.exe`.
   - Both "Founder IDE" and "Founder Node" appear in Start Menu.
   - Founder Node pairs → open Founder IDE → Chat view streams from the
     Gateway with no manual config (built-in extension).
   - Founder IDE installs side-by-side with VS Code (no mutex/installer
     conflict — fresh GUIDs).
8. **Publish**:
   ```powershell
   gh release create v<version> `
     "dist\Founder-Stack-Setup-<version>.exe" `
     "VSCode\...\win32-x64\Founder-IDE-Setup-x64-<version>.exe" `
     "VSCode\...\win32-x64\Founder-IDE-x64-portable-<version>.zip" `
     --repo danishhaiderau-maker/doxed-founders-website `
     --title "Founder Stack <version>" `
     --notes-file packages\founder-ide\RELEASE-NOTES-<version>.md
   ```

## Open CI work

A GitHub Actions workflow (`windows-2022` runner) mirroring VSCodium's
`stable-windows.yml` is the next step so releases aren't tied to one
developer's machine. The workflow will run steps 3–6 above on a hosted runner
and upload the artifacts to a draft release. Not built in this session —
filed as a follow-up.

## Code signing

Reuse the Founder Node signing cert (`apps/founder-node/build/sign.js`) with a
distinct `appId` for Founder IDE and the Founder Stack bootstrapper. Unsigned
installers will trigger SmartScreen warnings on first run — acceptable for
internal beta, **not** for a public release. See §10 of the design report.
