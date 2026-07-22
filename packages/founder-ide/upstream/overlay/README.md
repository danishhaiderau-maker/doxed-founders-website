# Founder IDE overlay

This directory holds the **Founder IDE customizations** that get layered on top
of upstream Void (`voideditor/void`) before the VS Code distribution is built.

CI flow (`.github/workflows/build-founder-ide.yml`):

1. Clone `voideditor/void` at the pinned commit from `MANIFEST.json`.
2. Enter the `vscode/` subdirectory.
3. Run `scripts/apply-founder-customizations.ps1` from the **monorepo root**.
   This copies every file listed in `MANIFEST.json` over the freshly cloned
   checkout (`mode: replace` overwrites; `mode: add` creates a new file).
4. `npm ci`, `gulp vscode-win32-x64`, `gulp vscode-win32-x64-user-setup`.
5. Build the outer Founder Stack installer with Inno Setup.

## Why an overlay (and not a git patch)

A unified `*.patch` was rejected because:

- The Void checkout on Windows flips LF→CRLF on checkout (we saw the warning
  during `git diff`), which makes line-anchored patches fragile.
- One of the customizations is a brand-new file (`sendFounderOs.ts`), which
  would need to be shipped alongside the patch anyway.
- The other three files (`product.json`, `build/win32/code.iss`,
  `sendLLMMessage.ts`) are small and easy to diff/review by hand when stored
  verbatim. This is also how Void itself ships overrides via `void-builder`.

## Updating the overlay

After editing one of these files in your local Void checkout at
`C:\Users\user\founder-ide-build\void-builder\vscode\`, copy it here:

```powershell
$vscode = "C:\Users\user\founder-ide-build\void-builder\vscode"
$overlay = "packages\founder-ide\upstream\overlay"
Copy-Item "$vscode\product.json" "$overlay\product.json" -Force
# ...etc
```

If you add a new customized file, register it in `MANIFEST.json`.

## Bumping the upstream pin

Edit `MANIFEST.json`:

- `upstream.commit` — the `voideditor/void` SHA to pin
- `upstream.commit_subject` — the commit's subject line (for human readers)
- `toolchain_pins.node` / `python` — the env pins; also update the workflow

Then re-trigger `build-founder-ide.yml` manually.

## What lives where

| Layer | Path | Built by |
|---|---|---|
| Monorepo (this repo) | `packages/founder-ide/installer/founder-stack.iss` | Inno Setup (outer bundle) |
| Monorepo (this repo) | `packages/founder-ide/upstream/overlay/` | copied onto upstream Void at build time |
| External (cloned by CI) | `vscode/` from `voideditor/void` | `gulp vscode-win32-x64` |
