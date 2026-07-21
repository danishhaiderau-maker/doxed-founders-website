# Founder Multi-Agent Coordination Specification

**Status:** authoritative product direction; implementation is not yet complete
**Purpose:** let many agents work quickly without duplicate effort, conflicting architecture, hidden races, or misleading completion claims.

## 1. Product decision

Founder will not sell an arbitrary agent count as the feature. The product promise is:

> Founder assigns the smallest useful team, gives every worker an isolated workspace and explicit ownership, detects overlap before edits, coordinates dependent work automatically, and merges only verified results.

The runtime is model-neutral. GPT, Claude, Kimi, GLM, DeepSeek, Gemini, OpenRouter models, and local models are interchangeable execution resources behind one Founder agent contract. No provider-specific prompt format, tool schema, memory format, or task state may become the product's source of truth.

User-facing execution choices remain simple:

| Mode | Behavior | Best for |
| --- | --- | --- |
| **Focus** | One primary agent, with read-only research help when useful | Questions, small fixes, precise edits |
| **Team** | Two to six bounded specialists with automatic coordination | Features crossing UI, API, data, tests, or docs |
| **Scale** | Adaptive parallelism up to the plan, provider, cost, and independence limits | Large migrations, audits, documentation, multi-repository work |

`Scale` does not promise a fixed number. Forty workers are harmful when only four independent ownership boundaries exist.

## 2. Why the Spettro claim can be technically possible

The expensive model inference can run on a provider's servers while the desktop performs asynchronous HTTP streaming, event handling, and file writes. That can keep local CPU use low. A Go, Rust, Node.js, or similar async runtime can hold many in-flight requests without creating one heavy operating-system process per request.

Spettro's public repository documents a real configurable multi-agent runtime, parallel sub-agent calls, isolated roles, and an Ultra mode. Its public material does not independently prove the exact social-post claim of forty agents, one hundred requests per second, and unchanged memory use. Kimi's official API documentation says accounts have concurrency, request, and token limits and recommends queues, semaphores, backoff, and starting with low concurrency.

Founder should adopt the sound architecture and reject the vanity metric.

## 3. Shared Work Ledger

Every active task publishes a durable intent card through the embedded Founder Node:

```text
task_id
objective
owner_agent
parent_task
status
workspace_id
worktree_or_branch
claimed_paths
claimed_contracts
dependencies
permission_scope
provider_and_budget
heartbeat
changed_files
verification_receipts
```

The ledger is local-first. Remote Founder surfaces receive only the minimum state allowed by the workspace privacy mode.

Every entry point joins the same workspace ledger. Five open chat tabs, a website-started task, a phone approval, and a Telegram request must all discover one another's active goals and ownership. Each chat may remain a separate goal universe with its own context, model, files, and history. It must still know what else is active in the workspace so it cannot unknowingly overwrite another goal's work.

Awareness is event-driven and reconciled periodically. A worker checks the ledger when it starts, before it expands scope, before every write/merge/deploy boundary, whenever another task changes ownership, and through a quiet 10-15 minute heartbeat while work remains active. Unrelated scopes continue without interruption. Nearby scopes exchange intent summaries. Direct overlap blocks the newer write until ownership is agreed.

## 4. Automatic overlap protocol

Before a worker can edit, it must publish its intent and requested ownership. Founder checks four kinds of overlap:

1. **Path overlap:** the same file, directory, generated output, migration, or lockfile.
2. **Contract overlap:** the same API route, event, schema, model alias, setting, command, or user journey even when files differ.
3. **Runtime overlap:** the same process, port, deployment, database, wallet, queue, or external account.
4. **Outcome overlap:** two workers solving substantially the same requirement with competing designs.

When overlap is found, the coordinator automatically opens a short negotiation:

- compare intent summaries and current evidence;
- choose one contract owner;
- split work into non-overlapping scopes or make one worker wait;
- record the agreed interface, test, and merge order;
- notify the founder only when a product or authority decision is genuinely required.

Agents may read shared work. A write lease is exclusive, scoped, and expires when heartbeats stop. A worker cannot silently expand its edit scope.

## 5. Isolation and merge discipline

- Each writing worker receives its own Git worktree and branch.
- Read-only research workers may share a checkout because they cannot mutate it.
- Generated files and shared manifests have an explicit owner.
- Cross-worktree dependencies use commits and typed handoff artifacts, not copied chat text.
- A dependency graph determines merge order.
- The merge queue reruns contract tests after every integration.
- Stale worker completions are rejected when their base contract or fencing token changed.
- No worker may force-push, deploy, rotate credentials, change authority, or execute a money path unless the task's permission receipt permits it.

## 6. Concurrency governor

Founder continuously chooses the useful parallelism level using:

- number of genuinely independent tasks;
- provider concurrency, RPM, TPM, and daily limits;
- user plan and personal-provider allowance;
- token and monetary budget;
- local CPU, memory, disk, and process pressure;
- merge-conflict risk;
- security and approval boundaries;
- request latency and rate-limit feedback.

The governor queues work, applies jittered exponential backoff, reuses cached context, and reduces parallelism after provider or machine pressure. The UI shows **active**, **waiting on dependency**, **rate limited**, **needs approval**, and **ready to merge** as different states.

## 7. Founder IDE experience

The Work desk should show a simple coordination map:

- objective at the top;
- active agents as named work items, not theatrical personalities;
- ownership pills for UI, API, data, tests, docs, deploy, and review;
- dependency lines and blocked reasons;
- live file being read or edited;
- cost, elapsed time, model route, and permission scope;
- direct actions: inspect, message, pause, cancel, compare, merge;
- conflict banner with the agreed resolution;
- one final completion receipt.

The chat header should use understandable product language rather than unexplained workbench icons. Show **New chat** as a labelled primary command and keep **Projects**, **Agents**, **Connections**, and **History** close at hand. A compact window may collapse secondary labels into familiar icons with tooltips, but the first-use state must teach the meaning without requiring the user to guess what a bare plus opens.

The Founder Dragon reflects the coordinator's real aggregate state. It flies only while work is active, asks for attention only when approval is needed, breathes fire only after the completion gate passes, and shows smoke on a blocked or failed run.

## 8. Completion gate

An agent saying "done" is never sufficient. The coordinator requires the receipts applicable to the task:

- requested behavior mapped to changed files;
- focused tests and broader contract tests;
- typecheck, lint, build, and CI where relevant;
- rendered UI at required viewports;
- console and network inspection;
- installed-application or deployed-route smoke;
- security and secret scan;
- exact commit, artifact, deployment, and rollback target;
- remaining gaps labelled verified, implemented-not-verified, designed-only, or externally blocked.

The coordinator performs an independent review instead of letting the worker grade its own result.

## 9. High-value features to keep or add

1. Live edits beside chat with hunk-level review and checkpoints.
2. Focus, Team, and Scale execution with the automatic overlap protocol.
3. One Founder Settings surface for identity, AI, privacy, infrastructure, and connections.
4. Founder-managed AI plus multiple remembered personal providers and local models.
5. Local, Hybrid, and Cloud boundaries enforced below the UI with per-request receipts.
6. Website/mobile/Telegram remote control through the embedded Node with a local kill switch.
7. GitHub, Vercel, Railway, Neon, email, calendar, messaging, and MCP connections through one connection center.
8. Persistent project memory, decisions, rules, skills, and architecture contracts that the user can inspect and delete.
9. Automatic visual self-QA, link/route checks, console/network checks, and a quiet daily review.
10. Deployment preview, health verification, rollback, and evidence attached to the task.
11. Founder-specific build-to-launch readiness, project intelligence, and acquisition workflows after legal and security gates.

## 10. What to hide or remove from the default experience

Do not remove the useful VS Code engine. Simplify its default presentation:

- remove all visible Void branding, onboarding, settings, and duplicate commands;
- remove the separate desktop Founder Node product from the ordinary user journey;
- hide duplicate chat and account surfaces;
- collapse status-bar noise into one Founder status control;
- keep Explorer, Search, Source Control, Run, Terminal, Problems, Diff, and Command Palette;
- move Extensions, Ports, Outline, Timeline, raw provider catalogues, and rare workbench preferences into Connect or Advanced unless context makes them relevant;
- reveal debugging and testing prominently only when the workspace supports them;
- do not delete upstream internals merely to rename private identifiers, because that makes updates fragile without improving the product.

## 11. Five delivery stages

### Stage 1: Stabilize the current installed product

Fix all four Founder model aliases, bridge native chat lifecycle events to the Dragon and task ledger, finish secure personal-provider selection, remove remaining visible Void strings, and pass the installed Windows end-to-end audit.

### Stage 2: Coordination engine

Build the Shared Work Ledger, worktree allocator, ownership leases, semantic overlap detector, peer negotiation, dependency graph, concurrency governor, merge queue, and independent completion gate.

### Stage 3: Live Build and remote work

Unify plan, streaming actions, code edits, diff review, terminals, tests, checkpoints, project memory, website/mobile remote sessions, approvals, and offline behavior.

### Stage 4: Connect and Ship

Complete the connection center, provider vault, GitHub/CI/deployment/database flows, health proofs, rollback, communication integrations, automations, and daily quality review.

### Stage 5: Founder platform and release trust

Complete the calm Founder-native shell, signed installer/updater, clean upgrade/uninstall, accessibility/performance/security audits, then add the legally approved DCF launch, vesting, project intelligence, and acquisition-settlement workflows defined in the product and tokenomics specifications.

## 12. Acceptance scenario

The coordination feature is complete when a founder requests one change spanning UI, API, schema, and tests; Founder chooses Team mode, creates isolated worktrees, detects an intentional API/schema overlap, records one agreed owner and interface without being prompted, shows live progress, merges in dependency order, runs independent visual and automated verification, and produces one truthful completion receipt. No duplicate implementation, contaminated branch, hidden conflict, or unexplained provider charge remains.

## 13. Research references

- Spettro product and workflow: https://spettro.app/
- Spettro public repository and Ultra-mode documentation index: https://github.com/Aploide/spettro
- Kimi API rate limits: https://www.kimi.com/help/kimi-api/api-rate-limits
- Kimi API concepts and concurrency accounting: https://platform.kimi.ai/docs/introduction
- Cursor multitask, worktrees, and multi-root workspaces: https://cursor.com/changelog/04-24-26
- Cursor cloud-agent isolation and computer-use verification: https://cursor.com/blog/agent-computer-use
