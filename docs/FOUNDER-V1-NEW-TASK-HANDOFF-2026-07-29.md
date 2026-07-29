# Founder OS V1 New Task Handoff

Date: 2026-07-29

Use this document when continuing Founder OS V1 in a fresh Codex task. Do not
reconstruct scope from chat history and do not create a new Stage 12.

## North Star

Complete Founder OS V1 stages 0 through 11 in the canonical
`doxedcryptofounder` repository: one lightweight Founder-first application,
embedded Founder Node, efficient managed DeepSeek plus unlimited Personal
AI/Ollama, persistent project context, bounded coordinated agents, truthful
receipts, remote control, Founder website alignment, and evidence-gated release.

Optimize for preserving the working system:

1. Reuse existing capability.
2. Extend it with the smallest safe change.
3. Replace or delete only with evidence and explicit approval.
4. Keep one editing owner; Second Brain is advisory.
5. Never claim completion without observable evidence.

## Canonical workspace

- Worktree:
  `C:\Users\user\Desktop\Final Bots\worktrees\founder-v1-clean-integration`
- Branch: `release/founder-v1-clean-integration`
- Tested product source commit: `ea45964d`
- Production base: `c7f655da0709407b6f378a5c00617d3c170adb6a`
- Relationship at the tested product commit: 88 ahead, 0 behind
- Changed paths in the Founder delta: 354
- Protected trading/analyzer/exchange/gate paths in the Founder delta: 0
- Backup ref: `backup/founder-v1-pre-rebase-20260729`

Do not switch to the old `doxedcryptofounder` checkout or `Final Bots` as the
product source. Do not merge or rebase this branch wholesale onto a moving
production branch. Build a clean production-descended integration candidate
and carry only the reviewed Founder delta when production clearance exists.

## Release candidate

- Installer:
  `C:\Users\user\Desktop\Final Bots\worktrees\founder-v1-clean-integration\packages\founder-ide\installer\dist\Founder-IDE-Setup-0.9.4-internal-qa.exe`
- Size: 323,910,432 bytes
- SHA-256:
  `6CCAFEED6211544E43DFABE95CA18901EEA18F7DB72AE94153AB0FD8678AE48F`
- Installed release: 0.9.4
- Installer-embedded Founder extension: 0.5.2
- Locally installed extension update under QA: 0.5.3
- Channel: unsigned internal QA, not production

The installed application proves Founder navigation, Projects/Chats, coherent
settings, the 200K weekly Free allowance, Personal AI/BYOK surfaces, image
preview/remove, managed Fast chat through DeepSeek V4 Flash, Remote and
Connections surfaces, and a real terminal command.

The extension-only checkpoint at `ea45964d` replaces the abstract cube and raw
path empty-workspace screen with a lightweight Founder Home. Its center keeps
the pursuing Goal, agent/conflict state, New chat, Project, Preview, Changes,
Deploy, and Remote visible. The one-click Developer mode exposes the full
legacy IDE escape hatch and switching back restores Founder mode. This was
implemented without a workbench compile or installer rebuild.

- VSIX:
  `C:\Users\user\Desktop\Final Bots\artifacts\founder-ide-extension-0.5.3-final.vsix`
- VSIX size: 2,457,488 bytes
- VSIX SHA-256:
  `3CC3AF49830F02CD8B9EAAC25BE0573CDD160D0E2DE1A73B66764766A3821D2C`
- Visual evidence:
  `C:\Users\user\Desktop\Final Bots\artifacts\founder-home-0.5.3-founder-mode.png`
- Real Electron checks: Founder Home default PASS; high-DPI 2x2 actions PASS;
  Developer mode toggle PASS; return to Founder mode PASS; visible error
  notifications 0.

Stage 8 native Dragon acceptance is also complete. The installed companion is
a transparent always-on-top entity outside Founder IDE, with consistent
nest/flight/fire/attention states. Native drag moved it from `(992, 390)` to
`(920, 358)` and an Electron restart restored exactly `(920, 358)`. Windows
hit-testing proved transparent-area click-through and visible-body
interaction. The state sweep produced zero console or page errors.

- Dragon verifier commit: `e049dc34`
- Dragon evidence:
  `docs/FOUNDER-DRAGON-NATIVE-QA-EVIDENCE-2026-07-29.md`
- Live desktop capture:
  `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-desktop-live-20260729.png`
- Native state and drag proof:
  `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-native-proof-20260729\`
- Restart-persistence proof:
  `C:\Users\user\Desktop\Final Bots\artifacts\founder-dragon-restart-proof-20260729\`

Primary evidence:

- `docs/FOUNDER-V1-CLEAN-INTEGRATION-EVIDENCE-2026-07-28.md`
- `docs/FOUNDER-DRAGON-NATIVE-QA-EVIDENCE-2026-07-29.md`
- `docs/FOUNDER-SCREENSHOT-ATTACHMENT-QA-EVIDENCE-2026-07-29.md`
- `docs/FOUNDER-V1-COMPLETE-EXECUTION-PLAN.md`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-navigation-qa-20260729-final\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-settings-qa-20260729-final2\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-attachment-qa-20260729-complete\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-chat-qa-20260729-fast\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-terminal-qa-20260729-final\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-visual-qa-20260729-packaged-052\`
- `C:\Users\user\Desktop\Final Bots\artifacts\installed-navigation-qa-20260729-packaged-052\`

## Verified automated baseline

- API: 309/309
- Founder extension: 214/214 plus TypeScript
- Founder Node: 62/62 plus build
- Shared utilities: 99/99
- Web: production build, 58 pages
- Overlay/installer and focused Founder contracts: passed
- Protected-path audit: zero trading/analyzer/exchange/gate paths

Latest narrow screenshot QA:

- Installed browse, paste, and drag/drop preview/removal: PASS
- Shared Ask/Plan/Build/Debug/Team screenshot composer: PASS
- Managed visual API/controller: 7/7
- Overlay: 22/22
- Console errors/page exceptions: 0/0
- Live circle/arrow OCR: still requires a permitted vision route; do not
  report deterministic provider-contract tests as live semantic vision.

Run focused checks after a narrow change. Do not rebuild an installer for
ordinary UI or extension edits:

- Extension text, navigation, composer, settings: fast extension lane
- Founder overlay changes: warm workbench lane
- Installer: release checkpoint only
- Full cold compile: Electron/native/core changes only

## Immediate launch blockers, in order

1. **Create a clean production integration candidate.**
   Rebase from the latest approved production master only after coordination.
   Prove the required trading-safety ancestor remains present and the final diff
   has zero protected paths.
2. **Deploy the reviewed API contracts through the normal guarded process.**
   Production currently returns DeepSeek V4 Pro for a fresh routine
   `founder-os-auto` request, while source commit `4108904d` correctly forces
   Auto to Flash. Production also lacks the matching Agent tool/history contract
   needed by installed Plan/Build/Debug/Team flows.
3. **Repeat installed managed-AI QA after deployment.**
   Prove Auto, Fast, Reasoning, Code, Ask, Plan, Build, Debug, Team, streaming
   content, terminal completion, cancellation, and quota errors. Require the
   exact route receipt for the new response, not an old line from chat history.
4. **Complete real Personal AI and local paths.**
   Prove one configured BYOK completion. Prove Ollama only if Ollama and a model
   are actually installed; the current machine has no service on port 11434.
5. **Complete human-interaction gates.**
   A person must speak the expected phrase for microphone transcription. Do not
   manufacture speech proof while the founder is away.
6. **Complete remote E2E.**
   Pair or inspect a real installed device through the signed-in production
   website, exercise an authenticated proposed action, verify approval and
   audit receipt, then revoke/reconnect without exposing a reusable secret.
7. **Finish website truth alignment.**
   Audit Founder account/admin routes against shipped capacity, remove stale
   claims, align plans/usage/providers/devices, and visually verify responsive
   states.
8. **Close release gates.**
   Trusted Windows signing, clean-VM install/uninstall/update/rollback, and soak
   evidence remain external/manual gates. Keep the build labelled internal beta
   until they pass.

## Known results that must not be misreported

- Fresh installed `founder-os-fast`: PASS,
  `fast / deepseek-v4-flash`, provider 798 ms, total 3,616 ms.
- Fresh installed `founder-os-auto`: response PASS but policy FAIL,
  `reasoning / deepseek-v4-pro`; source fix exists but is not deployed.
- Installed Plan selection: visible, but its managed request fails against the
  current deployed API. Do not mark all five modes complete yet.
- Installed terminal: PASS with a real marker file.
- Installed microphone button and managed transport contracts: present.
  Live spoken transcription: not yet human-proven.
- Installed attachment picker/preview/remove: PASS.
  Annotation-aware vision response: still needs an enabled permitted vision
  route plus a circled/arrowed screenshot.

## Boundaries

- Do not touch trading, analyzer, Bitfinex, relay-arm, exchange, or production
  gate files from this Founder task.
- Do not re-arm trading or use unpublished operator workflows.
- Notify the trading owner immediately before a master push or production
  deployment and wait for the exact boundary clearance.
- Never expose provider keys, device secrets, bearer tokens, or image/audio
  data in logs or evidence.
- Do not delete legacy worktrees, Codex history, research archives, user data,
  caches, or installed versions until the owning task classifies exact paths
  and the current release is safely preserved.
- Keep the personal HR line from the June pasted discussion out of product
  documentation; it is unrelated to Founder OS.

## When to start a fresh task

A fresh task is recommended now because the implementation state is preserved
in source, commits, tests, artifacts, and these two canonical documents. The
new task should begin by reading this handoff and the evidence document, then
verify the branch and current production boundary. It should not reread or
summarize the old conversation before acting.
