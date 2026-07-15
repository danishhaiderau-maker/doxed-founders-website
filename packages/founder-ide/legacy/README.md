# Legacy — VSCodium-based Founder IDE 0.8.0

**Status: LEGACY. Do not present as the current Founder IDE.**

This directory archives the **0.8.0** deliverables and build scripts, which were
based on a VSCodium fork. Founder IDE has since pivoted to a **Void fork** for
release 0.9.0 (see `../RELEASES.md` and `../build/build-founder-ide-void.sh`).

These files are kept for audit and rollback purposes. They are not part of the
active build.

## What's here

```
legacy/
├── dist/
│   └── Founder-Stack-Lite-0.8.0.zip          # 85.6 MB interim deliverable
├── staging/
│   ├── RELEASE-NOTES-0.8.0.md                # what 0.8.0 shipped + why "Lite"
│   ├── README-0.8.0-staging.md               # staging dir readme (0.8.0)
│   ├── install-founder-stack-lite.ps1        # bootstrap installer (0.8.0)
│   └── founder-ide-extension-0.1.0.vsix      # extension shipped with 0.8.0
└── build-vscodium/
    ├── build-founder-ide.sh                  # legacy VSCodium build (bash)
    ├── build-founder-ide.ps1                 # legacy VSCodium build (PS)
    └── prepare-founder-ide.sh                # legacy VSCodium branding
```

## Why it was archived

The 0.8.0 release could not produce a full `Founder-Stack-Setup-0.8.0.exe`
because the VSCodium toolchain was incomplete on the build machine (missing
`jq`, `7-Zip`, `Rustup`, `Inno Setup`; Python 3.14 instead of the required
3.11). Only a "Lite" zip (Founder Node + extension .vsix + bootstrap script)
was ever produced. See `staging/RELEASE-NOTES-0.8.0.md` for the full account.

The pivot to Void for 0.9.0 inherits a complete AI-native UX (inline edit,
autocomplete, chat sidebar) instead of building one on top of VSCodium, so the
VSCodium toolchain is no longer needed for the main release.

## Rollback to 0.8.0

If a future 0.9.0 build is bad and a founder needs the 0.8.0 path:

1. Unzip `dist/Founder-Stack-Lite-0.8.0.zip`.
2. Run `staging/install-founder-stack-lite.ps1` (or the two manual install
   commands in `staging/RELEASE-NOTES-0.8.0.md`).
3. The `~/FounderVault` vault is untouched by both 0.8.0 and 0.9.0 installers,
   so pairing survives the rollback.

This is also documented in `../updates/founder-stack-updates.json` under the
`0.8.0` release entry (`rollback.mechanism: "manual"`).
