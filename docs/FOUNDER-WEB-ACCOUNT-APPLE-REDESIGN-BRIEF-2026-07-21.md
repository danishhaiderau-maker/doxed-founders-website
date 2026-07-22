# Founder Web, Account, and Admin Redesign Brief

| Field | Value |
|---|---|
| Status | Product and implementation source of truth for the next web redesign |
| Updated | 2026-07-21 |
| Scope | Website navigation, Founder account, Founder Brain, admin control, responsive UI, and parity with Founder IDE |

## 1. Product outcome

Founder OS should feel like one calm product across the website, Founder IDE, and phone remote. A founder should understand the system without learning the internal split between Founder Den, Founder Stack, Founder Node, the AI Gateway, provider adapters, or operational dashboards.

The redesign is not a cosmetic reskin. It has four jobs:

1. Reduce navigation to a few stable user intents.
2. Merge duplicate settings and admin controls around one domain model.
3. Make Founder Brain provider-neutral for normal users while keeping exact routing observable to admins.
4. Preserve the working editor, account, trading, trust, deployment, and AI engines underneath the simpler shell.

## 2. Evidence and limits of this audit

This brief is based on:

- the current web source for SiteNav, AgentHubShell, Founder Den, AccountHub, Builder Settings, Admin Control, AI Keys, Founder Brain Providers, and AI Routing;
- the installed Founder IDE and embedded Founder Node behavior;
- authenticated production gateway tests through the locally paired Founder Node;
- the live public website and unauthenticated account handoff.

The browser session available to this audit was not signed in to the private admin page. Therefore no secret value or private account value was read. Admin structure was audited from its shipping source and production behavior. A signed-in visual route sweep remains a release gate before changing the admin UI.

## 3. Current truth

### Founder Brain production state

The production streaming and alias path is healthy after commits `d004cf3d` and `49bf5b5e`. On 2026-07-21, unique-nonce tests through the paired Founder Node returned HTTP 200, Founder metadata, non-empty content, and terminal `[DONE]` for all aliases:

| Alias | Current provider/model | Tier | Result |
|---|---|---|---|
| `founder-os-auto` | DeepSeek / `deepseek-v4-pro` | reasoning | Verified |
| `founder-os-fast` | DeepSeek / `deepseek-v4-flash` | fast | Verified |
| `founder-os-reasoning` | DeepSeek / `deepseek-v4-pro` | reasoning | Verified |
| `founder-os-code` | DeepSeek / `deepseek-v4-pro` | code | Verified |

Kimi, legacy GLM, and local Playwright capability rows are inactive for this production chat path. This is deliberate fail-closed behavior, not the final multi-provider design.

GLM 5.2 remains part of the intended Founder Brain. It must not become eligible merely because a masked key row exists. Activation requires a live provider test that proves authentication, supported model ID, non-empty completion, latency, and safe failure behavior. Until then, DeepSeek remains the truthful default.

### Current navigation debt

The product currently exposes several competing shells:

- SiteNav has Trade & Agents, Community, and Build dropdowns with many child routes.
- AgentHubShell adds a second persistent twelve-link sidebar.
- Founder Den has its own staged workspace navigation.
- Account has eight peer tabs.
- Admin Control has six peer sections plus links to additional admin pages.
- Founder Stack, Founder Node, Founder IDE, Founder OS, and Founder Den are presented as if they are separate products.

The capabilities are valuable. The simultaneous navigation models are not.

### Current AI administration debt

Four overlapping concepts exist today:

1. Platform Brain DeepSeek fallback.
2. Founder promo keys for GLM, Gemini, and DeepSeek.
3. Founder Brain Providers with DeepSeek and GLM model fields.
4. Generic provider and section routing.

They have different labels, storage paths, billing sources, and eligibility rules. The redesign must not hide those differences from operators, but it must present them inside one Founder Brain control center with clear scopes.

There is also a copy conflict: some pages describe a one-month promo while sign-in advertises three months. Promo duration and allowance must come from one entitlement contract, never repeated hard-coded copy.

## 4. Final information architecture

### Global website shell

Only three product intents remain in the permanent navigation:

| Destination | Purpose | Contains |
|---|---|---|
| **Build** | Create and operate | Workspace, chats, agents, Ship, remote work, connections, launch readiness |
| **Discover** | Evaluate people and projects | Projects, founders, agents, feed, trust, scout evidence, acquisition interest |
| **Trade** | Markets and positions | DCF Swap, portfolio, watchlist, predictions, graduated tokens, Raise Room market activity |

The right side of the global bar contains Search, Founder Chat, Notifications, and the Account menu. DDollar is shown as a balance inside Account and relevant transaction views, not as permanent navigation.

On mobile, the same three intents use a bottom or compact top navigation. Do not introduce a different mobile taxonomy.

### Route disposition

| Current surface | Decision | Final home |
|---|---|---|
| Founder OS + Founder Den | Merge the user concept | **Build** workspace; retain redirects for old links |
| Founder Stack | Remove as a product name | **Founder IDE** download or **Connect this device** |
| Standalone Founder Node | Hide from ordinary flow | Embedded in Founder IDE; standalone only under Advanced |
| Agent Hub | Keep capability, simplify shell | **Discover > Agents** |
| Scout Voting | Keep | **Discover > Trust Center** |
| Prediction Markets | Keep | **Trade > Predictions** |
| Feed | Keep, make contextual | **Discover > Updates** and project activity |
| DDollar page | Keep ledger capability | Account balance and relevant receipts |
| Reputation | Keep | Account trust summary and public profile |
| Builder Rewards | Keep when eligible | Account/Plan and contextual project reward state |
| Raise Room | Keep | Project page before graduation; market activity in Trade |
| Downloads | Keep | Account > Devices and public download route |
| Provider connections | Keep and expand | **Connect** inside Build and Account > Security & Connections |
| Admin Control | Keep, reorganize | Private **Admin** workspace with four domains |

Legacy URLs should redirect to the canonical destination until analytics show that old clients no longer use them. Do not delete working APIs while simplifying the UI.

## 5. Founder account redesign

The current eight peer tabs become four understandable sections:

| Final section | Merges | Required content |
|---|---|---|
| **Profile** | Overview + Reputation | X identity, public founder profile, trust level, projects, acquisition contact preference |
| **Security & Connections** | Security + Connected Accounts | Passkeys/2FA, recovery, wallets, devices, Founder Node, AI/code/deploy/data/social connections, scopes, revoke |
| **Plan & Usage** | Top up + AI usage + rewards | Plan, Founder-managed allowance, BYOK slots, DDollar, invoices/top-ups, rate limits, current usage |
| **Inbox & History** | Messages + Notifications + Activity | Messages, approvals, notification preferences, security events, action receipts, recent work |

### Keep

- X sign-in because verified public identity is required for listings and public proof flows.
- Email/Google sign-in for ordinary access, trading, and voting.
- Wallet ownership proof, but never private-key custody.
- Device and Founder Node health.
- Exact connection scopes, last use, health, and revoke.
- Admin entry only for admin accounts.

### Merge or move

- Top up moves into Plan & Usage.
- Connected Accounts moves into Security & Connections.
- Notification settings live inside Inbox & History.
- Reputation is summarized on Profile; its detailed history is contextual.
- Downloads and paired devices live together.

### Remove from the default view

- Separate pages for concepts that are only one setting row.
- Repeated setup explanations after onboarding is complete.
- Raw provider keys, model IDs, infrastructure hostnames, and operational billing-source names.
- Dead actions for users who are not eligible.

## 6. Founder Brain product contract

### User-facing modes

Users select intent, not vendor model strings:

| Mode | Meaning |
|---|---|
| **Auto** | Founder classifies the task and chooses the healthiest eligible route |
| **Fast** | Short answers, summaries, and social drafts |
| **Build** | Implementation, debugging, edits, and tool use |
| **Deep** | Architecture, research, strategy, and deliberate reasoning |

Founder Auto remains selected by default. Exact provider, model, tier, latency, cost, and fallback are visible in a request receipt and Admin, not in the everyday composer.

### Intended managed routing

| Intent | Primary when healthy | Fallback when healthy |
|---|---|---|
| Fast/Q&A/social | DeepSeek Flash | GLM Flash |
| Build/code | DeepSeek Pro | GLM 5.2 |
| Deep/reasoning | GLM 5.2 | DeepSeek Pro |
| Unknown/Auto | Classifier decision | Health- and budget-aware alternate |

This table is policy, not a hard-coded promise. Provider eligibility requires a passing credential/model test, current health, account entitlement, budget, and privacy mode. A failed provider is removed by the circuit breaker until recovery is proven.

### Personal and local routes

- Founder Managed is the default.
- Personal providers are remembered, can coexist, and can be enabled per workspace.
- Local means local: no prompt, code, attachment, embedding, or derived context may leave the device.
- Hybrid uses only the services explicitly permitted for that workspace.
- A personal key never silently becomes a platform key or another founder's fallback.

### Admin Founder Brain workspace

Replace the four overlapping AI control surfaces with five progressive sections:

1. **Health** - route health, active incidents, last successful request, latency, and fallback state.
2. **Routing** - intent policy and eligible primary/fallback lanes.
3. **Credentials** - platform, promo, and operational keys grouped by scope; masked status only.
4. **Budgets** - plan allowances, promo window, platform caps, concurrency, and provider limits.
5. **Tests & receipts** - safe provider probes, four-alias smoke, recent failures, cache outcome, and change history.

Advanced fields such as API base URL and exact model ID remain editable only in Credentials/Advanced. Saving a model field does not activate it; the test must pass first. Routing changes should be staged, simulated, and committed with an audit receipt.

## 7. Capacity for 10 to 100 founders

Having API keys is not enough to guarantee capacity. Founder Brain needs a tenant-aware concurrency governor.

### Required controls

- Per-founder and per-plan concurrent request limits.
- Provider RPM, TPM, daily spend, and burst budgets.
- Weighted queues so one large task cannot starve interactive chat.
- Separate lanes for interactive chat, background agents, embeddings, and scheduled review.
- Circuit breakers and exponential backoff with jitter.
- Cache keys scoped by tenant, workspace, intent, requirements, model policy, and privacy mode.
- No cross-founder cache reuse for private prompts or code.
- Idempotency and cancellation so retries do not duplicate tool actions or paid requests.
- Usage receipts with model, tier, cache result, latency, tokens, estimated cost, and completion state.

### Cache order

1. Deterministic local tools and indexed workspace facts.
2. Exact safe response cache where policy permits.
3. Workspace retrieval and reusable context summaries.
4. Provider-native prefix caching for stable system/context blocks.
5. Fresh provider inference.

### Acceptance target

Before claiming support for 100 founders, run a staged load test with at least 100 isolated tenants and mixed Fast/Build/Deep traffic. Prove p95 time-to-first-token, completion rate, queue time, cost per completed task, no tenant leakage, bounded retries, and predictable degradation when one provider is unavailable.

## 8. Admin redesign

The six current Admin Control tabs and scattered admin pages become four domains:

| Admin domain | Contains |
|---|---|
| **Operations** | Platform health, deployments, incidents, Founder Node fleet, daily review, demo/test tools |
| **AI & Usage** | Founder Brain health/routing/credentials/budgets/tests, builder usage, promo entitlements |
| **Treasury & Launch** | Wallet authorities, treasury, launch policy, fees, vesting, settlement, proposal receipts |
| **Trust & Safety** | Listings, moderation, investigations, agent registrations, acquisition verification |

Research bot internals belong in Operations > Agents, not as a peer of platform-wide AI settings. Social footer copy belongs under Communications or Brand settings. Builder breakdown belongs under AI & Usage analytics. Demo controls remain Advanced and clearly synthetic.

No key-management page should be mixed with bot trading limits, treasury operations, social copy, and moderation in one scrolling surface.

## 9. Visual and interaction language

"Apple-like" means calm hierarchy and thoughtful behavior, not copying Apple assets.

- Use system/Inter typography with letter spacing `0` and a small, stable type scale.
- Use an 8-point spacing system and card radius no greater than 8px unless an established component requires it.
- Use cards only for repeated objects or framed tools; page sections remain unframed bands.
- Use one primary action per state. Secondary actions move into a menu or contextual sheet.
- Use icons with labels on first-use and in wide layouts; compact mode may use icons with tooltips.
- Status colors are semantic: green healthy/completed, blue active, amber attention/waiting, red blocked/destructive, neutral idle.
- Avoid a one-color purple/slate product. Build, Discover, Trade, trust, and system status may have restrained semantic accents over neutral surfaces.
- Motion is 160-220ms for navigation/state transitions and longer only when it explains progress. Respect reduced-motion settings.
- Empty states give one next action, not product marketing.
- Errors state what failed, what remains safe, and the next action. Never render raw HTML/provider payloads to users.
- Desktop, tablet, mobile, and Founder IDE share labels, icons, status terms, and connection groups.

## 10. Five implementation stages

### Stage 1 - Stabilize and measure

- Keep current DeepSeek production aliases fail-closed and healthy.
- Validate GLM credentials/model IDs without exposing or logging keys.
- Complete native Founder IDE response proof and crash/focus audit.
- Instrument route, cache, budget, and completion receipts.
- Remove contradictory promo-duration copy by reading one entitlement source.

### Stage 2 - Shared shell

- Introduce shared design tokens and one responsive website shell.
- Replace SiteNav rows and AgentHubShell navigation with Build/Discover/Trade.
- Preserve old routes with redirects.
- Align website labels with Founder IDE Founder/Work/Connect concepts.

### Stage 3 - Account, Connect, and Admin

- Merge account tabs into four sections.
- Build one connection center used by website and IDE.
- Merge AI admin controls into Founder Brain Health/Routing/Credentials/Budgets/Tests.
- Reorganize the remaining admin pages into four domains.

### Stage 4 - Capacity and coordinated work

- Add tenant-aware budgets, queues, circuit breakers, cache isolation, and load tests.
- Implement the Shared Work Ledger, agent ownership, overlap detection, worktree isolation, and completion gate.
- Show current agents and nearby work without turning the UI into a monitoring dashboard.

### Stage 5 - Full visual overhaul and release proof

- Apply the shared visual system to Build, Discover, Trade, Account, Connect, Admin, and Founder IDE.
- Verify signed-in and signed-out journeys at desktop, tablet, and mobile widths.
- Capture screenshots, console/network errors, route health, accessibility, and interaction receipts.
- Ship only after clean-machine install, website deep-link, provider, rollback, and daily-review tests pass.

## 11. Release acceptance

The redesign is complete only when:

- a new founder can sign in, install Founder IDE, connect the device, choose privacy, and chat without learning internal product names;
- the same project, tasks, agents, connections, and receipts are recognizable on web and desktop;
- all normal navigation fits Build, Discover, Trade, Search, Chat, Notifications, and Account;
- each account and admin setting has one canonical home;
- Founder Auto can prove its provider/model decision without exposing it as everyday complexity;
- invalid GLM or DeepSeek credentials fail closed and cannot poison another route;
- 100-tenant load testing proves isolation and controlled degradation;
- no visible Void, Founder Stack, duplicate Node, raw provider error, or contradictory promo copy remains;
- the signed-in route sweep and installed-app visual audit have evidence, not only source-level confidence.

## 12. Related sources of truth

- [`FOUNDER-PRODUCT-DIRECTION-2026-07.md`](./FOUNDER-PRODUCT-DIRECTION-2026-07.md)
- [`FOUNDER-END-TO-END-UX-AUDIT-2026-07-21.md`](./FOUNDER-END-TO-END-UX-AUDIT-2026-07-21.md)
- [`FOUNDER-MULTI-AGENT-COORDINATION-SPEC-2026-07-21.md`](./FOUNDER-MULTI-AGENT-COORDINATION-SPEC-2026-07-21.md)
- [`FOUNDER-BRAIN-AI-OS-SPEC.md`](./FOUNDER-BRAIN-AI-OS-SPEC.md)
