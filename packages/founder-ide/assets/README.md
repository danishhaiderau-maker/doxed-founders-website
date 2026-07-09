# Founder IDE assets

Drop the binary asset files here before running a build. They are **not**
committed (large binaries) — see `.gitignore` notes below.

## Required files

| File | Used by | Notes |
|---|---|---|
| `icon.ico` | `build/win32/code.iss` (Inno Setup), `resources/app/resources/win32/code.ico` | Multi-resolution Windows .ico (256x256, 128, 64, 48, 32, 16). This is the app + installer icon. |
| `icon.png` | Linux builds (optional on Windows-only release) | 512x512 PNG. |
| `splash.png` | Loading window background (optional) | VS Code has no classic splash; this themes the loading window via a CSS patch layered on VSCodium's. |
| `founder-ide-extension.vsix` | `prepare-founder-ide.sh` → `product.json` `builtInExtensions` | Produced by `npm run package` in `packages/founder-ide-extension/`. Copy it here (or next to the build root) before running the build. |

## Where they end up in the built app

- App icon: `resources/app/resources/win32/code.ico` (replaced by
  `prepare-founder-ide.sh`).
- Installer icon: `code.iss` `SetupIconFile` (set by the sed patch).
- Built-in extension `.vsix`: extracted into
  `extensions/founder-ide-extension/` inside the built app by VS Code's
  `builtInExtensions` machinery.

## Generating the icons

Source artwork should live in the design repo (not here). Export to `.ico`
with multiple sizes (e.g. via `convert` / `png2ico` / Inkscape). The .ico
**must** include a 256x256 frame for Windows 10/11 taskbar + Start Menu
rendering at high DPI.

## .gitignore

These asset binaries are intentionally not committed. Add to the repo root
`.gitignore` if not already:

```
packages/founder-ide/assets/*.ico
packages/founder-ide/assets/*.png
packages/founder-ide/assets/*.vsix
!packages/founder-ide/assets/README.md
!packages/founder-ide/assets/product.json.template
```
