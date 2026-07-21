# Founder End-to-End Product Audit

| Field | Value |
|---|---|
| Status | Living audit and implementation contract |
| Updated | 2026-07-21 |
| Scope | Founder IDE, embedded Node, website, Founder ID, connections, daily QA, project intelligence, and acquisition interest |

## 1. Current evidence

### Founder IDE

- Founder ID, the embedded relay, the Founder control centre, Local/Hybrid/Cloud, gateway chat, authenticated IPC, and local edit/run/read tools are real.
- The installed product now uses three Founder Activity Bar containers: **Founder**, **Work**, and **Connect**. This fits the tested Windows scaling without shrinking native icons.
- **Work** contains the truthful Founder Dragon companion, Agents, and Ship. **Connect** contains Founder Node, provider connections, remote sessions, and later communication/calendar services.
- The installed chat gear now opens one responsive **Founder Settings** surface with Account, AI, Local & Cloud, Connections, and Advanced sections. User-facing `Void's Settings` copy and the inherited settings action are redirected with a rollback-backed post-build patch.
- The Founder Dragon is verified in the installed Work view. It is quiet while idle, flies only during observed extension chat/task work, breathes fire on verified delivery, and uses attention/error states rather than pretending to be busy. Native core-chat events still need a first-class bridge before the companion covers every inherited chat request.
- Founder Chat opens the native right sidebar and shows `founder-os-auto`. A real authenticated request reached the production gateway on 2026-07-21, but the active DeepSeek route translated the alias to obsolete `deepseek-coder-v2`; production rejected it with 400. The API workstream owns that routing repair and four-alias smoke. This is **implemented but not yet end-to-end verified**, not done.
- The server credential store can remember multiple sealed provider credentials, but the native entitlement-aware BYOK list, health state, selection, and failover are not fully wired into chat. The current Settings provider rows lead to the existing web connection surface.
- Debug Squasher already exposes platform health and a daily server run. It is not yet the complete local, website, integration, visual, and release evidence loop requested by the founder.

### Live website

The public route `https://doxxedcrypto.digital/founder-den?onboard=sovereign` was inspected signed out on 2026-07-21:

- The route loaded, its login callbacks were coherent, and the inspected page produced no browser console errors or warnings.
- The page currently presents Founder OS, a ten-stage journey, and a second workspace tab system while the global site navigation presents another set of destinations. This is too many simultaneous navigation models.
- IDE deep links currently target `/founder-den?onboard=sovereign` for remote control and `/settings/builder` for connections. Those destinations exist, but their names and information architecture do not yet match the IDE's Founder/Work/Connect model.
- Download copy still describes Founder IDE and a Founder Node tray as separate visible parts and advertises a standalone Node. The standard path must say **Founder IDE** is one application with an embedded background Node; standalone Node belongs under an Advanced disclosure only if it remains supported.

### Connections

The current shared integration registry contains GitHub, Cursor/Founder Copilot, X, Vercel, Railway, Render, Neon, DigitalOcean, and Supabase. Email, calendar, Telegram, Slack, Teams, wallet, and analytics providers are not implemented in that registry.

## 2. One product model

Users see one product and five consistent concepts:

| Concept | Desktop | Website/mobile |
|---|---|---|
| Founder | Home, account, current workspace | Founder/project identity and overview |
| Work | Build, Agents, Ship | Task creation, progress, approvals, receipts |
| Connect | Node, AI, code, deploy, data, communications, calendar, wallets | OAuth, permissions, revoke, device management |
| Discover | Contextual project intelligence | Projects, founders, trust, acquisition interest |
| Trade | Contextual launch readiness only | DCF Swap, portfolio, market and settlement |

The website gets only **Build**, **Discover**, and **Trade** as top-level intents. Account, Connect, notifications, plan, security, downloads, and admin controls live behind the appropriate user or admin control.

## 3. Founder Connect contract

Connections are grouped by purpose rather than shown as one long provider wall:

| Group | Initial providers |
|---|---|
| AI | Founder Auto, OpenAI, Anthropic, Gemini, DeepSeek, GLM, OpenRouter, Ollama |
| Code | GitHub, later GitLab/Bitbucket |
| Deploy | Vercel, Railway, Render, DigitalOcean |
| Data | Neon, Supabase |
| Communications | Gmail, Outlook, Telegram; later Slack and Teams |
| Calendar | Google Calendar, Outlook Calendar |
| Identity/social | X and Founder ID |
| Wallet | Solana and EVM self-custody wallets with signed ownership proof |
| Intelligence | DEX Screener market data, Birdeye holder/security data, and DCF-owned chain indexes |

Every connection shows the exact account, scopes, read/write boundary, workspaces allowed to use it, last use, health, and revoke control. Secrets stay in Founder Vault or the encrypted server credential store. Sending mail/messages, publishing, deploying, moving funds, or changing authority requires an action receipt and the configured approval policy; connecting a service never grants silent unlimited use.

## 4. Daily Founder Review

Founder must verify its own work at completion and once per 24-hour period.

### Completion receipt for every task

- Re-read the request and list each accepted requirement.
- Inspect the actual changed files and working tree.
- Run the smallest relevant typecheck, tests, build, and security checks.
- Open the installed/rendered product when UI changed and capture desktop plus narrow-window evidence.
- Exercise changed links, authentication handoffs, provider routes, error states, and disconnect/revoke paths.
- Report only **verified**, **implemented but unverified**, **externally blocked**, or **not started**.

### 24-hour review

- Run read-only local checks automatically while the app is open or at the next idle opportunity.
- Check identity expiry, embedded Node/IPC, gateway, update state, website/deep links, connected-provider health, recent deploy health, and unresolved failed tasks.
- Never upload source code or prompt content merely to perform the review. Local mode remains fully local.
- Network-backed platform checks may run for a signed-in account and disclose their last run; provider write tests and destructive actions remain approval-gated.
- Show one quiet result in Ship: last review, evidence, failures, fixes attempted, and next action. Do not create repeated notifications when nothing changed.
- Automatic repair is limited to reversible, pre-authorised actions. Code edits, deployments, wallet actions, authority changes, and paid operations remain reviewable proposals.

## 5. Project Intelligence

Every launched project profile should replace external dashboard hunting with a source-labelled view:

- price, market cap/FDV, liquidity, volume, buy/sell activity, pool age, and active markets;
- total holders, holder growth, top-holder concentration excluding known pools/vaults/program accounts, and distribution bands;
- founder/team allocation, vested, claimable, claimed, unvested, vesting end, and approved beneficiary wallet;
- liquidity ownership/lock status, mint/freeze/upgrade authority, treasury, incidents, audits, and contract version;
- 30/90/180-day **conviction cohorts**: wallets that held a positive balance throughout the period and did not make a net disposal, with sybil/bot and transfer caveats;
- product evidence: users, releases, repository activity, uptime, revenue where disclosed, and trust/review milestones.

Use DEX Screener for fast public pool/price/liquidity/volume data, Birdeye for Solana holder/security/market enrichment, and a DCF-owned event index for canonical vesting, founder authority, transfer history, cohort calculations, and acquisition snapshots. Cache third-party data and always show source plus observation time. Third-party market data is informative, not the settlement source of truth.

## 6. Acquisition Interest and Settlement

### Inquiry workflow

1. Founder explicitly enables **Open to acquisition interest** and chooses public, verified-buyers-only, or private.
2. A verified buyer reviews the public intelligence profile and requests a private data room.
3. The founder accepts or declines the introduction before private contact or documents are revealed.
4. The buyer submits a non-binding indication of interest with price, asset/share structure, currency, conditions, expiry, proof of funds, and requested control.
5. Founder, company, legal advisers, and any required token/shareholder governance review the offer. The UI never describes an inquiry as an accepted acquisition.

### Acquisition Settlement Vault

If the legally executed transaction allocates proceeds to token holders:

1. Publish the signed deal identifier, eligible chain/token, snapshot block, eligible-supply definition, exclusions, payout asset, claim rate, deadline, and unclaimed-funds policy.
2. Buyer or regulated escrow funds the complete payout before claims open.
3. An independently reproducible snapshot/Merkle root fixes each wallet's entitlement. LP, treasury, bridge, custodial, locked founder, and burned balances receive explicit treatment.
4. Eligible holders complete required identity/sanctions checks, submit a proof, and burn or irrevocably surrender the eligible token to claim stablecoin.
5. Contract prevents double claims and emits a permanent receipt. A public dashboard reconciles funded, claimed, remaining, rejected, and expired amounts.
6. Emergency pause can stop claims but cannot redirect funds. Changes require the published governance and legal process.

A token burn cannot by itself create company shares. Share conversion requires a legally recognised issuer, offer documents/exemptions, cap-table/registry handling, transfer restrictions, tax treatment, and licensed advice. V1 should support stablecoin settlement only after counsel approves the rights; token-to-share exchange is a separate regulated product.

## 7. Five delivery steps

1. **Founder shell and evidence:** three-container rail, Founder-native Home/Work/Connect, remove visible Void duplication, daily review surface, installed-app visual QA, and shared design/navigation language.
2. **Founder ID and Connect:** native provider vault, email/calendar/Telegram categories, narrow OAuth scopes, Local/Hybrid/Cloud enforcement, Node/device health, and revoke/receipt flows.
3. **Visible AI work:** Cursor-like code beside conversation, plan/timeline, streamed tools, diffs, tests, checkpoints, bounded agents, and remote task approvals.
4. **Ship and project intelligence:** Git/PR/deploy/rollback, website parity, DCF-owned chain index, holder cohorts, vesting dashboard, and acquisition inquiry/data room.
5. **Regulated launch and settlement:** audited enterprise launch template, founder vesting, DCF Swap/graduation, treasury governance, Acquisition Settlement Vault, legal/AML gates, signed installers, clean-machine and incident rehearsals.
