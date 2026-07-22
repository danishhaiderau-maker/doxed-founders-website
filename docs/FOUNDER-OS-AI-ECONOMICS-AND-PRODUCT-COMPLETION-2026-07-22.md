# Founder OS AI Economics and Product Completion

Date: 2026-07-22

## Product decision

Founder OS is model-agnostic. The product is the coordinated, verified completion of a founder's goal, not access to a particular model or a large advertised token number.

The user-facing promise is:

- Founder Free supplies a simple managed quota for questions, planning, explanations, and small edits.
- Paid plans unlock serious autonomous work, longer verification loops, remote actions, and more concurrent coordinated agents.
- Personal provider keys and local models remain available without consuming managed quota.
- Founder Free uses DeepSeek V4 Flash. Builder and Team start on Flash and can
  explicitly use V4 Pro for complex code, reasoning, or verification.

Exact tokens, provider costs, cache rates, and margin controls remain visible to platform admins.

## Current production truth

- The installed IDE uses Founder OS aliases: Auto, Fast, Reasoning, and Code.
- The last verified production routes use DeepSeek v4 Pro or Flash. GLM must remain disabled until its configured API key and target model pass an authenticated completion probe.
- Admins can add platform-wide OpenAI-compatible providers with a name, base URL, model, and API key.
- Ordinary founders can connect several known providers and switch connected providers in website chat.
- Ordinary founders have unlimited encrypted named provider profiles in the IDE with display name, base URL, API key, model ID, optional headers, connection testing, persistence, and chat selection.
- Managed usage is reserved atomically before provider access, reconciled from provider usage, and conservatively charged when an in-flight result is uncertain.
- Builder and Team coordination is server-backed across tasks and devices, with local atomic path claims, server fencing generations, overlap rejection, heartbeats, and an audit trail.
- Founder Free is fenced to V4 Flash even when a Code or Reasoning intent is requested. Builder and Team can use V4 Pro, and the served route is exposed in the response receipt.
- The older website copilot now follows the same managed DeepSeek-only model
  policy and reservation lifecycle. A bounded authenticated health probe
  disables an unhealthy managed route and automatically restores it on a
  later successful probe.

## Seven-stage completion plan

### 1. Truthful quota and provider experience

- Replace public raw token promises with Founder Free quota language.
- Show percentage, state, and expiry in Account and Founder IDE.
- Keep exact metering in Admin -> AI & Usage.
- Add unlimited personal AI profiles with display name, base URL, API key, model ID, optional headers, and a connection test.
- Make every enabled profile available in the chat model dropdown.
- Apply plan limits to managed concurrency and platform spend, not to personal profiles.

Acceptance: a founder can add two compatible endpoints, name them, test them, switch between them from chat, restart the IDE, and find both profiles intact.

### 2. Shared agent awareness

- Store an agent lease containing task ID, goal, workspace, branch or worktree, owned files, heartbeat, and current phase.
- Publish file-intent and decision events before edits.
- Warn on overlapping ownership and open a direct coordination channel automatically.
- Refresh awareness on important events and at a low-frequency fallback interval; do not poll large chat transcripts every few minutes.
- Permit independent work when ownership does not overlap.
- Require a merge coordinator for multi-agent tasks.

Acceptance: two tasks editing the same file discover the overlap before writing; two independent tasks continue without extra context traffic.

### 3. Cost-aware routing and hard budgets

- Convert tokens to an internal USD-normalized compute cost using the active provider price table.
- Reserve estimated task cost before dispatch and reconcile it after completion.
- Route classification, retrieval, summaries, and simple edits to the cheapest passing model.
- Escalate to a stronger model only for high-risk work or after verifier failure.
- Cache stable system, policy, repository-map, and tool-schema prefixes.
- Use diffs, symbols, and artifact references instead of repeatedly sending whole files or agent transcripts.
- Stop failed loops after a bounded retry count and ask for a decision.

Acceptance: no request can exceed its user, task, or global cost reservation; admin sees cost, margin, latency, cache hit rate, and success by route.

### 4. Plans, billing, and capacity

Proposed packaging, pending final pricing approval:

- Free: one active managed task, light work, queued execution, personal and local AI available.
- Builder: serious autonomous builds, two or three coordinated agents, remote actions, deployment connections, and several remembered personal profiles.
- Team: shared projects, pooled capacity, roles, audit trail, and higher concurrency.
- Launch Partner: contracted platform capacity and token-launch support with negotiated limits.

Maintain a variable inference cost target below 20-30 percent of subscription revenue. When included capacity is depleted, offer personal AI, local AI, a top-up, or a controlled queue. Do not silently degrade quality.

Acceptance: a 100-founder load test proves rate limits, reservations, routing, cache behavior, billing events, and a positive margin under the expected workload mix.

### 5. Founder workflow completion

- Finish remote Founder Node actions with explicit approval, audit receipts, cancellation, and stale-result rejection.
- Make chat edits visible as proposed diffs, allow accept or reject, run tests, and show evidence.
- Complete deployment connections for GitHub, Vercel, Railway, and Neon.
- Add speech input, recent chats, projects, and simple task history.
- Keep Founder Node embedded in the single Founder IDE installer.

Acceptance: sign in once, open a project, request a change, watch the edit, approve it, run tests, deploy, and review the receipt from desktop or web.

### 6. One Windows product and living companion

- Keep one installed product and one taskbar entry: Founder IDE.
- Founder Node runs as its embedded background relay.
- Founder Dragon is a transparent, draggable companion window with no taskbar entry.
- Dragon states are resting, working, delivered, attention, and offline; each state is driven by real task events.
- Installer removes or migrates obsolete Founder Node, Founder Stack, Founder Slack, and Void-era registrations after a verified backup of user data.
- Sign releases and prove update rollback on a clean Windows VM.

Acceptance: one desktop shortcut and one taskbar icon launch the complete product; the companion can be hidden without stopping Founder Node.

### 7. Apple-inspired IDE and website convergence

- Use the same information architecture across IDE and website: Overview, Work, Decisions, Connect, Data, Account.
- Prefer labels for primary navigation; reserve icon-only controls for universally understood actions with tooltips.
- Remove Void names, old onboarding, duplicate settings, stale promo claims, and unused navigation.
- Keep code, source control graph, search, terminal, tests, extensions, and debugging available through progressive disclosure.
- Redesign the authenticated website account, admin control, Founder Den, project profiles, launch flow, DCF Swap, acquisition inquiry, and analytics surfaces around current production capability.
- Apply one restrained component system: compact typography, 6-8 px radii, neutral surfaces, semantic status colors, short motion, reduced-motion support, and responsive layouts.
- Run daily visual and end-to-end QA across desktop and mobile viewports, including console, network, accessibility, overflow, authentication, and core workflow evidence.

Acceptance: no visible Void branding, no duplicate control surfaces, no dead routes, and the full first-time-founder journey passes on production.

## Agent operating modes

- Focused: one agent; default for most work.
- Team: two or three agents with separate ownership and a coordinator.
- Studio: up to five agents for paid, clearly separable workstreams.

Do not advertise a large swarm count as the product. More agents can increase cost, merge risk, and duplicated context. Scale only when decomposition produces measurable speed or quality gains.

## Research basis

- DeepSeek pricing and context caching: https://api-docs.deepseek.com/quick_start/pricing-details-usd and https://api-docs.deepseek.com/guides/kv_cache
- Google Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
- Anthropic multi-agent architecture and context engineering: https://www.anthropic.com/engineering/multi-agent-research-system and https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Cursor plan structure: https://cursor.com/pricing and https://docs.cursor.com/account/pricing
- OpenRouter low-cost routing and BYOK: https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/
- Z.AI pricing and subscription restrictions: https://docs.z.ai/guides/overview/pricing and https://docs.z.ai/legal-agreement/subscription-terms
- Multi-agent coordination video-linked examples: https://fulcrumresearch.ai/2025/10/22/introducing-orchestra-quibbler.html and https://cosine.sh/blog/reinvent-2025-multi-agent-orchestration

## Release gates still open

- Approve prices and connect the billing entitlement source.
- Run the 100-founder capacity and margin test.
- Finish authenticated website deep-route redesign and production deployment.
- Produce a signed installer, clean-VM install proof, update rollback proof, and soak evidence.
- Repair the current Windows shortcut and verify there is only one active installed product entry.
