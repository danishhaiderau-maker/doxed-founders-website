# Founder IDE — Void fork build orchestration (Phase 5)

This directory contains the **branding patches, AI rewire, build scripts, and
bundled installer** that turn a [Void](https://github.com/voideditor/void)
checkout into **Founder IDE**. The Founder Node runtime is embedded inside the
IDE as its hidden website and local-infrastructure relay, so users install and
manage one application.

> **Foundation:** Void (a VS Code fork with full AI UX) — replaces the
> VSCodium-based build from 0.8.0.
> **Design source:** [`docs/FOUNDER-IDE-VOID-FORK-PLAN.md`](../../docs/FOUNDER-IDE-VOID-FORK-PLAN.md)

## Why Void instead of VSCodium

Void already ships the AI-native UX we'd otherwise build from scratch:
inline edit (Ctrl+K), autocomplete (FIM), diff review, and a chat sidebar.
Forking Void changes the project from "build the AI UX on top of a rebranded
editor" to **"rewire an existing AI UX to talk to our Gateway"** — smaller,
faster, better outcome.

## Layout

```
packages/founder-ide/
├── README.md                              # this file
├── RELEASES.md                            # release process + 0.9.0 notes
├── build/
│   ├── build-founder-ide-void.sh          # main build — wraps void-builder
│   ├── prepare-founder-ide-void.sh        # branding: Void -> Founder IDE
│   ├── rewire-llm-to-gateway.sh           # THE REWIRE — AI -> our Gateway
│   ├── sendFounderOs.ts                   # Gateway client (ports gateway-client.ts)
│   ├── brand-product.json.patch           # product.json branding patch
│   └── strip-spectre-native-modules.sh    # node-gyp Spectre mitigation fix
├── updates/                               # update manifest + rollback spec
│   ├── founder-stack-updates.json
│   ├── founder-stack-updates.schema.json
│   └── validate-update-manifest.mjs
├── scripts/
│   ├── embed-founder-relay.ps1            # embeds Node runtime inside the IDE
│   └── smoke-pairing-and-gateway.mjs      # pairing + SSE smoke test
├── assets/
│   └── product.json.template              # our GUIDs / name / data dir
├── installer/
│   ├── founder-stack.iss                  # mode-aware one-app bootstrapper
│   └── build-stack-installer.ps1          # orchestrator
├── legacy/                                # ARCHIVED 0.8.0 VSCodium material
│   ├── dist/Founder-Stack-Lite-0.8.0.zip
│   ├── staging/                           # 0.8.0 release notes + lite installer
│   └── build-vscodium/                    # legacy VSCodium build scripts
└── config/
    └── build-env.sh                       # branding env vars
```

## The rewire (the important part)

Void's AI all flows through one chokepoint:
`src/vs/workbench/contrib/void/electron-main/llmMessage/sendLLMMessage.ts`.
`rewire-llm-to-gateway.sh` patches that file to call our Gateway
(`sendFounderOs.ts`) before the per-provider dispatch, and map each feature to
a Gateway model alias:

| Void feature | Gateway alias |
|--|--|
| Autocomplete (FIM) | `founder-os-fast` |
| Edit / Ctrl+K | `founder-os-code` |
| Chat (normal) | `founder-os-auto` |
| Chat (agent) | `founder-os-reasoning` |

When Founder IDE is connected (`~/FounderVault/node-config.json`), every AI
request flows through the Gateway. When unpaired, the editor stays usable and
offers the Founder ID connection flow.

## How a build works

1. `void-builder` clones `voideditor/void` into `vscode/` and applies Void's
   patches (product.json -> "Void", code.iss sed, etc.).
2. `prepare-founder-ide-void.sh` layers "Founder IDE" on top — fresh GUIDs,
   Open VSX, our URLs, our icon.
3. `rewire-llm-to-gateway.sh` drops in `sendFounderOs.ts` and patches
   `sendLLMMessage.ts`.
4. The Founder relay is packaged unpacked and copied to
   `resources/founder-relay/` in the IDE payload.
5. `gulp vscode-win32-x64-user-setup` creates the one Founder IDE installer.
6. `installer/founder-stack.iss` optionally adds Private/Hybrid tooling without
   registering a second installed product.

## Prerequisites (Windows)

Pinned by `packages/founder-ide/upstream/overlay/MANIFEST.json` and enforced by
`.github/workflows/build-founder-ide.yml` on a `windows-2022` runner:

- **Node 20.18.2** (NOT 24 — Electron 34.3.2 ABI mismatch breaks native modules)
- **Python 3.12** (NOT 3.13+ — node-gyp has no wheel for 3.13/3.14)
- **Inno Setup 6** (preinstalled on `windows-2022` runners at
  `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`)
- Git, jq, 7-Zip (only for the legacy VSCodium build path — see `legacy/`)

~140 GB free disk. Clean CI build: 70–110 min (Electron download dominates).
Warm-cache CI build: 30–45 min.

For the Azure Trusted Signing step (release builds only), the 6 `AZURE_*`
repo secrets documented in `docs/CODE-SIGNING-GUIDE.md` §0 must be set.
Fork PRs without the secrets produce an unsigned exe (SmartScreen warns) —
the `signtool verify` step is advisory-only on dev builds.

## Side-by-side install

Fresh GUIDs (`win32AppId` `{39F6C958-6DA1-4BAE-82D5-F0952FE270BD}`,
`win32x64UserAppId` `{436F5001-DD46-4F41-8D93-89EF9716CC07}`) — Founder IDE
installs alongside VS Code, VSCodium, Void, and the old Founder IDE 0.8.0.

## License

Apache 2.0 (Void upstream) + MIT (Founder OS additions). Open VSX marketplace.
