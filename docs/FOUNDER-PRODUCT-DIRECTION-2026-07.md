# Founder Product Direction — IDE, Providers, Website, and DEX

| Field | Value |
|---|---|
| Status | Working direction — founder confirmation required where marked **Proposal** |
| Updated | 2026-07-21 |
| Scope | Founder IDE, Founder Node, AI providers, commercial plans, website information architecture, token launch, DEX |
| Supersedes | The “no subscription / no BYOK” assumptions in `BILLING.md` and `PRODUCT.md` |

## 1. Product promise

Founder is one connected product across desktop, web, and mobile:

- **Founder IDE** is where work is visible: code, conversation, diffs, agents, tests, Git, and shipping.
- **Founder Node** is bundled into the same desktop installation and performs approved local work in the background.
- **Founder web/mobile** is the remote control, community, discovery, launch, and trading surface.
- **Founder AI Gateway** is the default AI route and selects the right provider for the task.
- **Founder Vault** remembers user connections and secrets securely across sessions.

The user should never need to understand the internal split between the IDE, Node, Gateway, Event Bus, or provider adapters.

## 2. Identity tiers and commercial plans are different

Do not mix trust with billing.

### Identity and trust

- **Visitor:** X account exists. Can evaluate the product but cannot launch a token.
- **Doxxed Builder:** X + GitHub + founder video, reviewed by DoxxedCrypto. Can earn full builder rights and become eligible for token launch.

### Commercial plans

| Plan | Working offer |
|---|---|
| Free | Founder-managed evaluation allowance, local models, and a simple path to connect a personal provider. Exact allowance is configurable. |
| Founder Pro | **Proposal: USD 35/month**, Founder-managed allowance, remote control, automatic routing/fallback, and up to **five active personal cloud providers**. |
| Launch Partner | Contracted founders receive managed build and launch support, higher fair-use allowances, launch-readiness workflows, and access to the token-launch pipeline when trust and product gates are satisfied. |
| Team / Studio | Later: shared workspaces, roles, approvals, consolidated usage, and organization billing. |

Price, provider-slot count, included managed usage, and launch-partner commercial terms must be configuration-backed entitlements. Do not hard-code them into the IDE.

## 3. AI provider model

Founder supports three routes at the same time.

### Founder Managed — default

- Appears first as **Founder Auto**.
- Uses capability-based choices such as Fast, Balanced, Architect, and Autonomous.
- Routes through the Founder Gateway with health checks, usage controls, caching, and fallback.
- Launch Partners can receive a contract-specific managed allowance.
- Raw provider names remain available in request details, not as the primary everyday UI.

### Personal Provider Vault

- A user connects a provider once and Founder remembers it until revoked.
- Multiple providers remain connected simultaneously; changing the active route must not require re-entering a key.
- Initial provider set: OpenAI, Anthropic, Gemini, DeepSeek, GLM, OpenRouter, Ollama, and other existing adapters that pass security review.
- Each credential has: provider, friendly label, masked identity, verification status, last health check, last used time, and revoke control.
- A Pro slot counts an active cloud credential. Local Ollama does not consume a paid cloud-provider slot.
- The user can set a preferred provider or leave Founder Auto selected. Founder may fail over only to providers the user has enabled for that workspace.

The existing `IntegrationCredential` contract already stores one encrypted credential per `userId + provider`, so the backend can remember several providers today. The missing work is a clean entitlement-aware API and IDE experience around it.

### Local and private

- Ollama and approved local runtimes execute through Founder Node.
- Local mode must fail closed: no prompt, code, attachment, or derived context is sent to a cloud provider.
- Hybrid mode may use only the services explicitly enabled for the workspace.
- Cloud mode uses Founder Managed or selected personal providers.

## 4. IDE experience

Everyday navigation is limited to three desks:

1. **Build** — conversation, current objective, visible edits, diff, terminal, tests, and contextual approval.
2. **Agents** — active and recent workers, role, progress, cost, pause, stop, resume, and handoff.
3. **Ship** — changes, tests, commit, pull request, deploy, verify, token-launch readiness, and rollback.

Settings are secondary and contain only:

- Account and plan
- AI and provider connections
- Privacy mode
- Founder Node
- Notifications
- Advanced controls

The default model control is an intent selector, not a catalogue of model version strings. Advanced users can inspect the exact provider/model and override routing in Advanced controls.

## 5. Website information architecture

The current hub exposes too many destinations at once. Replace the permanent multi-row menu with three top-level intents:

| Top level | Contains |
|---|---|
| **Build** | Founder workspace, Agents, Ship, Founder Node, downloads, remote sessions, provider connections |
| **Discover** | Projects, founders, trust, scout signals, community updates, reputation |
| **Trade** | DEX, portfolio, watchlist, market feed, predictions, Raise Room activity |

Account, plan, notifications, privacy, security, connections, and downloads live in the user menu. DDollar is a balance/control, not a permanent top-level destination.

Token launch is contextual:

- Eligible founder sees **Prepare launch** in Ship.
- Community sees Raise Room progress on the project page.
- Traders see the graduated token in Trade/DEX.
- Users who are not eligible are not shown a dead launch navigation item.

### Visual direction

- One dominant action per state.
- Progressive disclosure instead of dashboards full of controls.
- Strong typography, generous spacing, stable layout, and quiet motion that explains state.
- Compact contextual sheets for details; advanced configuration stays out of the main workspace.
- No decorative gradients, floating sections, nested cards, or a palette dominated by one dark hue.
- Desktop, mobile, and IDE use the same language and state names.

## 6. Token launch and DEX

The DEX is part of the long-term revenue loop, but it is not the first screen of Founder OS.

Current later-stage design direction in `REVERSE-TOKENOMICS-SPEC.md`:

- Raise Room graduates launch on the platform bonding curve.
- **1% pre-graduation fee:** 0.5% founder revenue + 0.5% platform-token buyback/burn.
- Graduated liquidity moves to the platform AMM.
- **0.25% post-graduation fee:** 0.125% founder revenue + 0.125% buyback/burn.
- Founder payout is in USDC or the relevant native asset.
- Launch rights require Doxxed Builder status and product/trust gates.

This later design conflicts with older screens and `BILLING.md`, which still say 0.1%. Treat the fee schedule as unresolved in user-facing copy until the founder locks it and legal/security review is complete. Smart contracts, custody assumptions, geofencing, audits, liquidity migration, and rollback/incident plans are mandatory release gates.

## 7. Next engineering milestone

Build **Provider Vault and Routing** before redesigning the whole website:

1. Add an authenticated provider-connections endpoint for the IDE that returns capability, connection, health, entitlement, and masked credential state.
2. Add configurable entitlements for plan, managed allowance, and active provider slots.
3. Replace the IDE’s external Connections launcher with a native Founder Connections panel.
4. Keep Founder Auto selected by default; show personal providers and Local beneath it.
5. Support connect, verify, rename, enable per workspace, preferred route, fallback order, and revoke.
6. Never place raw keys in VS Code settings, logs, webview state, or IPC messages.
7. Enforce Local/Hybrid/Cloud in the Gateway and Node, not only in the UI.
8. Add tests for multi-provider persistence, slot limits, provider switching, fallback, revoked keys, and local-mode cloud blocking.

After this contract is stable, both the IDE and redesigned website can consume the same provider and entitlement model.

## 8. Decisions to lock

1. Confirm Founder Pro at **USD 35/month**.
2. Confirm **five** active personal cloud providers for Pro.
3. Decide whether Free includes one personal cloud provider or unlimited BYOK with fewer managed features.
4. Define the monthly/daily Founder Managed allowance by plan and for Launch Partners.
5. Confirm whether the latest 1% / 0.25% DEX schedule supersedes every older 0.1% reference.
6. Confirm the 50% founder / 50% buyback-and-burn split.
7. Obtain legal and security approval before presenting launch/DEX economics as available production functionality.
