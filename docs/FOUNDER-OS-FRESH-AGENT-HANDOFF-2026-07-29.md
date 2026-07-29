# Founder OS Fresh Agent Handoff

Date: 2026-07-29

This is the factual handoff for a new agent. It replaces chat reconstruction.
Read this document, inspect Git state, and continue from evidence. Do not assume
that a checked source contract means the full product is shipped.

## 1. Honest status

Founder OS V1 is **not complete**.

There is substantial implemented and tested work across the API, Founder Node,
Founder IDE extension, installer, website, memory, coordination, quota, and
remote-control contracts. An internal Windows installer and a newer extension
checkpoint exist. However, the user-visible product still has important launch
gaps:

- The full Apple-inspired Founder-first UI is incomplete.
- Some installed work modes and managed AI routes are not proven end to end.
- Production `Founder Auto` routing was observed using Pro instead of the
  intended Flash route; the source repair was not deployed from this task.
- The production Agent tool loop is not proven in the installed application.
- Human microphone transcription is not proven.
- Live annotation-aware image understanding is not proven.
- Live Ollama completion is not proven because no local Ollama model is present.
- Production website-to-installed-device remote control is not proven.
- GitHub, Vercel, Railway, Neon, email, and Telegram connector flows are not all
  proven end to end in the Founder experience.
- The refreshed website routes still need signed-in responsive visual QA.
- Windows trusted signing, clean-VM lifecycle, updater rollback, and soak remain
  external/manual release gates.
- A final installer containing every later extension and website/API change has
  not been produced.

The previous task became slow because it combined product strategy, IDE work,
website work, production API coordination, installer QA, Windows application
control, and repeated trading-release boundaries in one very large conversation.
The new agent should freeze V1 scope, separate source completion from launch
proof, and close the shortest user-visible critical path.

## 2. North Star

Deliver one lightweight Founder-first application:

- Founder IDE is the desktop application.
- Founder Node is embedded background infrastructure, not a second product.
- The default surface is simple: Goal, Chat, Project, Preview, Changes, Deploy,
  Remote, agent state, and decision requests.
- A one-click Developer mode preserves the full IDE escape hatch.
- Founder-managed AI uses DeepSeek Flash by default and Pro only for explicit
  Code/Reasoning or recorded escalation.
- Personal AI/BYOK profiles and local Ollama are available from the same model
  picker and stay outside managed quota.
- One editing owner works; other agents or models provide bounded advisory
  review.
- Every meaningful result is verified before it is shown as complete.
- New capabilities should be extension-first. Avoid workbench/core edits unless
  the extension, embedded Node, or website boundary cannot provide the feature.

Do not create a Stage 12. Finish or explicitly defer Stages 0 through 11.

## 3. Canonical source

- Repository worktree:
  `C:\Users\user\Desktop\Final Bots\worktrees\founder-v1-clean-integration`
- Branch: `release/founder-v1-clean-integration`
- Product HEAD before this handoff document: `8f0e0c9b`
- The handoff document is the newest commit; verify its exact SHA with
  `git log -1 --oneline` rather than copying a stale value from chat.
- Remote comparison at handoff: `0 behind, 101 ahead` of `origin/master`
- Approved production ancestor recorded in this branch:
  `c7f655da0709407b6f378a5c00617d3c170adb6a`
- Backup ref: `backup/founder-v1-pre-rebase-20260729`

Do not use these as the canonical product source:

- `C:\Users\user\Desktop\Final Bots`
- the old `doxedcryptofounder` checkout
- `Final Bots` as though it were a single repository
- an unrelated trading or analyzer worktree

Do not merge the 100-commit branch wholesale into a moving production master.
Create a clean branch from the current approved production base and carry only
the reviewed Founder delta after protected-path and ancestry checks.

## 4. Current uncommitted state

At handoff, the worktree is intentionally **not clean**. Review these files
before doing other work:

- `apps/web/src/components/founder-node-downloads.tsx`
  - Fixes a real hydration defect by making the initial operating-system state
    deterministic (`unknown`) and detecting the browser OS after hydration.
  - The defect was observed visually: `/downloads` duplicated major page
    sections and logged a React hydration mismatch.
- `apps/web/src/components/founder-node-downloads.spec.tsx`
  - New untracked server/browser initial-markup stability test.
  - Must be run and either committed with the fix or removed deliberately.
- `apps/web/scripts/founder-downloads-visual-qa.mjs`
  - New untracked desktop/mobile `/downloads` visual harness.
  - Checks required/forbidden product language, overflow, console errors, page
    errors, and failed responses.
- `apps/web/package.json`
  - Adds `qa:founder-downloads:visual` for the new visual harness.

Do not blindly commit these four paths.

Recommended resolution:

1. Keep and validate the hydration fix plus its test.
2. Run the component test, TypeScript, and the new `/downloads` visual harness.
3. Inspect its desktop/mobile screenshots rather than trusting predicates alone.
4. Commit the hydration repair and its QA harness together if all checks pass.

The attempted `dev:fast` and Turbopack-root edits are not present in the final
worktree. They failed validation and were not retained.

## 5. Latest visual finding

The local `/founder-os` workspace passed the existing deterministic harness at:

- desktop: 1440 x 900
- tablet: 1024 x 768
- mobile: 390 x 844

Evidence directory:

`C:\Users\user\AppData\Local\Temp\FounderIDE\visual-qa\founder-os-workspace`

The harness reported three screens and zero predicate failures. Visual inspection
showed a coherent responsive layout with Founder Free usage, project memory,
Personal AI, focused tools, and connected services.

The separate `/downloads` inspection found:

- React hydration mismatch.
- Major content duplication in the full-page screenshot.
- Server initial markup hid the OS-specific link while the browser initial
  markup rendered it.

The current uncommitted state fixes the deterministic initial render, but the
corrected screenshot has not yet been captured because the development compiler
became unusably slow.

## 6. Build and development performance problem

The development process is still too slow in this worktree.

Observed during the latest QA:

- Web no-output TypeScript previously passed, but took about 8.6 minutes.
- First `/founder-os` development compilation took about 140 seconds.
- A one-component hot edit caused the Webpack dev route to remain busy for more
  than five minutes.
- Turbopack crashed with:
  `Symlink apps/web/node_modules is invalid, it points out of the filesystem root`.

Actual junction:

`apps\web\node_modules` points to:

`C:\Users\user\Desktop\Final Bots\worktrees\founder-vision-api\apps\web\node_modules`

That target is outside the current worktree root. Setting Turbopack's root only
to the current repository cannot solve that layout. The next agent should choose
one safe approach:

1. Give the canonical worktree its own normal workspace dependency install.
2. Repoint the junction to dependencies inside the canonical worktree.
3. Set a proven Turbopack root to the common `worktrees` parent, then validate
   that this does not expose unrelated files or break package resolution.
4. Keep Webpack and remove the unproven fast script until dependency ownership
   is corrected.

Do not keep layering build flags over a broken junction. Fix dependency
ownership once. Do not rebuild an installer for ordinary UI edits.

Existing measured fast IDE lane:

- First Founder extension build: about 11.82 seconds.
- Cached Founder extension build: about 1.99 seconds.

Use the extension lane for chat, navigation, settings, composer, and Founder
Home work. Use the unpackaged application for visual QA. Rebuild the installer
only at a release checkpoint.

## 7. What was completed

### Canonical integration and evidence

- Production-descended Founder branch established and audited.
- Protected trading/analyzer/exchange/gate path delta recorded as zero at the
  reviewed checkpoints.
- Internal Windows installer preserved with hash and installed QA evidence.
- Numerous evidence documents added under `docs/`.

### Founder Home and navigation

- Lightweight Founder Home added without a workbench rebuild.
- Founder mode exposes Goal, agent/conflict state, New chat, Project, Preview,
  Changes, Deploy, and Remote.
- One-click Developer mode preserves the full IDE.
- Essential navigation was made visible and readable.

### Living Founder Dragon

- Transparent always-on-top companion outside the IDE.
- Nest, flight, fire, and attention states.
- Native dragging and restart-position persistence proven.
- Transparent-area click-through and visible-body interaction proven.
- State sweep reported no console/page errors.

### AI and composer

- Founder-managed aliases and DeepSeek V4 boundary exist in source.
- Named Personal AI/BYOK profiles with name, base URL, key, model, optional
  headers, test, edit, disable, delete, and active selection.
- Installed GLM-5.2 Personal AI completion proven with an outside-managed-quota
  receipt.
- Native picker round trip among Founder routes and Personal AI proven.
- Screenshot browse, paste, drag/drop, preview, and removal proven.
- Managed image-description API contract implemented and tested.
- Voice capture state, explicit cancel, Escape-to-cancel, track cleanup, and
  typed-text preservation proven with synthetic media.

### Founder Node and remote security

- Preferred device-code pairing is single-use, expiring, account/install bound,
  revocable, and routed to a paired Node/IDE session in source tests.
- Legacy pairing was hardened:
  - validates pairing code and node identity;
  - bounds device metadata;
  - validates optional 32-byte IPC secret;
  - refuses cross-founder node-ID takeover;
  - atomically claims unused, unexpired codes;
  - rejects replay/concurrent second claim.

### Memory, efficiency, plans, and coordination

- Persistent project/decision/task/goal/receipt primitives exist.
- Code-intelligence graph contracts, bounded retrieval, cache invalidation, and
  secret/ignored-file exclusions exist with tests.
- Managed usage reservation/reconciliation and weekly plan allowances exist.
- AgentBus v2 contracts, dependency fields, overlap/lease/fencing behavior,
  goal contracts, decision inbox, and housekeeping-goal contracts exist.
- These are meaningful foundations, but the new agent must distinguish API/unit
  proof from installed multi-chat product proof.

### Website truth pass

Commit `bf7c34d8` updated 27 web source files:

- `Founder Stack` -> `Founder IDE`
- `Founder Copilot` -> `Founder AI`
- AI settings -> `Personal AI`
- Founder Node described as embedded infrastructure
- legacy standalone Node downloads collapsed
- macOS/Linux Founder IDE packages no longer falsely presented as available
- Windows candidate labelled unsigned internal beta
- unsupported guaranteed-savings language removed

Web no-output TypeScript passed after fixing three stale imports. Focused Founder
shell and provider-label tests passed.

## 8. Important commits

Newest relevant commits:

- `8f0e0c9b` docs: website truth audit
- `bf7c34d8` fix: one-app website terminology
- `db74a4b6` docs: remote-control security evidence
- `5b760b69` fix: hardened legacy Founder Node pairing
- `71cfd947` docs: voice cancel evidence
- `af79fda5` feat: safe voice cancellation
- `391a7b33` test: Personal AI route switching
- `f78233fe` test: screenshot attachment paths
- `8ab595e5` docs: native Dragon acceptance
- `e049dc34` test: Dragon drag QA
- `ea45964d` feat: lightweight Founder Home
- `16036ec8` docs: packaged V1 QA evidence
- `55ac43f8` fix: essential Founder navigation

Review the full branch log before selecting commits for a clean production
candidate.

## 9. Verified baselines

Most recent recorded baselines:

- API: 311/311
- Founder extension: 214/214 plus TypeScript
- Founder Node: 62/62 plus no-output TypeScript
- Shared utilities: 99/99
- Focused remote API contracts: 55/55
- Web focused shell/provider tests: 9/9
- `/founder-os` visual harness: 3/3 responsive screens, zero predicate failures

Do not assume these counts remain current after new changes. Re-run the focused
test first, then the broad suite when the blast radius requires it.

## 10. Release artifacts

Internal installer:

`C:\Users\user\Desktop\Final Bots\worktrees\founder-v1-clean-integration\packages\founder-ide\installer\dist\Founder-IDE-Setup-0.9.4-internal-qa.exe`

- Size: 323,910,432 bytes
- SHA-256:
  `6CCAFEED6211544E43DFABE95CA18901EEA18F7DB72AE94153AB0FD8678AE48F`
- Channel: unsigned internal QA
- Installed application: 0.9.4
- Installer-embedded extension: 0.5.2

Newer extension checkpoint:

`C:\Users\user\Desktop\Final Bots\artifacts\founder-ide-extension-0.5.3-final.vsix`

- Size: 2,457,488 bytes
- SHA-256:
  `3CC3AF49830F02CD8B9EAAC25BE0573CDD160D0E2DE1A73B66764766A3821D2C`

The installer does not automatically contain every later branch change. Do not
present it as the final public Founder OS V1.

## 11. What remains by stage

### Stage 0

- Salvage unique evidence and archive only explicitly approved obsolete clean
  worktrees.
- Do not delete caches, archives, source, or Codex history without a bounded
  housekeeping review and user approval.

### Stage 1

- Trusted Windows signing.
- Clean Windows VM install/uninstall.
- Update and rollback proof.
- Required soak.

### Stages 2-4

- Source/contracts are largely complete.
- Revalidate them in the final clean integration candidate.
- Prove actual measured cost/cache behavior before making savings claims.

### Stage 5

- Human microphone transcription.
- All five work modes in the refreshed installed application.
- Live annotation-aware screenshot understanding.
- Deploy the focused Agent tool/history gateway contract through a safe
  production boundary.
- Complete one installed model -> approval -> local tool -> tool result -> final
  answer loop.
- Re-run authenticated Auto/Fast/Reasoning/Code/stream/cancel/quota tests.
- Prove truthful model receipts.
- Prove local Ollama only after deliberate installation of a service/model.

### Stage 6

- Contracts are implemented.
- Prove the multi-chat coordination experience in the final installed build,
  including enforced scope overlap rather than advisory-only fields.

### Stage 7

- Finish the Apple-inspired Founder/Developer experience.
- Apply consistent Overview / Work and Decisions / Connect and Data hierarchy.
- Add purposeful state transitions.
- Audit empty/loading/offline/unauthorized/quota/error states.
- Align Remote and the website shell.
- Remove obsolete chrome from Founder mode, not from Developer mode.

### Stage 8

- Native Dragon is accepted.
- Keep it optional, draggable, low-resource, and state-driven.
- Do not let animation block the core product.

### Stage 9

- Prove production website -> real installed device remote action.
- Prove approve/deny, receipt, revoke, reconnect, and session expiry.
- Prove consistent device status in website and IDE.
- Complete approved connector flows.

### Stage 10

- Finish signed-in Founder account/admin route inventory and responsive QA.
- Prove plans, quota, providers, devices, and connector health/revoke states.
- Do not let token launch, treasury, acquisition settlement, DEX, vesting, or
  wallet-rotation ideas block Founder IDE V1. Those require separate legal,
  security, economic, and smart-contract programs.
- Reclassify speculative business workflows as post-V1 unless the user
  explicitly identifies one as a V1 release blocker.

### Stage 11

- Make verify-then-show an enforced release gate.
- Complete loading/empty/error/offline/reduced-motion states.
- Capture deterministic screenshots after each final UI change.
- Build one final installer after source and installed QA freeze.
- Sign it, run clean-VM lifecycle, test update/rollback, and perform soak.
- Produce final hashes, screenshots, test logs, and a launch/blocker report.

## 12. First actions for the new agent

1. Read:
   - this document;
   - `docs/FOUNDER-V1-COMPLETE-EXECUTION-PLAN.md`;
   - `docs/FOUNDER-V1-NEW-TASK-HANDOFF-2026-07-29.md`;
   - `docs/FOUNDER-V1-CLEAN-INTEGRATION-EVIDENCE-2026-07-28.md`.
2. Verify the exact branch, HEAD, ahead/behind count, and uncommitted files.
3. Resolve the `/downloads` hydration fix and test as a small isolated commit.
4. Fix or remove the unproven Turbopack fast-lane experiment. Do not commit a
   script that still panics on the dependency junction.
5. Run corrected desktop/tablet/mobile visual QA for `/downloads`,
   `/founder-os`, `/settings/builder?tab=downloads`,
   `/settings/builder?tab=ai`, `/settings/ai-usage`, and `/phone`.
6. Freeze the user-visible V1 scope. Keep treasury/DEX/acquisition/token
   mechanics in a separate backlog.
7. Produce a short remaining-critical-path board with only:
   - production AI/tool contract;
   - installed five-mode AI proof;
   - remote E2E;
   - website signed-in visual proof;
   - signing/VM/update/soak/final installer.
8. Coordinate before any master push or production deployment.
9. Build a clean production-descended candidate; do not wholesale-merge the
   current 100-commit branch.
10. Report complete, blocked, and external/manual items separately.

## 13. Safety and ownership boundaries

- Do not touch trading, analyzer, Bitfinex, relay-arm, exchange, live-copy, or
  production-gate files from this Founder task.
- Do not re-arm trading or use unpublished operator workflows.
- Notify the trading owner before any master push or production deployment and
  wait for exact clearance.
- Do not print or persist API keys, tokens, pairing secrets, image bytes, or
  audio data.
- Do not assume GitHub, Vercel, Railway, Neon, or provider access merely because
  environment files or old scripts exist. Verify each connection without
  exposing credentials.
- Do not delete installed applications, worktrees, research archives, build
  caches, or user data without exact-path classification and explicit approval.
- Do not use a Second Brain as another editing authority. It is advisory.
- Do not rebuild installers for text, extension, or ordinary website changes.

## 14. Why seven days did not produce a final public release

The delay was not one compiler alone. It was a combination of:

- a large, duplicated worktree/dependency layout;
- repeated full IDE and installer build cycles before fast lanes stabilized;
- native Electron and packaging problems;
- old installed-version confusion;
- a very broad Stage 0-11 plan mixing IDE, website, AI economics, remote
  infrastructure, token/business ideas, and release engineering;
- production master moving under an independent trading workstream;
- legitimate safety holds around API deployments;
- external signing, clean-VM, live credentials, physical microphone, and soak
  gates;
- too much time spent reconciling strategy messages instead of freezing and
  shipping the smallest user-visible candidate;
- source/unit completion being discussed alongside installed/production
  completion, which made progress sound closer to release than it was.

The next agent should not repeat that operating model. Keep one narrow critical
path, use evidence, and move speculative post-V1 work out of the release gate.

## 15. Definition of done

Founder OS V1 is done only when:

- one signed Founder IDE installer contains the final approved extension;
- clean install, launch, sign-in, update, rollback, uninstall, and soak pass;
- Founder mode is simple and distinct from Void while Developer mode remains
  available;
- managed Auto/Fast/Reasoning/Code and all five work modes pass with truthful
  receipts;
- Personal AI works and local Ollama is either proven or clearly optional;
- screenshot attachment and human speech input work;
- one complete approved local Agent tool loop works;
- website-to-installed-device remote control works with revoke/reconnect;
- website plans, usage, provider, connection, and device states are truthful;
- no protected trading paths are changed;
- final tests, typechecks, screenshots, hashes, signature, and blockers are
  recorded in a release evidence document.

Anything less must be reported as internal beta, partial, structurally complete,
or externally blocked. Do not call it a finished public V1.
