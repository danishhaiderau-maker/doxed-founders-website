# Founder IDE Completion Blueprint

| Field | Value |
|---|---|
| Status | Living product and engineering blueprint |
| Updated | 2026-07-21 |
| Goal | One Founder-native desktop product with visible AI work, local relay, remote control, providers, agents, and shipping |
| Evidence | Repository audit plus installed Founder IDE screenshot supplied 2026-07-21 |

## 1. Honest current verdict

Founder IDE is a working Founder-branded Void/VS Code distribution with meaningful Founder infrastructure. It is not yet a Founder-native product experience.

The screenshot shows the exact gap:

- The title and activity icon say Founder IDE, but the shell, editor chrome, empty state, right chat, settings model, colors, spacing, and interaction patterns still read as upstream Void/VS Code.
- The Founder hub is an extension panel beside the inherited product, not the organizing shell of the product.
- There are two mental entry points: **Open Founder Chat** on the left and the inherited **CHAT** panel on the right.
- The center is a stock open-folder/SSH start screen with a decorative upstream object and raw Windows paths, rather than a useful Founder workspace.
- Local/Hybrid/Cloud looks selectable, but the user cannot inspect the exact privacy boundary or see a request-level proof of what stayed local.
- Identity and Node status are visible, but agents, active work, diffs, approvals, tests, Git, deployments, and remote-control sessions are not organized into one workflow.
- The interface uses default blue controls, generic typography, uneven density, and large unused space. It does not yet have the calm, deliberate Founder visual system requested.

Using Void as an engine is still the correct shortcut. The problem is not the inherited editor core; the problem is that the inherited product shell still owns the experience.

## 2. What is real today

| Capability | Current state | Evidence / limitation |
|---|---|---|
| One Windows application | Release-candidate | Installer can bundle and launch the Founder Node relay; signing/clean-machine verification remain release gates |
| Founder ID / X pairing | Implemented | Device-code flow and shared FounderVault credentials exist |
| Embedded local relay | Implemented | IDE starts/stops the bundled relay and uses the shared Node identity |
| Gateway chat streaming | Implemented, production proof incomplete | Gateway client, SSE, routing metadata, retries, cancellation, and auth-expiry handling exist |
| Inline edit, autocomplete, diff chat | Inherited and rewired | Void surfaces remain visually upstream; end-to-end model/provider reliability still needs a clean-machine smoke test |
| Remote IDE actions | Implemented at protocol level | Authenticated IPC supports workspace read, proposed edit, command, cancellation, and results; staging/web round-trip needs product-level proof |
| Local edit/run/read tools | Implemented | Approval-aware workspace edit, terminal command capture, and workspace read exist |
| Local/Hybrid/Cloud selector | Partial | UI state exists; complete fail-closed enforcement and per-request proof are not yet demonstrated |
| Founder settings/hub | Implemented as extension webviews | Better than old Void settings, but duplicated with inherited settings/chat and not yet the main shell |
| Provider connections | Partial | Backend credential storage exists; native multi-provider vault and entitlement-aware IDE flow are missing |
| Memory | Partial | Static/local context exists; live Memory Engine endpoint still has a fallback for unimplemented deployments |
| Execution profiles | Partial | UI state exists; backend persistence is explicitly a stub |
| Agents and live work timeline | Missing as a unified UI | No first-class task list, step stream, file-change timeline, pause/resume/handoff, or budget surface |
| Ship flow | Missing as a unified UI | Git, tests, PR, deploy, verification, and rollback are not one Founder workflow |
| Founder-native visual system | Missing | Branding assets exist, but the workbench remains visually Void/VS Code |

### Installed-product audit on 2026-07-21

This audit read the actual installation at `C:\Users\user\AppData\Local\Programs\Founder IDE`, not only the repository:

- Editor core reports VS Code `1.99.3`, commit `b3166e7ef2aefbdfeb139445fdf248a561b85d4d`, x64.
- Installed Founder extension is `doxxedcrypto.founder-ide-extension@0.2.0`.
- `Founder IDE.exe` is **not digitally signed**.
- Windows file metadata reports product description `Electron` and version `34.3.2`, not a Founder IDE release identity.
- Installed `product.json` still has internal `void-tunnel`, `void-tunnelservice`, and `void-editor` identifiers. Internal compatibility names may remain only when proven necessary; Linux icons and any user-visible service names must be Founder-owned.
- The installed remote server download template contains an empty release tag/version and a space-bearing artifact name: `releases/download//founder ide-reh-${os}-${arch}-.tar.gz`. Remote-server download/update must be treated as broken until an installed-product test proves otherwise.
- The desktop shortcut exists, but shortcut presence does not prove taskbar pinning, update ownership, or clean uninstall behavior.

These are release blockers or explicit platform limitations. They are not overridden by passing extension tests.

## 3. Product model

The application has three primary desks and no competing chat products:

1. **Build** - conversation, objective, plan, visible edits, diff, terminal, tests, and contextual approvals.
2. **Agents** - active/recent tasks, workers, progress, files, cost, pause, stop, resume, and handoff.
3. **Ship** - changes, checks, commit, PR, deploy, health verification, rollback, and launch readiness.

Secondary surfaces live in a compact account/settings menu:

- Founder ID and plan
- AI and provider connections
- Privacy and infrastructure mode
- Founder Node and remote devices
- Notifications
- Advanced editor controls

Explorer, Search, Source Control, Run, and Extensions remain available for developers, but they support the three desks instead of defining the product.

## 4. First-run experience

The first screen must be the product, not a marketing page and not the stock VS Code welcome screen.

1. **Welcome:** Founder IDE, one sentence, Continue with X, or Work locally.
2. **Node:** embedded relay starts automatically; show a simple health check, not setup instructions.
3. **Privacy:** choose Local, Hybrid, or Cloud with a short consequence summary and inspectable details.
4. **Workspace:** Open folder, Clone from GitHub, Open SSH, or continue a recent workspace. Paths are secondary and truncated intelligently.
5. **AI:** Founder Auto is ready by default; connect a personal provider or local Ollama without seeing a wall of model IDs.
6. **Ready:** open the Build desk with one suggested next action based on the workspace.

Returning users go directly to their last desk and task state.

## 5. Founder-native visual system

The target is calm and highly legible, inspired by Apple's restraint but not a visual copy of macOS.

### Shell

- Founder wordmark/product name is a first-viewport signal.
- A compact top command bar holds workspace, current task, search/command, privacy state, Node state, and account.
- Use one left navigation rail and one contextual right inspector. Avoid simultaneous unrelated sidebars.
- The center always carries real work: conversation plus editor/diff/terminal, recent work, or a purposeful empty state.
- No nested cards, decorative gradients, floating blobs, oversized rounded controls, or giant empty panels.

### Tokens

- Define semantic color tokens for canvas, raised surface, divider, text, muted text, action, success, warning, error, and diff states.
- Use a neutral graphite base with restrained green Founder identity and blue only for familiar editor links/actions.
- Support polished dark and light themes from the same tokens.
- Establish an 8px spacing grid, compact 4-8px radii, stable icon-button dimensions, zero negative letter spacing, and accessible focus states.
- Use the existing icon library/Codicons for editor actions; use text only when the command is not clear from a familiar icon.

### Required replacement work

- Replace the stock startup editor and decorative upstream object.
- Replace every visible `Void`, `Void's Settings`, upstream onboarding, upstream empty state, and upstream provider label.
- Rename internal command IDs only where they leak to users; preserve upstream boundaries where renaming would create needless maintenance risk.
- Redesign chat header, composer, thread list, diff review, model intent control, and error states as one Founder system.
- Remove duplicate status-bar items and compress health into one inspectable Founder status control.
- Keep upstream license attribution in About/Open Source Notices. Product differentiation does not mean hiding provenance.

## 6. Complete functional backlog

### A. Product shell and navigation

- Founder startup/home editor with recent workspaces, current/recent tasks, Node/identity health, and one primary action.
- Three desks: Build, Agents, Ship.
- Unified command palette entries and deep links for every primary state.
- Responsive behavior for laptop, wide monitor, narrow window, high DPI, and screen zoom.
- Persist layout and task context per workspace.

### B. Account, plan, and onboarding

- X/Founder ID sign-in, sign-out, expiry, device-code cancellation, and recovery states inside the IDE.
- Account switcher and clear separation between website identity, Node identity, GitHub identity, and wallet identity.
- Plan, allowance, provider slots, DDollar use, renewal, and honest limit states.
- No browser detour for ordinary account/provider setup; web is used only when OAuth or payment requires it.

### C. Provider Vault and routing

- Founder Auto first; Fast, Balanced, Architect, and Autonomous intents instead of raw model catalogues.
- Remember multiple OpenAI, Anthropic, Gemini, DeepSeek, GLM, OpenRouter, Ollama, and approved compatible connections.
- Connect, verify, rename, enable per workspace, reorder fallback, view health, and revoke.
- Enforce plan entitlements server-side and show exact reason/fix when a route is unavailable.
- Never expose raw keys in settings JSON, IPC, logs, webview storage, crash reports, or chat transcripts.
- Show request receipts: mode, route, provider/model, token/cost, latency, and whether cloud data left the device.

### D. Privacy and local infrastructure

- Local means no prompt, code, attachment, embedding, derived context, or telemetry leaves the device.
- Hybrid provides a per-service allowlist and per-request boundary preview.
- Cloud uses only enabled Founder-managed or personal routes.
- Node screen shows service health, local models, ports without secrets, queue, last sync, and remote-session state.
- Add a privacy test that deliberately blocks network egress and proves Local still works.

### E. Build desk and Cursor-like live editing

- One conversation with plan, tool calls, progress, and final result in chronological order.
- Stream the active step and show files as they are read or edited.
- Open affected code beside chat automatically; reveal live edits with stable decorations.
- Proposed changes appear as an editable diff with accept file, accept hunk, reject, and restore.
- Support inline `Ctrl+K`, selection chat, autocomplete, whole-task agent mode, checkpoints, undo, and cancel.
- Terminal commands stream output in the task timeline and can be stopped.
- Diagnostics and failed tests feed back into the same task so the agent can repair and retry.
- Never claim an edit, test, commit, or deploy completed without the matching receipt.

### F. Agents desk

- Active and recent tasks with objective, status, current step, branch/worktree, elapsed time, cost, and changed files.
- Expand a task into its timeline, logs, approvals, checkpoints, and artifacts.
- Pause, resume, cancel, retry, hand off, and open the exact file/diff/terminal.
- Bounded parallel agents with explicit file ownership and worktree isolation.
- Budget and permission controls at task level; destructive/network/deploy actions remain approval-aware.

### G. Ship desk

- Show source changes, unresolved conflicts, secret scan, typecheck, lint, tests, and build status.
- Stage/unstage, write commit, push, create/view PR, inspect reviews and CI.
- GitHub, Vercel, Railway, Neon, and later providers show connected identity, project mapping, last deployment, and health.
- Preview deploy, production deploy, migration preview, rollout health, and rollback use one progressive flow.
- Deploy receipts include commit, environment, URL, provider job, schema migration, checks, and rollback target.
- Token launch readiness is contextual after product, trust, legal, wallet, and contract gates pass.

### H. Web/mobile remote control

- Website discovers the paired online Node without exposing the local machine publicly.
- Start a bounded task, stream steps/output, approve edits/commands, cancel, and inspect completion.
- IDE visibly indicates a remote-controlled session and who initiated it.
- Workspace allowlist, session expiry, replay protection, rate limits, and an immediate local kill switch.
- Offline queue is explicit; no silent execution after a stale approval.

### I. Memory and project intelligence

- Workspace memory: goals, architecture, decisions, conventions, dependencies, deployments, and known risks.
- User can inspect, correct, pin, export, and delete memory.
- Retrieval is scoped by workspace and privacy mode and cites its sources.
- Compact context budgeting prevents stale history from crowding out current code.

### J. Reliability, updates, and support

- Signed installer and updates, SHA-256 verification, rollback, and one uninstall owner.
- Clean removal of legacy Founder IDE/Node/Stack while preserving user-selected vault/settings data.
- Crash recovery restores unsent prompts, task timeline, open diffs, and terminal state where possible.
- Health screen diagnoses identity, Node, gateway, provider, IPC, extension, updater, GitHub, and deployment connections.
- Redacted diagnostic export and one-click copy of a support bundle manifest.

### K. Accessibility and performance

- Full keyboard navigation, screen-reader names, high contrast, reduced motion, visible focus, and 200% zoom.
- Startup and interaction budgets; no layout shifts when status, filenames, or model labels change.
- Virtualize long threads and logs; keep typing, scrolling, inline edits, and terminal output responsive.
- Test dark/light themes at 1280x720, 1440x900, 1920x1080, ultrawide, and narrow split-screen.

### L. Security and supply chain

- Pin and audit the Void/VS Code upstream revision; maintain a small, documented overlay.
- Threat-model IPC, webviews, extension host, local relay, updater, OAuth/device code, provider secrets, and remote actions.
- Webviews use strict CSP and message schemas; local IPC is authenticated, replay-resistant, and fail-closed.
- Dependency/SBOM, license notices, secret scanning, artifact hashes, signing provenance, and reproducible release evidence.
- Permission receipts distinguish read, edit, command, network, Git, deploy, wallet, and destructive actions.

## 7. Ordered delivery plan

### Milestone 1: Founder shell

- Implement design tokens, Founder startup/home, Build/Agents/Ship navigation, unified status, and deliberate empty/loading/error states.
- Remove all visible Void wording and duplicate Founder/upstream entry points.
- Reuse the current compiled upstream checkout during development; do one release build after the milestone, not a fresh fork for every UI change.

**Exit:** the supplied screenshot can no longer be mistaken for Void with a Founder extension installed.

### Milestone 2: Native account, privacy, and Provider Vault

- Bring account, Node, modes, provider connections, entitlements, and request receipts into the shell.
- Enforce Local/Hybrid/Cloud below the UI.

**Exit:** a new user signs in, chooses privacy, connects or selects AI, and sends a successful request without visiting raw settings.

### Milestone 3: One Build experience

- Unify inherited chat, inline edit, autocomplete, task timeline, diffs, approvals, terminal/tests, checkpoints, and errors.

**Exit:** the user watches code change beside the conversation, reviews the diff, runs tests, and accepts or rolls back without switching mental models.

### Milestone 4: Agents and remote control

- First-class task runtime, bounded parallel work, web-to-Node sessions, live state, approvals, cancellation, and audit receipts.

**Exit:** a task started on the website becomes visibly active in the IDE and cannot exceed its workspace/permission boundary.

### Milestone 5: Ship

- Git/GitHub, CI, Vercel, Railway, Neon, verification, and rollback in one release flow.

**Exit:** a verified commit moves from local changes to a healthy deployment with a complete receipt.

### Milestone 6: Production hardening

- Signing, updater rollback, clean install/uninstall, accessibility, performance, security audit, soak, and release support.

**Exit:** signed clean-machine builds pass the complete acceptance suite on supported Windows versions; macOS/Linux follow their declared support plan.

## 8. Completion acceptance story

Founder IDE is complete enough for public testing only when this entire story passes:

1. Install one signed application on a clean machine; no separate Node install is required.
2. Continue with X or choose Local; Node and account state are clear and recoverable.
3. Open/clone a real repository and choose Local, Hybrid, or Cloud.
4. Ask Founder to implement a multi-file change.
5. Watch its plan, file reads, live edits, command output, and test repair beside the code.
6. Accept/reject hunks, cancel midway, restore a checkpoint, and resume safely.
7. Close and reopen the IDE; workspace, task, provider, and safe memory state return.
8. Start another task from the website; see it appear locally, approve one command, then stop it from the IDE.
9. Commit, open a PR, observe CI, deploy a preview, verify health, and roll back.
10. Switch to Local and prove through network-denial tests that no protected content leaves the device.
11. Rotate/revoke identity/provider sessions and confirm old credentials immediately fail.
12. Update the IDE, verify the signed artifact, recover from an interrupted update, and uninstall without orphan processes or shortcuts.

## 9. Evidence rules

Every milestone report uses only these labels:

- **Done and verified:** code plus passing automated and manual evidence.
- **Implemented, not fully verified:** code exists but a required environment or end-to-end proof is missing.
- **Designed only:** specification/mockup exists; no production behavior.
- **Externally blocked:** signing authority, provider credential, legal review, hardware/VM, or required wall-clock soak is unavailable.

Screenshots alone do not prove behavior. Unit tests alone do not prove the installed product. A feature is complete only when the installed application passes its user journey.

## 10. Living idea log

New Founder IDE ideas should be appended here only when they support one of the three desks or remove user effort. Record the user problem, proposed behavior, privacy/security effect, dependency, and acceptance test. Do not add another permanent navigation item by default.

Current ideas to preserve:

- Intent-based AI routing with multiple remembered personal providers.
- Local-first privacy proof per request.
- Live code edits beside the conversation, with understandable checkpoints.
- Website/mobile remote control through the embedded Node.
- Launch Partner workflows that connect building, deployment, trust review, and token-launch readiness.
- Provider connection recipes for GitHub, Vercel, Railway, Neon, and later infrastructure without exposing secrets.
- A calm Apple-like flow: progressive disclosure, one dominant action, polished motion, and fewer decisions per screen.
