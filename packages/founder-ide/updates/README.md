# Founder Stack update manifest + rollback

This directory defines the versioned update manifest consumed by the Founder
Node update checker (`apps/founder-node/src/update-manager.ts`) and, for the
bundled Founder Stack, by the installers under `packages/founder-ide/installer/`.

## Files

- `founder-stack-updates.json` — the manifest. Declares the `latestVersion`,
  a per-release `download.url` + `sha256` + `size`, a `minimumVersion` gate,
  and a `rollback` strategy per release.
- `founder-stack-updates.schema.json` — JSON Schema for the manifest.
- `validate-update-manifest.mjs` — validator + invariant checks (run by CI and
  by the smoke test).

## How updates work today

### Founder Node (single app)

`update-manager.ts` polls `{apiBaseUrl}/api/founder-node/latest-release`, falls
back to the GitHub releases API, finds the newest `founder-node-v*` tag, and
compares versions with `semverGt`. If newer, it downloads the NSIS `.exe` and
spawns it detached with `/S`, then quits. The NSIS config
(`apps/founder-node/package.json` → `build.nsis`) has
`deleteAppDataOnUninstall: false`, so the `~/FounderVault` vault survives every
upgrade and every uninstall.

### Founder Stack bundle (IDE + Node)

Composed by `installer/founder-stack.iss` (Inno Setup). The bundle runs the two
sub-installers with their own silent flags. Founder IDE carries its own Inno
Setup `AppId` (`{436F5001-...}` for the user installer), so installing 0.9.0
over 0.8.0 replaces the IDE in place at
`%LOCALAPPDATA%\Programs\Founder IDE`.

## Rollback (the part the audit asked about)

There is intentionally **no automatic rollback daemon**. Automatic rollback
daemons add failure modes (a half-installed rollback that bricks the install)
without solving the real risk, which is "the new build won't launch." The
rollback story is deliberately simple and robust:

1. **The vault is sacred.** Neither installer touches `~/FounderVault`. A bad
   update cannot lose pairing, node ID, or sync state.
2. **Downgrade = reinstall.** To roll back, re-run the previous
   `Founder-Stack-Setup-<v>.exe`. NSIS supports downgrade-in-place (`/S`), and
   the Founder IDE Inno installer's `AppId` is stable across versions, so the
   prior build replaces the new one cleanly.
3. **Minimum-version gate.** The manifest's `minimumVersion` field is the hard
   floor below which an in-place upgrade is not advertised. Clients below it are
   told to reinstall cleanly rather than attempt an unsupported jump.
4. **Status field.** A release with `status: "yanked"` is excluded from update
   checks even if its version is higher, so a bad release can be pulled without
   deleting the GitHub release (preserving audit history).

## Before you publish a release

1. Build the installer.
2. Compute the SHA-256 and fill it into `founder-stack-updates.json`
   (replace the `PLACEHOLDER_FILL_...` value).
3. Run `node packages/founder-ide/updates/validate-update-manifest.mjs` — it
   will fail the publish if any `sha256` is still a placeholder or if the
   manifest fails its schema.
4. Upload the `.exe` to the GitHub release tagged `founder-stack-v<version>`.
5. Flip `status` from `pending-build` to `current` and set `releasedAt`.

## Update-detection + rollback test

`validate-update-manifest.mjs` (below) asserts the invariants that make the
update + rollback path safe. Run it as part of the smoke test:

```powershell
node packages\founder-ide\updates\validate-update-manifest.mjs
```
