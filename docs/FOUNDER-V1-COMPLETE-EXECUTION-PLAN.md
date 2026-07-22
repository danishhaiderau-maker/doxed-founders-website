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
- The managed model is infrastructure rather than the product moat. Founder OS
  competes on verified execution, project memory, coordination, retrieval,
  receipts, and a simpler workflow that can make an inexpensive model perform
  reliably.
- Every managed request must use a byte-stable, versioned prefix wherever
  possible: system policy, tool schemas, repository map, and project rules come
  before changing task and conversation context.
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
- [x] Add decision provenance and negative memory: rejected approaches are not
  suggested again unless the founder reopens them.
- [x] Detect repeated solutions and reuse verified prior work.
- [x] Surface dependency impact before an agent edits shared modules.
- [ ] Link chats to projects and goals so a founder can resume work without
  reconstructing context from message history.
- [x] Store compact verified solution patterns with commit, checks, affected
  symbols, and invalidation conditions; never reuse an unverified answer.
- [ ] Add a founder-controlled daily project brief covering completed work,
  blockers, decisions, usage, savings, and suggested next actions.

Evidence: graph tests, cold-start benchmark, provenance examples, secret-scan
result.

## Stage 3 - Efficiency engine and honest savings

- [x] Compact prompts deterministically and cache reusable context.
- [x] Record baseline input, actual input, cache level, and tokens avoided.
- [x] Version the baseline and weighted-token formula.
- [x] Add semantic result caching for safe read-only questions.
- [x] Add exact-diff context retrieval and changed-file invalidation.
- [x] Show measured savings in usage receipts and dragon notifications.
- [ ] Benchmark representative coding tasks against full-context execution.
- [ ] Keep the cacheable prefix byte-stable across turns and record provider
  cache-hit input separately from ordinary input.
- [ ] Invalidate file, symbol, dependency, solution, and semantic caches from a
  single changed-file event rather than waiting for stale output to fail.
- [x] Reserve cache reuse for read-only or equivalently proven results; tool
  execution, secrets, changing remote state, and destructive actions bypass it.
- [ ] Show both token and estimated-cost comparisons against an explicit named
  baseline; marketing percentages remain hidden until the benchmark proves
  them.

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
- [x] Present allowances as a friendly weekly quota in the product while usage
  receipts retain exact weighted units, raw tokens, cache status, and cost.
- [x] Add low-quota warnings, reservation visibility, renewal time, upgrade,
  cancellation, grace, and fail-closed exhausted states in both IDE and web.
- [x] Keep personal keys and local models outside the managed quota; plan limits
  apply to managed inference and paid collaboration capabilities rather than
  silently restricting founders from connecting their own providers.

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
- [x] Escalate Flash to Pro only on measured difficulty or failed QA and record
  the escalation reason in the task receipt.
- [x] Add the seven focused actions: Verify, Challenge, Research, Panel, Test,
  Explain, Optimize. Each drafts a focused, editable instruction above the
  native composer; the selected managed, personal, or local model remains
  explicit in the model menu.
- [ ] Run authenticated native tests for Auto, Fast, Reasoning, Code, BYOK,
  Ollama, streaming content, completion marker, cancellation, and quota errors.
- [ ] Make model identity truthful in the native answer and receipt: managed
  responses disclose the resolved DeepSeek route; personal/local routes show
  their configured profile without exposing a key.
- [ ] Make the composer model picker switch instantly among Founder Auto, Fast,
  Reasoning, Code, Ollama, and every named personal profile.
- [ ] Support OpenAI-compatible profiles through name, base URL, key, model,
  optional headers, connectivity test, edit, disable, and delete controls.
- [ ] Add input-device choice, permission/error states, transcription preview,
  cancel, and keyboard-accessible microphone behavior.

Evidence: provider/model/intent/latency receipts, streaming tests, settings
screenshots, quota tests, native final-response screenshots.

## Stage 6 - Coordinated agents across chats

- [x] Persist a workspace coordination ledger shared by every connected chat.
- [x] Broadcast goal, scope, branch, files, dependencies, and expected output
  before work starts.
- [x] Acquire short-lived file/path leases with fencing tokens before edits.
- [x] Detect path overlap and require ownership resolution before agents write.
- [x] Prevent stale agents from overwriting a newer lease or result.
- [x] Refresh awareness on events and at a bounded heartbeat while working.
- [x] Allow independent work to continue quietly when scopes do not overlap.
- [x] Add Focus and Team modes; Team creates bounded specialist tasks.
- [x] Show a task graph with running, waiting, blocked, verifying, and complete
  states plus a compact coordination indicator.
- [x] Require a verified merge step for cross-agent output.
- [x] Make every active chat publish a lightweight awareness heartbeat at least
  every 10-15 minutes and immediately on goal, scope, file, blocker, and result
  changes.
- [x] Surface nearby work before an agent acts; coordinate when goals, files, or
  dependencies overlap and keep unrelated work quiet.
- [x] Give each task a clear goal, expected output, owned paths, dependencies,
  budget, parent goal, verification state, and final receipt.
- [x] Keep Team bounded to useful specialist roles selected for the goal rather
  than displaying an arbitrary agent count; default Focus remains one agent.
- [x] Show overlap, waiting, blocked, verification, and merge decisions in a
  founder-readable activity timeline and graph.

Evidence: race tests, stale-lease tests, cross-chat overlap demonstration,
merge receipt, multi-agent task graph screenshots.

## Stage 7 - Founder-native Apple-inspired IDE and website UI

- [ ] Replace icon-only primary navigation with a calm labelled hierarchy:
  New chat, Projects, Chats, Agents, Graph, Connect, and Settings.
- [ ] Give Projects and Chats first-class persistent navigation, searchable
  history, useful names, pin/archive controls, and a clear new-chat action.
- [ ] Widen the primary navigation enough for readable labels and project/chat
  context; use compact icons only for familiar secondary editor tools.
- [ ] Keep coding tools available through progressive disclosure rather than
  removing essential editor capability.
- [ ] Remove every visible Void name, action, settings title, and legacy logo.
- [ ] Make Founder settings one coherent surface for account, plans and usage,
  Personal AI, local AI, connections, privacy, remote control, and companion.
- [ ] Remove duplicate settings entry points and route the global settings icon
  to the coherent Founder surface; keep advanced editor settings progressively
  disclosed inside it.
- [ ] Put provider selection, mode, attachments, microphone, and send in one
  stable composer without overlap.
- [ ] Preserve and elevate the source-control graph as a Founder differentiator.
- [ ] Use the left activity space for small, labelled Founder shortcuts.
- [ ] Make Chat the primary founder workflow while retaining Explorer, Search,
  Source Control/Graph, Run, Extensions, and debugging capabilities under a
  clear Build and ship section.
- [ ] Apply the same Overview / Work and Decisions / Connect and Data hierarchy
  to IDE, Founder account, admin, Agent Hub, trading, and analyzer surfaces.
- [ ] Use restrained radii, clear typography, consistent status colors,
  purposeful motion, reduced-motion support, and responsive layouts.
- [ ] Remove obsolete website controls and match visible features to production
  capability.
- [ ] Use a restrained multi-color status language: neutral surfaces, one clear
  action accent, green healthy/success, amber attention/verification, red only
  for destructive/error, and blue for active navigation.
- [ ] Add short purposeful transitions for state changes, loading, task travel,
  and completed work while honoring reduced motion and avoiding decorative
  animation in dense work surfaces.
- [ ] Audit empty, loading, partial, offline, unauthorized, quota-exhausted,
  error, success, and long-content states in every changed surface.

Evidence: full route/tab screenshot matrix at desktop and narrow widths,
zero Void-brand scans, keyboard/focus audit, no overlap or horizontal overflow.

## Stage 8 - Living Founder Dragon and proof receipts

- [x] Build one transparent, frameless, always-on-top companion owned by Founder
  IDE; remove duplicate Dragon/IDE launch identities.
- [ ] Make it draggable across screens and remember its position per display.
- [x] States: resting in nest, listening, planning, flying/working, coordinating,
  blocked, verifying, delivered/fire, failed, offline, and update available.
- [ ] Drive states from real task, network, coordination, verification, usage,
  and update events rather than a cosmetic timer.
- [ ] Show compact task cards, measured savings, current agent count, and result
  receipts without exposing secrets.
- [ ] Click opens the relevant task; context menu provides usage, hide/show,
  pause, settings, sign in/out, and reduced motion.
- [ ] Keep transparent assets free of baked-in backgrounds and provide a quiet
  non-animated accessibility state.
- [x] Ensure the companion never steals typing focus or blocks essential UI.
- [ ] Keep one recognisable dragon across every state, with transparent
  animation assets sized for a desktop companion rather than a video panel.
- [ ] Support snap-to-edge, multi-monitor work areas, temporary click-through,
  hide until next task, and recovery when a saved display is disconnected.
- [x] Announce delivered work with a compact two-line result card and a brief
  fire state; resting returns the dragon to its nest without visual noise.

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
- [ ] Keep one installer, one application identity, one tray/taskbar identity,
  one updater, and one uninstall entry; Founder Node runs as an embedded
  background capability with no second founder-facing app.
- [ ] Show device online/offline, current workspace, pending approval, last
  activity, revoke, rename, and reconnect states consistently in IDE and web.
- [ ] Add connected-service flows for GitHub, Vercel, Railway, Neon, email, and
  Telegram with least-privilege scopes, health, reconnect, and revoke controls.
- [ ] Keep remote edits proposed until locally or remotely approved according
  to policy; high-risk commands always require explicit approval.

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
- [ ] Resolve and publish one token economics contract covering the bonding
  curve fee, any platform share, founder share, graduation threshold, DCF Swap
  fee destination, and every treasury action before deployment.
- [ ] Model the proposed 50% founder/team allocation as continuous daily vesting
  over ten years, claimable only by the configured founder wallet, with public
  schedule, claimed, available, and remaining supply.
- [ ] Model platform-received project tokens as a transparent locked treasury
  with the proposed ten-month 10% monthly release option; prohibit automatic
  sale, burn, or liquidity movement until the approved policy is explicit.
- [ ] Add a basket-level treasury simulator so an approved sale can distribute
  impact across projects rather than unexpectedly dumping one asset; enforce
  slippage, liquidity, rate, value, and cooldown bounds.
- [ ] Keep early platform-token treasury policy configurable through governed
  proposals, including the proposed liquidity-first phase until a verified
  market-cap threshold and a later buyback/burn phase.
- [ ] Build wallet rotation around role-separated multisig or smart-account
  authority, hardware-backed admin authentication, delay, guardian/recovery,
  test transaction, and audit; application code never stores raw private keys.
- [ ] Treat emergency asset or token migration as a governed recovery plan with
  pause, snapshot, simulation, notice, claim/migration contract, timelock, and
  independent security review rather than a unilateral one-click replacement.
- [ ] Give every project an acquisition-interest workflow with verified buyer
  contact, private founder notification, nonbinding offer status, and public
  on-chain metrics without exposing private founder data.
- [ ] Add acquisition settlement only after legal review: consideration can be
  escrowed and claimed pro rata against burned/redeemed tokens, while the
  product clearly states that a token is not automatically equity or a share.
- [ ] Add project analytics for liquidity, volume, circulating/locked/vested
  supply, holder concentration, holder-age cohorts, founder holdings, treasury,
  unlock calendar, and acquisition data using sourced on-chain indexers.
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
- [ ] Add an opt-in daily self-QA run that checks the last 24 hours of owned
  changes, builds/tests affected modules, probes configured links and services,
  captures changed UI states, records failures, and proposes bounded repairs.
- [ ] Require self-QA to respect ownership and active coordination leases so it
  cannot undo a founder's or another agent's concurrent work.
- [ ] Verify desktop shortcut, taskbar pin, app identity, installed version,
  embedded Node lifecycle, sign-in, managed AI, personal AI, microphone,
  navigation, Graph, coordination, Dragon, remote control, update, and uninstall
  from the actual installed application.

Evidence: signed artifact/hash, clean-machine matrix, installed-app screenshots,
test report, visual audit bundle, release receipt, and blocker register.
