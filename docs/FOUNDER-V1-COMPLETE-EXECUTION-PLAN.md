# Founder OS V1 Complete Execution Plan

Status date: 2026-07-29

This is the canonical implementation checklist for Founder IDE, embedded
Founder Node, the Founder website, and the V1 release. A feature is complete
only when its acceptance evidence exists. Chat claims are not evidence.

## Product decisions locked for V1

- Founder-managed AI uses DeepSeek only.
- `founder-os-auto` and `founder-os-fast` prefer DeepSeek V4 Flash;
  `founder-os-reasoning` and `founder-os-code` use DeepSeek V4 Pro. Auto may
  escalate to Pro only from recorded task evidence and plan entitlement.
- Founder Free always uses DeepSeek V4 Flash while preserving the requested
  task intent. Builder and Team use Flash by default and may explicitly route
  coding, reasoning, and verification escalation to DeepSeek V4 Pro.
- GLM, Claude, OpenAI, Gemini, Groq, Mistral, and other cloud providers are
  available only through the founder's own key and endpoint.
- Local Ollama remains private, offline-capable, unlimited, and unmetered by
  Founder OS.
- Personal provider profiles are unlimited and remember name, base URL, API
  key, model, and optional headers in encrypted IDE settings.
- The native composer presents one primary Founder AI route by default.
  Founder Auto uses managed DeepSeek Flash for ordinary work and may use Pro
  only for explicit Code/Reasoning work or a recorded evidence-based
  escalation. Personal and local profiles remain available from the same
  model menu without turning the composer into a provider dashboard.
- The customer sees one `Founder AI`, not an internal provider matrix. A
  higher-cost managed model may handle an exceptional escalation only after
  an authenticated completion probe, a recorded escalation reason, entitlement
  and budget checks, and a truthful receipt. A configured-but-unproven admin
  key never makes a route eligible.
- DeepSeek V4 Flash remains the ordinary managed route and DeepSeek V4 Pro
  remains the explicit Code/Reasoning route. A future managed expert lane such
  as GLM may be enabled only behind a feature flag after a live authenticated
  provider/model probe, a repeatable quality-versus-cost benchmark, plan and
  budget enforcement, a recorded reason, and fail-closed fallback. It is not
  part of the public V1 promise while those gates remain unproven.
- The native composer has five founder-readable work modes: Ask, Plan, Build,
  Debug, and Team. Ask is read-only; Plan inspects and proposes without edits;
  Build has one editing owner; Debug follows reproduce, isolate, fix, verify;
  Team adds bounded read-only specialists around one editing owner.
- Skills are reusable, versioned workflows selected inside a work mode. They
  do not become a new permanent activity icon each time a capability is added.
  Every skill declares tools, permissions, budget, evidence, and output
  contract.
- New V1 capabilities are extension-first. Change the workbench core only when
  the extension, embedded Founder Node, or website boundary cannot provide the
  capability, and record that reason in the release evidence. This keeps
  ordinary product iteration on the fast build lane.
- The existing working system is presumed valuable. Before editing, every
  implementation plan declares `reuse`, `extend`, or `replace`, identifies
  affected architecture nodes and protected scopes, and sets a rewrite budget.
  Replacement, deletion, public-API removal, or a budget increase requires
  evidence and explicit approval rather than model preference.
- Second Brain review is advisory and bounded. It cites evidence, records
  confidence and dissent, and stops after three discussion rounds. The editing
  agent remains responsible for the implementation; the founder remains the
  final authority.
- Independent review is a first-class "Second brain" workflow, not seven
  generic prompt chips. The founder chooses an enabled Personal AI profile and
  one review goal: QA the result, audit the approach, check competitive
  advantage, or write a custom review. The reviewer receives the original
  goal, relevant project graph, current result, changed files, checks,
  receipts, and unresolved risks before it answers.
- A second-brain result never edits the workspace directly. Founder AI
  reconciles the review with the evidence, shows agreements and disagreements,
  and asks for approval before applying a proposed correction.
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
- The supported managed boundary uses only `deepseek-v4-flash` and
  `deepseek-v4-pro`. Retiring aliases such as `deepseek-chat` and
  `deepseek-reasoner` are never sent upstream after 2026-07-24.
- DeepSeek advertises a 1,000,000-token context and up to 384,000 output
  tokens for these routes. Founder OS still applies smaller task-specific
  output limits, quota reservations, and context retrieval rather than
  treating the provider maximum as a safe default.
- Usage and savings claims must be measured against a named baseline and must
  never be invented.
- Managed cost receipts use the versioned DeepSeek price card supplied for
  2026-07-22: V4 Flash $0.0028/M cached input, $0.14/M uncached input, and
  $0.28/M output; V4 Pro $0.003625/M cached input, $0.435/M uncached input,
  and $0.87/M output. A price change creates a new version rather than silently
  rewriting historical receipts.
- Product economics use observed provider usage and cache hits. The example
  70% cache-hit margin scenario is a forecast, not a customer-facing promise;
  savings copy remains hidden until measured against the named full-context,
  uncached-input baseline.

## Stage 0 - One source of truth

- [x] Consolidate current IDE, Node, API, web, installer, and research commits
  onto `release/founder-ide-v1`.
- [x] Preserve dirty legacy source and dragon assets until inventoried.
- [x] Keep trading/analyzer ownership outside the IDE release.
- [ ] Salvage unique evidence, then archive obsolete clean worktrees.
- [x] Confirm zero unexplained uncommitted changes in the canonical release
  worktree. Dirty legacy/bot checkouts are inventoried and preserved for their
  owning workstreams rather than mixed into this release.

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
- [x] Link chats to projects and goals so a founder can resume work without
  reconstructing context from message history.
- [x] Store compact verified solution patterns with commit, checks, affected
  symbols, and invalidation conditions; never reuse an unverified answer.
- [x] Add a founder-controlled daily project brief covering completed work,
  blockers, decisions, usage, savings, and suggested next actions.
- [x] Expose a local Code Intelligence service from Founder Node for Founder
  IDE and other explicitly connected clients. V1 must incrementally hash files,
  index symbols, imports, and reverse dependencies, produce a compact
  active-file-seeded repository map, and return bounded
  `(file, startLine, endLine, score, reason)` evidence rather than whole files.
  A vector store is optional until a benchmark proves it improves this
  deterministic graph-first path.
- [x] Prove changed-file invalidation, workspace isolation, ignored and
  secret-file exclusion, bounded MCP output, cold-start recovery, and useful
  retrieval on representative repositories before advertising token savings
  from Code Intelligence. Branch identity remains part of the persisted
  workspace key and is revalidated during the final multi-worktree release
  matrix.

Evidence: graph tests, cold-start benchmark, provenance examples, secret-scan
result.

## Stage 3 - Efficiency engine and honest savings

- [x] Compact prompts deterministically and cache reusable context.
- [x] Record baseline input, actual input, cache level, and tokens avoided.
- [x] Version the baseline and weighted-token formula.
- [x] Add semantic result caching for safe read-only questions.
- [x] Add exact-diff context retrieval and changed-file invalidation.
- [x] Show measured savings in usage receipts and dragon notifications.
- [x] Benchmark representative coding tasks against full-context execution.
- [x] Keep the cacheable prefix byte-stable across turns and record provider
  cache-hit input separately from ordinary input.
- [x] Invalidate file, symbol, dependency, solution, and semantic caches from a
  single changed-file event rather than waiting for stale output to fail.
- [x] Reserve cache reuse for read-only or equivalently proven results; tool
  execution, secrets, changing remote state, and destructive actions bypass it.
- [x] Show both token and estimated-cost comparisons against an explicit named
  baseline; marketing percentages remain hidden until the benchmark proves
  them.
- [x] Keep the Founder AI gateway enabled by default, require an explicit
  emergency rollback to reopen legacy paths, and attribute every provider
  boundary to a typed call-site ID and budget domain. Strict release mode
  blocks unscoped egress before its network request; unknown routing sections
  fail registration tests instead of disappearing into an `other` bucket.

Evidence: repeatable benchmark dataset, cache hit tests, measured receipts,
zero unsupported percentage claims, provider-egress snapshot and strict-mode
tests.

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
- [x] Replace the browser speech dependency in source with bounded local WAV
  capture and an explicit Personal AI GLM transcription command.
- [ ] Deliver working microphone/speech-to-text in the refreshed installed
  application.
- [x] Capture microphone audio locally as a bounded WAV recording, show
  permission/listening/preview/error states, and transcribe it through an
  explicitly selected speech provider. For V1, an enabled Personal AI GLM
  profile may use the official `glm-asr-2512` transcription boundary; typed
  composer text must survive every recording or provider failure.
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
- [x] Replace Verify, Challenge, Research, Panel, Test, Explain, and Optimize
  chips with one compact Second brain control. It must select an enabled BYOK
  or local reviewer and provide QA, architecture audit, competition check, and
  custom-review intents without crowding the composer.
- [x] Build a deterministic reviewer context pack containing the original
  founder goal, project/decision graph, current answer or patch, changed files,
  test and visual evidence, route receipts, and known risks. Bound and redact
  the pack before sending it to the founder-selected reviewer.
- [x] Require a structured reviewer result with verdict, verified defects,
  evidence references, opinion, proposed correction, confidence, competitive
  analysis, and unanswered questions. Generic acknowledgements such as
  "I will inspect the repository" are an incomplete result, not success.
- [x] Reconcile the independent review through Founder AI: distinguish verified
  defects from opinion, propose the smallest correction, preserve dissent in
  the receipt, and never apply reviewer output without the normal approval and
  ownership gates. The source path is read-only and fail-closed; refreshed
  installed-app proof remains part of the Stage 11 acceptance run.
- [x] Add a simple work-mode menu modelled on the useful parts of modern AI
  workspaces: Ask (read only), Plan (no edits), Build (one editing owner),
  Debug (focused diagnosis), and Team (bounded coordinated specialists).
- [ ] Prove all five work modes in the refreshed installed application,
  including that Ask and Plan cannot edit, Build and Debug keep one editing
  owner, and Team exposes the bounded coordination receipt.
- [x] Treat skills as reusable, versioned workflows attached to these five
  modes rather than adding another permanent icon for every capability.
  Skills declare tools, permissions, evidence requirements, budget, and output
  contract; risky or destructive tools still require explicit approval. V1
  attaches one stable built-in workflow to each visible mode and exposes the
  selected skill in the response receipt without adding another navigation
  surface.
- [x] Implement the Founder Settings Personal AI manager with name, connection
  kind, base URL, key, model, optional headers, edit-with-secret-preservation,
  connectivity test, enable/disable, delete, and active selection. Secrets use
  operating-system encrypted storage and are never returned to the webview.
- [x] Bridge Founder Settings into the native chat picker and direct-provider
  runtime so the settings gear has one Founder-owned destination and changing
  a route is not a cosmetic selection.
- [ ] Complete the existing screenshot attachment path end to end. Paste,
  browse, and drag/drop must work in Ask, Plan, Build, Debug, and Team; each
  image receives one bounded vision/OCR pass and the text-only working model
  receives a structured, explicitly untrusted description of layout, visible
  text, annotations, and likely intent rather than raw image bytes.
- [x] Implement the authenticated `/api/v1/images/descriptions` boundary used
  by the installed composer, enforce the existing type/count/byte limits, cap
  each structured description at 2,000 tokens, preserve attachments and typed
  text on failure, and show a clear fail-closed message when no permitted
  vision route is available.
- [x] Support a founder-selected Personal AI vision profile and a compatible
  local Ollama multimodal profile for the privacy path. Managed vision, if
  enabled, uses its own probed model, entitlement, reservation, and truthful
  receipt; it never silently changes the Founder Auto text route.
- [ ] Prove annotation-aware evidence with screenshots containing circles,
  arrows, and labels. The vision result must identify the marked region and
  OCR text without treating model inference as verified source truth.
- [x] Permit HTTP only for localhost and private-network endpoints; remote
  Personal AI requires HTTPS, protected headers cannot be overridden, and
  keys are redacted from logs and visible route receipts.
- [x] Show a native Personal/Local route receipt with profile, configured model,
  latency, and outside-managed-quota status without exposing credentials.
- [ ] Deploy the focused production Agent-tool gateway repair from PR `#41`
  (`2d293f85`). The patch admits validated `tools`/`tool_choice`, preserves
  tool-call history, and forwards both to DeepSeek. The exact three-file
  contract is integrated on the isolated Founder release branch and passes the
  current no-write typecheck plus the complete 252-test API suite; installed
  Agent execution remains blocked until the coordinated production deployment
  boundary.
- [ ] Prove one complete installed Agent loop after that deployment: model
  selects a tool, the IDE obtains local approval where required, the tool runs,
  the tool result returns to the same conversation, and the final answer plus
  Founder route receipt and `[DONE]` are visible.
- [ ] Run authenticated native tests for Auto, Fast, Reasoning, Code, BYOK,
  Ollama, streaming content, completion marker, cancellation, and quota errors.
- [ ] Make model identity truthful in the native answer and receipt: managed
  responses disclose the resolved DeepSeek route; personal/local routes show
  their configured profile without exposing a key.
- [ ] Make the composer model picker switch instantly among Founder Auto, Fast,
  Reasoning, Code, Ollama, and every named personal profile.
- [ ] Support OpenAI-compatible profiles through name, base URL, key, model,
  optional headers, connectivity test, edit, disable, and delete controls in
  the installed IDE; source and unit coverage are complete, installed visual
  and live-provider proof remain.
- [ ] Replace the unreliable browser speech dependency with a supported
  Founder voice boundary: capture audio locally, show recording/permission and
  input-device states, transcribe through an explicitly configured local or
  managed service, preserve typed text on failure, preview before send, allow
  cancel, and support keyboard-only operation.

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
- [x] Upgrade the shared AgentBus contract from prompt handoffs to a durable
  dependency graph with typed `dependsOn`, `supersedes`, `replyTo`, owned
  `scope` globs, artifact references, token/time budgets, attempt and stall
  counters, capability routing, and versioned evidence. Large handoffs live in
  bounded disk/database artifacts rather than chat messages.
- [x] Make AgentBus v2 event-sourced and idempotent, reject cycles and stale
  completions, route overlapping scopes through the existing lease/fencing
  boundary, and feed CI/review/test failures back to the owning task without
  creating another editing owner.
- [x] Prove replay, dependency release, supersession, overlap, stale fencing,
  bounded retry/stall escalation, independent-scope concurrency, and verified
  merge behavior across multiple chats.
- [x] Add one editable, versioned Goal Contract with objective, invariants,
  budgets, success evidence, status, and amendment history. Keep Goal, Task,
  Decision, and Evidence as separate concepts so progress remains legible.
- [x] Inject the bounded current Goal Contract into every Founder AI request as
  its North Star. Include the version, objective, pending decisions, and exact
  blocked task ids; require evidence against the request and parent goal; never
  let the model infer permission or silently expand or replace the goal.
- [x] Add a persistent "Needs your decision" inbox. Present two or three
  context-specific options, put one evidence-backed recommendation first,
  accept a custom answer, and block only dependent tasks while unrelated work
  continues.
- [x] Let a read-only Research task continue while a founder is away and attach
  its findings to the pending decision. Never let research, a timeout, or a
  default option authorize deletion, deployment, secret use, or an external
  write.
- [x] Make housekeeping an editable goal: incrementally identify generated,
  cached, duplicate, obsolete, stale-worktree, and archive candidates; show
  size, references, evidence, risk, and the recommended action; preview the
  exact selected change; checkpoint when restoration is possible; and require
  explicit founder approval before deletion.
- [x] Apply goal amendments at task/action boundaries. Show affected tasks,
  invalidated assumptions, salvageable work, budget/timeline impact, and
  accept/revise/reject/defer choices before incrementing the goal version.
- [x] Keep committed product intent small and reviewable. Store the Goal
  Contract and durable decisions with the project, keep leases/heartbeats and
  private runtime episodes in ignored local state, and generate trajectory
  views from the event log rather than maintaining noisy duplicate files.

Current Stage 6 boundary: the installed IDE has an editable Goal surface and a
persistent "Needs your decision" inbox synchronized through the authenticated,
paired Founder Node identity. Goal state is scoped by a path-free workspace
identity, stays usable offline, uploads offline decisions and research after
reconnection, and merges concurrent same-process writes instead of silently
dropping one device's decision. Executable AgentBus/build-queue work fails
closed on an exact pending decision while unrelated work continues. This V1
merge is serialized within one API process; a future multi-replica database
transaction/compare-and-swap remains an infrastructure hardening item.
Housekeeping now has a bounded, read-only, no-content scan for exact
reproducible-cache paths; it cannot delete, does not follow links, and
downgrades incomplete measurements to keep. Duplicate/source-obsolescence
analysis and stale-worktree review remain unchecked. The decision surface now
shows an exact live preview of checked paths and bytes, accepts revised founder
instructions without granting permission, and can wait for additional
read-only research while unrelated work continues. Restore checkpoints and the
separately authorized deletion executor remain unchecked.

Evidence: race tests, stale-lease tests, cross-chat overlap demonstration,
merge receipt, multi-agent task graph screenshots, concurrent decision-write
test, paired-node credential/no-token-in-URL test, offline-resolution sync
test, bounded North-Star prompt tests, API regression 288/288, utility
regression 95/95, and native-extension regression 206/206.

## Stage 7 - Founder-native Apple-inspired IDE and website UI

- [x] Replace icon-only primary navigation in source with
  a calm labelled hierarchy:
  New chat, Projects, Chats, Agents, Graph, Remote, Connect, and Settings.
- [ ] Prove that labelled hierarchy as the refreshed installed default layout
  at every release viewport, with no duplicate generic activity rail competing
  for primary navigation.
- [x] Give Projects and Chats first-class persistent navigation, searchable
  history, useful names, pin/archive controls, and a clear new-chat action.
- [x] Widen the primary navigation enough for readable labels and project/chat
  context; use compact icons only for familiar secondary editor tools.
- [ ] Keep coding tools available through progressive disclosure rather than
  removing essential editor capability.
- [x] Remove every visible Void name, action, settings title, and legacy logo
  from the installed routes covered by the release screenshot matrix.
- [x] Make Founder settings one coherent surface for account, plans and usage,
  Personal AI, local AI, connections, privacy, remote control, and companion.
- [x] Remove duplicate settings entry points and route the global settings icon
  to the coherent Founder surface; keep advanced editor settings progressively
  disclosed inside it.
- [x] Put provider selection, mode, attachments, microphone, and send in one
  stable composer without overlap.
- [x] Preserve and elevate the source-control graph as a Founder differentiator.
- [x] Use the left activity space for small, labelled Founder shortcuts.
- [x] Make Chat the primary founder workflow while retaining Explorer, Search,
  Source Control/Graph, Run, Extensions, and debugging capabilities under a
  clear Build and ship section.
- [ ] Apply the same Overview / Work and Decisions / Connect and Data hierarchy
  to IDE, Founder account, admin, Agent Hub, trading, and analyzer surfaces.
- [x] Use restrained radii, clear typography, consistent status colors,
  purposeful motion, reduced-motion support, and responsive layouts.
- [x] Remove obsolete website controls and match visible features to production
  capability.
- [x] Use a restrained multi-color status language: neutral surfaces, one clear
  action accent, green healthy/success, amber attention/verification, red only
  for destructive/error, and blue for active navigation.
- [ ] Add short purposeful transitions for state changes, loading, task travel,
  and completed work while honoring reduced motion and avoiding decorative
  animation in dense work surfaces.
- [ ] Audit empty, loading, partial, offline, unauthorized, quota-exhausted,
  error, success, and long-content states in every changed surface.
- [x] Migrate the source default shell to the Founder-native Apple-inspired
  layout: hide the legacy
  menu bar and generic activity rail by default, use a calm labelled Founder
  rail, reduce visual borders and competing controls, keep typography and
  spacing consistent, and expose advanced editor commands through Build,
  shortcuts, and the command palette.
- [ ] Prove the Apple-inspired shell in the refreshed installed application.
  Acceptance requires calm hierarchy, readable labels, deliberate spacing,
  restrained radii, no visible upstream identity, no control overlap, and
  predictable keyboard/focus behavior. A source theme or screenshot from an
  older payload is not acceptance.
- [ ] Keep Terminal, Explorer, Search, Graph/Source Control, Extensions, Run,
  and Debug as advanced capabilities rather than deleting them. AI automates
  the common path, while these tools remain the inspectable escape hatch for
  real repositories, local services, tests, and recovery.
- [x] Add Remote as a visible Founder navigation destination in IDE source.
- [ ] Align and prove Remote in the refreshed installed IDE and production web.
  Show this device, online state, workspace, last activity, pending approvals,
  rename, revoke, reconnect, and an explicit "Pair another device" action.
- [ ] Treat a human-readable pairing code only as a short-lived authorization
  handle. Keep the 256-bit device secret out of the renderer and URL, bind
  approval to the signed-in account and install identity, rate-limit guesses,
  expire and single-consume grants, require explicit device details before
  approval, and expose immediate revoke plus an audit receipt.
- [ ] Apply the same Founder shell, navigation names, state language, settings
  structure, and remote pairing flow to the website before calling the
  Apple-inspired redesign complete.

Evidence: full route/tab screenshot matrix at desktop and narrow widths,
zero Void-brand scans, keyboard/focus audit, no overlap or horizontal overflow.

## Stage 8 - Living Founder Dragon and proof receipts

- [x] Build one transparent, frameless, always-on-top companion owned by Founder
  IDE; remove duplicate Dragon/IDE launch identities.
- [x] Make it draggable across screens and remember its position per display.
- [x] States: resting in nest, listening, planning, flying/working, coordinating,
  blocked, verifying, delivered/fire, failed, offline, and update available.
- [x] Drive listening, task, network, coordination, verification, and usage
  states from real events rather than a cosmetic timer.
- [x] Connect updater availability and install/rollback transitions to the
  desktop companion without overwriting a more urgent active-task state.
- [x] Show compact task cards, measured savings, current agent count, and result
  receipts without exposing secrets.
- [x] Click opens the relevant task; context menu provides usage, hide/show,
  pause, settings, sign in/out, and reduced motion.
- [x] Keep transparent assets free of baked-in backgrounds and provide a quiet
  non-animated accessibility state.
- [x] Ensure the companion never steals typing focus or blocks essential UI.
- [x] Keep one recognisable dragon across every state, with transparent
  animation assets sized for a desktop companion rather than a video panel.
- [x] Support snap-to-edge, multi-monitor work areas, temporary click-through,
  hide until next task, and recovery when a saved display is disconnected.
- [x] Announce delivered work with a compact two-line result card and a brief
  fire state; resting returns the dragon to its nest without visual noise.
- [ ] Replace the current placeholder/copy-like visual treatment with one
  recognisable Founder Dragon designed for the desktop companion: transparent
  body-only frames, no rectangular video background, a convincing nest/rest
  pose, purposeful flight/effort, listening, verification, blocking, and
  delivery/fire reactions.
- [ ] Make the final companion feel alive without becoming distracting:
  breathing and eye motion at rest, task-linked motion only, compact result
  cards above it, drag anywhere on any monitor, edge snap, and a quiet
  reduced-motion fallback.
- [ ] Prove the final Dragon outside the IDE window, including drag, persistence,
  click-through, focus safety, task-state transitions, one-launch identity,
  and recovery after IDE restart. The earlier embedded panel and generated
  video preview are reference material, not release evidence.

Evidence: transparent pixel checks, multi-monitor drag test, restart persistence,
state-transition recording, reduced-motion and focus tests.

## Stage 9 - One application with embedded Founder Node

- [x] Bundle Founder Node with Founder IDE rather than shipping a second app.
- [x] Establish authenticated local IPC and fail-closed action contracts.
- [x] Complete website-to-Node-to-IDE remote actions for read, proposed edit,
  accept/reject, command, cancel, output, and status.
- [x] Connect IDE sign-in to the Doxxed account/X onboarding flow and register
  the device automatically after approval.
- [x] Add workspace boundaries, command risk classes, approvals, timeouts,
  idempotency, leases, and receipts.
- [ ] Prove remote control through the production website with a real installed
  device while preserving local control and revocation.
- [x] Keep one installer, one application identity, one tray/taskbar identity,
  one updater, and one uninstall entry; Founder Node runs as an embedded
  background capability with no second founder-facing app.
- [ ] Show device online/offline, current workspace, pending approval, last
  activity, revoke, rename, and reconnect states consistently in IDE and web.
- [ ] Add connected-service flows for GitHub, Vercel, Railway, Neon, email, and
  Telegram with least-privilege scopes, health, reconnect, and revoke controls.
- [x] Keep remote edits proposed until locally or remotely approved according
  to policy; high-risk commands always require explicit approval.

Evidence: 81 API relay/device-code tests, 41 Founder Node tests including the
chat/read/edit/command protocol boundary, extension IPC tests, paired-device
screenshots, replay/rejection tests, audit receipts, and a production remote
edit and command demonstration.

## Stage 10 - Website and founder business workflows

- [ ] Audit every Founder account and admin route; retain only controls backed
  by production behavior.
- [ ] Align plans, quota, provider settings, connected services, device status,
  projects, chats, agents, graph, and receipts with the IDE.
- [ ] Add proactive founder intelligence: decision archaeology, competitor
  watch, idea validation, repeated-pattern detection, and a daily brief with
  sources and opt-in controls.
- [x] Specify token launch, vesting, treasury, DCF Swap, liquidity, wallet
  rotation, emergency controls, acquisition offers, holder analytics, and
  settlement as audited business contracts before implementation.
- [x] Resolve and publish one token economics contract covering the bonding
  curve fee, any platform share, founder share, graduation threshold, DCF Swap
  fee destination, and every treasury action before deployment.
- [x] Model the proposed 50% founder/team allocation as continuous daily vesting
  over ten years, claimable only by the configured founder wallet, with public
  schedule, claimed, available, and remaining supply.
- [x] Model platform-received project tokens as a transparent locked treasury
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
- [x] Treat emergency asset or token migration as a governed recovery plan with
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

Evidence: capability audit, sourced intelligence examples,
`DCF-SWAP-FEES-CUSTODY-AND-AUTHORITY.md`, contract specs, security review,
legal/compliance gate, and end-to-end admin simulations. Specifications marked
complete above are design-complete; mainnet implementation remains blocked on
the explicitly listed legal, security, multisig, oracle, and contract-audit
gates.

## Stage 11 - Verify, package, install, and release

- [ ] Enforce verify-then-show: tests, typecheck, build, visual check, and
  evidence receipt before an agent declares completion.
  - [x] Enforce the source completion boundary in both extension chat and the
    installed native chat engine. Ask and Plan reject mutation; implementation
    requests require an observed owned edit; changed code requires a passing
    test/build/typecheck/lint command; user-facing UI additionally requires a
    passing visual/screenshot command. The application, not provider prose,
    appends the trusted receipt, and extension receipts persist in the local
    project activity ledger. Refreshed installed-app proof remains below.
- [x] Add a cheap independent self-critique pass against the original goal.
  Founder Second Brain can run manually or after four/eight completed tasks,
  uses a founder-selected Personal AI outside managed quota, receives a
  redacted read-only evidence snapshot including the durable Goal contract,
  and returns structured findings for Founder AI to reconcile without editing.
- [ ] Treat visual verify-then-show as a first-class gate for user-facing UI
  work. Capture the changed state, combine a redacted screenshot with the
  original goal, affected UI contract, diff summary, and deterministic
  DOM/console/network evidence, then ask a read-only independent vision
  reviewer for a structured verdict, defects, risks, and evidence references.
  - [x] Reject keyword-only and shell-composed visual claims in both native and
    extension completion boundaries. Only a successful dedicated Playwright,
    screenshot-QA, visual-audit, UI-QA, or end-to-end runner can satisfy the
    deterministic visual-command prerequisite.
- [ ] Run deterministic visual checks for every UI change and use local vision
  first when configured. A founder-selected Personal AI vision model may be
  used with consent; if no permitted vision route exists, never label the
  result visually verified and require an explicit human visual sign-off.
- [ ] Bound and redact visual review inputs and outputs, exclude secrets and
  unrelated windows, honor reduced motion, and record the reviewer route,
  latency, budget domain, verdict, and unresolved dissent without allowing the
  reviewer to edit files.
- [ ] Auto-repair failed QA; use scoped rollback only when the agent owns the
  entire change and can prove it will not remove founder work.
- [x] Run API, web, Founder Node, extension, overlay, installer, updater, IPC,
  coordination, quota, and security suites.
- [x] Visually inspect the installed primary workbench and settings routes at
  1440x900, 1280x720, 1187x739, and 768x900; capture screenshots and reject
  critical console errors, page errors, overflow, or visible Void branding.
- [ ] Complete the installed loading, empty, error, offline, focus, reduced
  motion, and success-state matrix after the final source freeze.
- [x] Build and locally verify one Founder IDE installer containing Founder
  Node. The refreshed unsigned internal-QA artifact is
  `Founder-IDE-Setup-0.9.4-internal-qa.exe`, 323,910,538 bytes, SHA-256
  `4AA6CFA39445E144BB60A66CDD51616686F743DD3DB4E3AFCE04710E5D063BF9`.
  Its installer log records inner exit code 0, navigation/workbench corrections,
  shortcut creation, release identity 0.9.4, and normal completion.
- [ ] Rebuild the installer after the final owned changes, then regenerate and
  validate the canonical release receipt against that exact artifact.
  The receipt must include the preservation preflight, actual files and
  architecture nodes changed, public APIs or components removed, rewrite-budget
  variance, behavior-preservation checks, and the reason for every core
  workbench change that could not be delivered extension-first.
  - [x] The internal-QA refresh contains extension 0.5.1 and the final
    product-bearing Founder navigation changes. Installed Deploy, Remote,
    Connect, attachment, settings, managed Fast chat, and terminal execution
    passed. Signing, clean-VM lifecycle, and the canonical production receipt
    remain open.
- [x] Add an explicit fast-compression installer-QA profile so install,
  shortcut, upgrade, and uninstall checks do not pay release-compression cost.
  Keep `lzma2/ultra64` and deterministic release settings unchanged for the
  final signed artifact, and label every fast-compression package internal QA.
- [x] Reject a warm-build payload when its compiled Founder action toolbar is
  older than the reviewed React source; the installer and CI now fail instead
  of silently shipping stale UI.
- [x] Remove old installed Founder IDE/Node/Stack variants only after inventory
  and explicit preservation of user data.
- [x] Install the local candidate, create one desktop shortcut, pin the single
  Founder IDE identity to the taskbar, verify restart and uninstall inventory,
  and preserve FounderVault and the Founder IDE profile.
- [ ] Prove install, update, rollback, and uninstall for the refreshed candidate
  on a separate clean Windows VM.
- [x] Publish only when signing and clean-machine gates pass. Otherwise label
  the artifact internal testing and report the exact blocker.
- [x] Add an opt-in daily self-QA run that checks the last 24 hours of owned
  changes, builds/tests affected modules, probes configured links and services,
  captures changed UI states, records failures, and proposes bounded repairs.
- [x] Require self-QA to respect ownership and active coordination leases so it
  cannot undo a founder's or another agent's concurrent work.
- [x] Verify desktop shortcut, taskbar pin, app identity, installed version,
  embedded Node lifecycle, managed normal chat, personal AI settings,
  microphone affordance, navigation, and Graph from the installed application.
- [ ] Prove live sign-in, a personal-provider completion, speech capture, active
  coordination, Dragon state transitions, website remote control, update,
  rollback, and final uninstall from the refreshed installed application.
- [x] Add a fail-closed, testable release-evidence verifier that checks the
  installer bytes, SHA-256, Authenticode status, installed chat nonce and route,
  required viewports, settings, screenshots, and release-note references.
  Schema v2 also requires one annotated input, picker/paste/drop/preview/remove
  proof, bounded and redacted review evidence, visible results in Ask, Plan,
  Build, Debug, and Team, plus either an identified AI reviewer with a stable
  result hash or explicit timestamped human sign-off. Historical schema v1 is
  accepted only for the unsigned 0.9.4 internal-test artifact.

## Immediate completion sequence from this checkpoint

1. **Freeze the product contract.** Keep one Founder AI, five work modes,
   Personal AI/Ollama profiles, one Second brain workflow, one embedded Node,
   one Dragon, one labelled navigation hierarchy, and one evidence standard.
   Reject duplicate settings, launch identities, generic prompt-chip clutter,
   and public claims unsupported by measured receipts.
2. **Finish the core AI and input loop.** Prove managed Auto/Fast on Flash and
   explicit Code/Reasoning on Pro; keep any premium expert lane feature-flagged
   until its probe and benchmark pass; finish installed voice, the bounded
   screenshot-to-text bridge in all five modes, and the packaged terminal; keep
   model, quota, latency, cache, vision, and error receipts truthful.
3. **Finish local context efficiency.** Measure current provider egress through
   typed, CI-enforced call-site IDs rather than stale static percentages. Ship
   the minimal graph-first Code Intelligence service in Founder Node with
   incremental hashes, symbols, imports, reverse dependencies, a compact repo
   map, bounded evidence tuples, and representative retrieval benchmarks.
4. **Finish reliable work orchestration.** Prove Ask, Plan, Build, Debug, and
   Team; upgrade AgentBus to durable dependency, supersession, reply, scope,
   artifact, budget, and stall contracts; require ownership leases, verified
   memory, one editing owner, and 10-15 minute cross-chat awareness. Team uses
   useful specialists, never an arbitrary swarm count.
5. **Finish independent review.** Complete the bounded reviewer context pack,
   structured Second brain result, evidence-linked reconciliation, dissent
   receipt, and approval gate. Add visual verify-then-show for user-facing
   changes, using deterministic checks plus an allowed local or Personal AI
   vision reviewer; without one, require explicit human visual sign-off.
6. **Finish the Founder experience.** Ship the installed Apple-inspired shell,
   labelled New chat/Projects/Chats/Agents/Graph/Remote/Connect/Settings
   navigation, progressive disclosure for expert coding tools, persistent
   projects/chats, Graph as a first-class view, and the accepted living
   desktop Dragon.
7. **Finish secure Remote and website parity.** Prove short-lived pairing,
   account/install binding, 256-bit device secret protection, explicit device
   approval, rate limits, expiry, single consumption, revoke, rename,
   reconnect, pending approvals, and a real web-to-installed-IDE action. Apply
   the same plans, usage, AI, projects, chats, agents, graph, services,
   receipts, settings, and visual language to the website.
8. **Freeze, package, install, and prove.** Rebuild the exact payload and
   one-app installer, preserve Founder data, remove stale app identities,
   install, and execute the full functional, visual, failure-state, security,
   accessibility, update/rollback/uninstall, and clean-machine matrix. Public
   release remains held until signing, clean-VM, and soak gates pass.

## Focused questions for an independent GLM review

Ask GLM to challenge implementation contracts rather than re-decide the
product. Request one structured answer covering:

1. What is the smallest complete JSON schema for a read-only Second brain
   request and result, including evidence references, dissent, confidence,
   risks, competition analysis, and proposed corrections?
2. What deterministic arbitration rules should Founder AI use when the primary
   answer and reviewer disagree, without letting either model silently edit?
3. Which tests prove Ask and Plan cannot mutate files or run risky tools, while
   Build, Debug, and Team preserve exactly one editing owner?
4. Which prompt-prefix, retrieval, cache, and tool-result boundaries maximize
   DeepSeek cache reuse without serving stale repository or remote-state data?
5. Which threat cases remain in the Remote pairing design despite short-lived
   codes, account/install binding, a 256-bit secret, throttling, expiry,
   single consumption, explicit approval, revoke, and audit?

Require GLM to return: `verdict`, `verified_gaps`, `evidence`, `better_option`,
`required_tests`, `residual_risks`, and `confidence`. Reject generic planning
or acknowledgement as an incomplete review.

Evidence: signed artifact/hash, clean-machine matrix, installed-app screenshots,
test report, visual audit bundle, release receipt, and blocker register.

## Current release checkpoint

- 450 scoped automated checks pass across the IDE extension, Founder Node,
  vault, updater, installer, IPC, coordination, quota, API, and website layers.
- API and website typechecks pass. The production-style website build completed
  route generation after the quota-policy and admin-language repair.
- The installed one-app candidate passed screenshots at 1440x900, 1280x720,
  1187x739, and 768x900 with no critical page errors in the tested routes.
- The final narrow settings pass keeps every section label readable, and the
  installed Founder Settings bundle exactly matches the reviewed source bundle.
- Desktop, Start, taskbar, uninstall, application identity, embedded relay,
  version marker, preserved FounderVault, preserved profile, and managed normal
  chat are proven on the local Windows machine.
- Prior installed artifact `Founder-IDE-Setup-0.9.4.exe` is 214,115,394 bytes with
  SHA-256
  `F3E829D43B144DDD98FB5B5FF29AD9AF36AD9238C4F5273C2983F28CEE372A30`.
  It is intentionally unsigned and restricted to internal testing.
- A refreshed source candidate containing the ownership-aware daily review,
  Plan/Team account repair, hardened web visual gate, and release-evidence
  verifier was built on 2026-07-23. It is not the final candidate: the
  founder's subsequent installed-app review reopened voice, Second brain,
  default shell, terminal runtime, and secure Remote acceptance gates before
  another install/release receipt is allowed.
- `V1-CORRECTED-20260723-1848` produced a visible DeepSeek response and route
  receipt from the final installed artifact. It also proved the remaining
  production policy defect: Founder Auto resolved to Pro, so the coordinated
  Auto-to-Flash deployment remains open.
- Draft PR `#42` contains this release branch and its Vercel preview checks pass.
- API PR `#41` is tested but deliberately held until the trading owner proves a
  simultaneous full-flat boundary. Installed Agent-tool QA therefore remains
  open rather than being reported as complete.
- Trusted signing, a separate clean Windows VM, production remote-control
  proof, live connected-service proofs, and the soak period are external or
  manual gates and remain open.
