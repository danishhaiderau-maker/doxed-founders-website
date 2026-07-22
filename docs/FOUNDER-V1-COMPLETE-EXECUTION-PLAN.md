# Founder OS V1 Complete Execution Plan

Status date: 2026-07-22

This is the canonical implementation checklist for Founder IDE, embedded
Founder Node, the Founder website, and the V1 release. A feature is complete
only when its acceptance evidence exists. Chat claims are not evidence.

## Product decisions locked for V1

- Founder-managed AI uses DeepSeek only.
- Founder Free always uses DeepSeek V4 Flash while preserving the requested
  task intent. Builder and Team use Flash by default and may explicitly route
  coding, reasoning, and verification escalation to DeepSeek V4 Pro.
- GLM, Claude, OpenAI, Gemini, Groq, Mistral, and other cloud providers are
  available only through the founder's own key and endpoint.
- Local Ollama remains private, offline-capable, unlimited, and unmetered by
  Founder OS.
- Personal provider profiles are unlimited and remember name, base URL, API
  key, model, and optional headers in encrypted IDE settings.
- Founder Free is 200,000 weighted token units per recurring seven-day window.
- Founder Builder is $35/month with 5,000,000 weighted token units per
  recurring seven-day window plus coordination and remote control.
- Founder Team uses a pooled allowance, roles, and audit. Final price and pool
  size must be approved before public checkout.
- Provider pooling across third-party free tiers is rejected for V1.
- Usage and savings claims must be measured against a named baseline and must
  never be invented.

## Stage 0 - One source of truth

- [x] Consolidate current IDE, Node, API, web, installer, and research commits
  onto `release/founder-ide-v1`.
- [x] Preserve dirty legacy source and dragon assets until inventoried.
- [x] Keep trading/analyzer ownership outside the IDE release.
- [ ] Salvage unique evidence, then archive obsolete clean worktrees.
- [ ] Confirm zero unexplained uncommitted changes before release.

Evidence: merge commit, clean status, dirty-file inventory, preserved artifact
manifest.

## Stage 1 - Release truth and signing gates

- [x] Define build, install, signing, updater, and external-blocker gates.
- [x] Prevent unsigned artifacts from being labelled production-ready.
- [x] Keep installer inputs and update hashes deterministic.
- [ ] Obtain an active trusted Windows signing identity.
- [ ] Run clean Windows VM install/uninstall and update/rollback tests.
- [ ] Complete the required soak period with the installed application.

Evidence: release contract, CI result, signature verification, VM screenshots,
installer hashes, soak log.

## Stage 2 - Persistent project graph and founder memory

- [x] Persist files, symbols, imports, decisions, tasks, goals, and receipts.
- [x] Build cold-start context without rereading the full repository.
- [x] Exclude secrets, dependencies, generated files, and large binaries.
- [ ] Add decision provenance and negative memory: rejected approaches are not
  suggested again unless the founder reopens them.
- [ ] Detect repeated solutions and reuse verified prior work.
- [ ] Surface dependency impact before an agent edits shared modules.

Evidence: graph tests, cold-start benchmark, provenance examples, secret-scan
result.

## Stage 3 - Efficiency engine and honest savings

- [x] Compact prompts deterministically and cache reusable context.
- [x] Record baseline input, actual input, cache level, and tokens avoided.
- [x] Version the baseline and weighted-token formula.
- [ ] Add semantic result caching for safe read-only questions.
- [ ] Add exact-diff context retrieval and changed-file invalidation.
- [ ] Show measured savings in usage receipts and dragon notifications.
- [ ] Benchmark representative coding tasks against full-context execution.

Evidence: repeatable benchmark dataset, cache hit tests, measured receipts,
zero unsupported percentage claims.

## Stage 4 - Plans, quota, and cost reservation

- [x] Reserve weighted units atomically before a managed provider request.
- [x] Reconcile authoritative usage after completion.
- [x] Charge the conservative reservation when a started outcome is uncertain.
- [x] Release requests rejected before provider execution.
- [x] Expose cap, used, reserved, remaining, reset time, unit, and formula.
- [x] Make allowance server-authoritative by plan: Free, Builder, Team.
- [x] Add Builder checkout/webhook lifecycle, cancellation, grace, and
  downgrade rules. Team checkout remains truthfully unavailable until price
  and pool are approved.
- [x] Add team pool membership, role, and audit enforcement. Owner/admin/member
  mutations are bounded, and coordination plus membership changes emit a
  server-side audit trail.

Evidence: concurrency tests, replay/idempotency tests, billing webhook tests,
admin and user usage screenshots.

## Stage 5 - Complete AI experience

- [x] Route Founder managed aliases through the authenticated gateway.
- [x] Use DeepSeek Flash for fast work and DeepSeek Pro for code/reasoning.
- [x] Keep unsupported or unhealthy models fail-closed.
- [x] Add encrypted named personal provider profiles with no arbitrary limit.
- [x] Keep local Ollama and direct BYOK outside Founder quota.
- [x] Add microphone/speech-to-text control to the native composer.
- [x] Put every platform-managed legacy website copilot call behind the same
  reservation ledger and enforce DeepSeek-only plus Free Flash policy at the
  final streaming and non-streaming provider boundaries.
- [x] Add truthful route receipts with tier, provider, model, latency, and the
  Free Flash-only policy when it applies.
- [x] Add scheduled live provider capability probes to disable an unhealthy
  managed model before customer traffic reaches it.
- [x] Ensure managed DeepSeek never silently becomes another provider and a
  failed health probe removes it from customer routing until recovery.
- [ ] Escalate Flash to Pro only on measured difficulty or failed QA and record
  the escalation reason in the task receipt.
- [x] Add the seven focused actions: Verify, Challenge, Research, Panel, Test,
  Explain, Optimize. Each drafts a focused, editable instruction above the
  native composer; the selected managed, personal, or local model remains
  explicit in the model menu.
- [ ] Run authenticated native tests for Auto, Fast, Reasoning, Code, BYOK,
  Ollama, streaming content, completion marker, cancellation, and quota errors.

Evidence: provider/model/intent/latency receipts, streaming tests, settings
screenshots, quota tests, native final-response screenshots.

## Stage 6 - Coordinated agents across chats

- [x] Persist a workspace coordination ledger shared by every connected chat.
- [ ] Broadcast goal, scope, branch, files, dependencies, and expected output
  before work starts.
- [x] Acquire short-lived file/path leases with fencing tokens before edits.
- [x] Detect path overlap and require ownership resolution before agents write.
- [x] Prevent stale agents from overwriting a newer lease or result.
- [x] Refresh awareness on events and at a bounded heartbeat while working.
- [x] Allow independent work to continue quietly when scopes do not overlap.
- [ ] Add Focus and Team modes; Team creates bounded specialist tasks.
- [ ] Show a task graph with running, waiting, blocked, verifying, and complete
  states plus a compact coordination indicator.
- [ ] Require a verified merge step for cross-agent output.

Evidence: race tests, stale-lease tests, cross-chat overlap demonstration,
merge receipt, multi-agent task graph screenshots.

## Stage 7 - Founder-native Apple-inspired IDE and website UI

- [ ] Replace icon-only primary navigation with a calm labelled hierarchy:
  New chat, Projects, Chats, Agents, Graph, Connect, and Settings.
- [ ] Keep coding tools available through progressive disclosure rather than
  removing essential editor capability.
- [ ] Remove every visible Void name, action, settings title, and legacy logo.
- [ ] Make Founder settings one coherent surface for account, plans and usage,
  Personal AI, local AI, connections, privacy, remote control, and companion.
- [ ] Put provider selection, mode, attachments, microphone, and send in one
  stable composer without overlap.
- [ ] Preserve and elevate the source-control graph as a Founder differentiator.
- [ ] Use the left activity space for small, labelled Founder shortcuts.
- [ ] Apply the same Overview / Work and Decisions / Connect and Data hierarchy
  to IDE, Founder account, admin, Agent Hub, trading, and analyzer surfaces.
- [ ] Use restrained radii, clear typography, consistent status colors,
  purposeful motion, reduced-motion support, and responsive layouts.
- [ ] Remove obsolete website controls and match visible features to production
  capability.

Evidence: full route/tab screenshot matrix at desktop and narrow widths,
zero Void-brand scans, keyboard/focus audit, no overlap or horizontal overflow.

## Stage 8 - Living Founder Dragon and proof receipts

- [ ] Build one transparent, frameless, always-on-top companion owned by Founder
  IDE; remove duplicate Dragon/IDE launch identities.
- [ ] Make it draggable across screens and remember its position per display.
- [ ] States: resting in nest, listening, planning, flying/working, coordinating,
  blocked, verifying, delivered/fire, failed, offline, and update available.
- [ ] Show compact task cards, measured savings, current agent count, and result
  receipts without exposing secrets.
- [ ] Click opens the relevant task; context menu provides usage, hide/show,
  pause, settings, sign in/out, and reduced motion.
- [ ] Keep transparent assets free of baked-in backgrounds and provide a quiet
  non-animated accessibility state.
- [ ] Ensure the companion never steals typing focus or blocks essential UI.

Evidence: transparent pixel checks, multi-monitor drag test, restart persistence,
state-transition recording, reduced-motion and focus tests.

## Stage 9 - One application with embedded Founder Node

- [x] Bundle Founder Node with Founder IDE rather than shipping a second app.
- [x] Establish authenticated local IPC and fail-closed action contracts.
- [ ] Complete website-to-Node-to-IDE remote actions for read, proposed edit,
  accept/reject, command, cancel, output, and status.
- [ ] Connect IDE sign-in to the Doxxed account/X onboarding flow and register
  the device automatically after approval.
- [ ] Add workspace boundaries, command risk classes, approvals, timeouts,
  idempotency, leases, and receipts.
- [ ] Prove remote control through the production website with a real installed
  device while preserving local control and revocation.

Evidence: paired-device screenshots, replay/rejection tests, audit receipts,
production remote edit and command demonstration.

## Stage 10 - Website and founder business workflows

- [ ] Audit every Founder account and admin route; retain only controls backed
  by production behavior.
- [ ] Align plans, quota, provider settings, connected services, device status,
  projects, chats, agents, graph, and receipts with the IDE.
- [ ] Add proactive founder intelligence: decision archaeology, competitor
  watch, idea validation, repeated-pattern detection, and a daily brief with
  sources and opt-in controls.
- [ ] Specify token launch, vesting, treasury, DCF Swap, liquidity, wallet
  rotation, emergency controls, acquisition offers, holder analytics, and
  settlement as audited business contracts before implementation.
- [ ] Keep founder allocations, unlock schedules, fee destinations, upgrade
  authority, emergency swaps, and acquisition settlement explicit and legally
  reviewed. Never represent a token automatically as equity.
- [ ] Build admin execution as propose, simulate, approve with strong hardware
  authentication, timelock where appropriate, execute, and audit.

Evidence: capability audit, sourced intelligence examples, contract specs,
security review, legal/compliance gate, end-to-end admin simulations.

## Stage 11 - Verify, package, install, and release

- [ ] Enforce verify-then-show: tests, typecheck, build, visual check, and
  evidence receipt before an agent declares completion.
- [ ] Add a cheap independent self-critique pass against the original goal.
- [ ] Auto-repair failed QA; use scoped rollback only when the agent owns the
  entire change and can prove it will not remove founder work.
- [ ] Run API, web, Founder Node, extension, overlay, installer, updater, IPC,
  coordination, quota, and security suites.
- [ ] Visually inspect all states at 1440x900, 1280x720, and a narrow layout;
  capture screenshots and check console, network errors, overflow, focus,
  loading, empty, error, offline, and success states.
- [ ] Build one Founder IDE installer containing Founder Node.
- [ ] Remove old installed Founder IDE/Node/Stack variants only after inventory
  and explicit preservation of user data.
- [ ] Install the release candidate, create one desktop shortcut, pin the single
  Founder IDE identity to the taskbar, and verify restart/update/uninstall.
- [ ] Publish only when signing and clean-machine gates pass. Otherwise label
  the artifact internal testing and report the exact blocker.

Evidence: signed artifact/hash, clean-machine matrix, installed-app screenshots,
test report, visual audit bundle, release receipt, and blocker register.
