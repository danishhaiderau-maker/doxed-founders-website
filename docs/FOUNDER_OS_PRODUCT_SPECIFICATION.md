# Founder OS — Product Specification

> **Status:** Canonical · Single source of truth
> **Audience:** Every engineer, designer, and contributor who touches the Founder OS codebase
> **Owner:** Founder OS core team
> **Last updated:** 2026-07-01

This document is the formal engineering design specification for Founder OS. It is the reference every future development session should consult before proposing, designing, or implementing a feature. Every architectural decision, engineering rule, and product boundary in this repository traces back to the principles recorded here. When code and this document disagree, this document wins until the document is intentionally updated.

---

## Table of Contents

1. [Vision & Philosophy](#1-vision--philosophy)
2. [Product Positioning](#2-product-positioning)
3. [Architecture](#3-architecture)
4. [Founder Node](#4-founder-node)
5. [Browser Experience](#5-browser-experience)
6. [IDE Integration](#6-ide-integration)
7. [Cursor Integration (Flagship)](#7-cursor-integration-flagship)
8. [UX & UI](#8-ux--ui)
9. [Session Persistence](#9-session-persistence)
10. [AI Runtime](#10-ai-runtime)
11. [Community](#11-community)
12. [Founder Vault](#12-founder-vault)
13. [Roadmap](#13-roadmap)
14. [Engineering Rules](#14-engineering-rules)
15. [API Contracts](#15-api-contracts)
16. [Design System](#16-design-system)
17. [State Management](#17-state-management)
18. [Failure Modes](#18-failure-modes)
19. [Security](#19-security)
20. [Future IDE Support](#20-future-ide-support)
21. [Onboarding Flow](#21-onboarding-flow)
22. [Cost Management](#22-cost-management)

---

## 1. Vision & Philosophy

**Founder OS is the remote operating system for modern builders.** It connects to your desktop, resumes your work exactly where you left it, lets you use your preferred IDE and AI, helps you build in public, and keeps costs low by letting your laptop do the heavy lifting.

This is the one-sentence definition every contributor should be able to recite. If a proposed feature does not advance that sentence, it does not belong in Founder OS.

### 1.1 The Mission

> Let founders validate ideas, prototype products, and grow communities using their own hardware first, spending on cloud AI only when it creates real value.

The mission is an economic argument, not a technical one. Founders are resource constrained. Cloud AI is expensive and frequently wasteful — most prompts do not require a frontier model, and most "AI work" is mechanical refactoring, file reading, or git operations that a local model on a capable laptop can handle. Founder OS inverts the default billing model of modern AI tools: the laptop is the compute, the cloud is the exception.

### 1.2 The Core Principle

> Never replace existing tools. Connect them.

Founder OS is not a competing IDE. It is not a competing AI vendor. It is not a competing hosting platform. A founder has already chosen an IDE (Cursor, Claude Code, OpenHands, Windsurf, VS Code). They have already chosen a brain (GPT-5, Claude, GLM, DeepSeek, Gemini, Ollama). They have already chosen infrastructure (GitHub, Railway, Vercel, Neon, Docker). They have already chosen community surfaces (X, Discord, LinkedIn).

Founder OS does not ask them to switch. It asks only to be granted a thin bridge into each of those tools so it can orchestrate them as one continuous workspace. Wherever a tool exposes a capability, Founder OS uses it. Wherever it does not, Founder OS gracefully degrades and tells the truth about what is unavailable.

### 1.3 The Philosophy

> The founder should think only about Ideas. Founder OS thinks about coding, deployments, infrastructure, community, memory, documentation, publishing, cost optimization.

This is a separation-of-concerns statement directed at the human experience, not at the architecture. The founder's cognitive load should collapse to the idea layer: what to build, why to build it, who it is for, and what to ship next. Everything else — the mechanical act of writing code, the choreography of deployments, the bookkeeping of memory and documentation, the discipline of building in public, the budgeting of AI spend — is the operating system's job.

This is not a promise that Founder OS writes every line of code autonomously. It is a promise that the founder never has to context-switch into a dozen tools to make one idea real.

### 1.4 The Tagline

> Your laptop is the compute. Stop paying for cloud AI before you know your idea is worth building.

The tagline is the marketing compression of the mission. It positions Founder OS against the prevailing pattern in AI tooling — monthly subscriptions to cloud coding agents that bill for tokens before a founder has validated that the idea deserves another token. Founder OS flips the order: validate cheaply on local hardware first, then escalate to cloud AI only when the work clearly benefits.

### 1.5 The Moat

> The moat is not AI. The moat is continuity. People don't want another chatbot. They want to leave home, open their phone, and feel like they never left their desk.

This is the most important sentence in the entire specification, and it should govern every product decision. AI is a commodity. Every vendor has access to the same frontier models, the same open-weight local models, the same retrieval patterns. Competing on "we have AI" is competing on something any competitor can replicate in a weekend.

Continuity is not a commodity. Continuity is the experience of closing a laptop mid-thought at 11 p.m., opening a phone on a train at 7 a.m., and finding the exact conversation, the exact branch, the exact terminal scrollback, the exact draft you were working on — reattached and ready, with live events streaming from the desktop the moment it comes back online. Continuity is hard. It requires session persistence, desktop bridging, IDE adapters, event sourcing, and a refusal to ever simulate state that is not real.

Continuity is what Founder OS is built to deliver. Every feature either contributes to continuity or is a distraction.

---

## 2. Product Positioning

Founder OS occupies a category that most existing tools do not. It is **a developer operating system and control plane** — not an AI IDE, not a coding agent, not a cloud dev platform. It does not edit code in a panel. It does not run a coding agent loop in the browser. It does not host containers or virtual machines. It does not compete with any of the tools a founder already uses.

### 2.1 Category Definition

> Founder OS is the operating layer that sits above IDEs, AI models, infrastructure, and community tools, giving developers one place to resume work, orchestrate execution, manage infrastructure, and build in public from anywhere.

The phrase "operating layer that sits above" is deliberate. An operating system does not replace applications; it coordinates them, gives them a shared filesystem, a shared process model, and a shared surface for the user. Founder OS does the same for a founder's working life: it gives IDEs, AI models, infrastructure, and community surfaces a shared workspace model, a shared session model, and a shared resumption surface — the browser.

### 2.2 Competitor Comparison

Founder OS is not in the same row as any of these tools. The table below clarifies what each competitor does and why Founder OS does not replace it.

| Tool | Category | What it does | What Founder OS does instead |
|------|----------|--------------|------------------------------|
| **Cursor** | AI IDE | Edits code in a desktop editor with AI assist | Connects to Cursor as the Execution layer; orchestrates it remotely |
| **Claude Code** | Coding agent | Runs agentic coding loops in a terminal | Treats Claude Code as an Execution/Brain option behind the IDE Bridge Interface |
| **OpenHands** | Open coding agent | Runs autonomous coding agents | Wraps OpenHands as an Execution option in the runtime dropdown |
| **GitHub Copilot** | IDE AI completion | Inline completions and chat inside IDEs | Does not compete; Founder OS layers above the IDE that hosts Copilot |
| **Windsurf** | AI IDE | Edits code with Cascade agent | Future IDE adapter behind the same interface |
| **Replit** | Cloud dev platform | Hosts IDE, compute, and deploys in browser | Does not host compute; the laptop is the compute |
| **Continue.dev** | Open AI code assistant | Brings model choice into an IDE | Does not compete; Continue is an in-IDE assistant, Founder OS is the layer above |
| **Ollama** | Local model runtime | Runs LLMs locally | Uses Ollama as a Private-layer Brain option |
| **OpenRouter** | Model marketplace | Routes prompts to many providers | Uses OpenRouter as a Marketplace Brain option |
| **Linear** | Issue tracker | Tracks issues and projects | Does not track issues itself; integrates where exposed |
| **Notion** | Knowledge base | Stores docs and notes | Does not replace docs; Founder Vault stores private memory locally |

The pattern: every tool in the ecosystem has a job it does well. Founder OS never re-implements that job. It connects to the tool, learns what the tool exposes, and orchestrates it from a remote surface.

### 2.3 What NOT to Build

> Don't try to build a better Cursor, a better Claude Code, a better IDE. Instead, become the layer that makes all of them work together.

This is a guardrail, not a suggestion. The single fastest way for Founder OS to fail is to start competing with the tools it is supposed to connect to. Every hour spent building an in-browser code editor is an hour not spent on continuity, and it produces a worse editor than the one the founder already has open. Every hour spent training a custom coding agent is an hour not spent on session persistence, and it produces a worse agent than the ones already on the market.

The discipline is: when a feature is proposed, ask "does this build a competing IDE, a competing agent, or a competing hosting platform?" If the answer is yes, the feature is wrong.

### 2.4 The Stack Diagram

Founder OS sits at the top of a stack of tools the founder already uses. The diagram below is the canonical mental model for the product.

```
Founder OS
│
├── IDE
│     ├── Cursor
│     ├── Claude Code
│     ├── OpenHands
│     └── Future IDEs
│
├── Brain
│     ├── Cursor Auto
│     ├── GPT-5
│     ├── Claude
│     ├── GLM
│     ├── Ollama
│     └── Future models
│
├── Infrastructure
│     ├── GitHub
│     ├── Railway
│     ├── Vercel
│     ├── Neon
│     └── Docker
│
└── Community
      ├── X
      ├── Discord
      ├── LinkedIn
      └── Founder Feed
```

Each subtree is pluggable. New IDEs, new brains, new infrastructure providers, and new community surfaces are added by implementing the relevant adapter — not by rewriting Founder OS. The product's surface area is the orchestration layer and the resume experience; everything below the line is replaceable.

---

## 3. Architecture

Founder OS is structured as a three-layer architecture. The layers are not network tiers; they are responsibility boundaries. Every component in the system belongs to exactly one layer, and the layer dictates what that component is allowed to do.

### 3.1 Layer 1: EXECUTION

**Where work happens.** This layer owns the actual editing of code, the terminal, the filesystem, git operations, debugging, and the editor surface.

Members of this layer: Cursor IDE, Claude Code, OpenHands, VS Code, Windsurf, Future IDEs.

This layer owns:
- Repositories (cloning, branching, committing, pushing)
- Terminal (shell sessions, long-running processes, build output)
- Filesystem (reading, writing, watching files)
- Git (status, diff, history, remotes)
- Debugging (breakpoints, run configurations)
- Editor (open files, cursors, selections, multi-file edits)

Founder OS never duplicates these capabilities. If the user wants to edit a file, the edit happens in the connected IDE. If the user wants to run a terminal command, the command runs in the connected IDE's terminal (or in Founder Node's terminal proxy). Founder OS only observes, reports, and resumes.

The execution layer is also the layer that owns **artifact truth**. When a build fails, the build output that explains the failure lives in the execution layer. When a test passes or fails, the test report lives in the execution layer. When a file is half-saved during a crash, the half-saved state is the truth and Founder OS reports it rather than masking it with a sanitized summary. This is important because it means Founder OS's browser surface is never the place to debug a build; the browser tells the founder "build failed, here are the last 50 lines of output" and the founder opens their IDE to actually fix it. The browser is the alert; the IDE is the fix.

A consequence of this layering is that the execution layer must always be present for serious work. A founder can read prior conversation and review past events from the browser with the desktop offline, but they cannot do new execution work until an execution layer (an IDE, Founder Node's terminal proxy, or an OpenHands-style agent) is connected. This is a feature, not a limitation: it keeps the source of truth honest.

### 3.2 Layer 2: BRAIN

**How to think.** This layer owns reasoning: reading a prompt, deciding what to do, producing code changes, summarizing work, drafting community posts.

Members of this layer: Cursor Auto (the default — uses whatever Cursor currently uses, and Founder OS does not interfere), GPT-5, Claude, DeepSeek, GLM, Gemini, OpenRouter, Surplus, Jatevo.

Two operating modes:

1. **Default — IDE Brain.** If no external brain is selected, Founder OS forwards prompts to the connected IDE. The IDE's built-in AI does the reasoning. Founder OS streams the IDE's events back to the browser. This costs the user nothing extra because they already pay the IDE vendor, and it requires no API key management from Founder OS.

2. **External Brain.** If an external brain is selected (e.g. GLM, Claude, DeepSeek, Ollama), Founder Node performs the reasoning using that provider, applies changes directly to the repository on disk, and the connected IDE reflects those file changes through its normal file-watching mechanism. The IDE does not need to know that an external brain is running; it just sees files update on disk.

The external-brain mode is what makes "Founder Node does the heavy lifting" possible. Because the brain runs through Founder Node on the founder's laptop, the founder can use a local model (Ollama) for $0, or a cheap cloud model for fractions of a cent, while still seeing the result in their preferred IDE.

An important property of the brain layer is **provider substitutability**. The brain layer is the most interchangeable layer in the system. A founder who is using GLM today can switch to Claude tomorrow and back to Cursor Auto the day after, all without losing session state, because the session is bound to the workspace, not to the brain. The conversation history, the events, the terminal scrollback, and the branch state all persist across brain switches. The only thing that changes is which provider produces the next assistant message and which attribution chip appears on it (`🧠 GLM 5.2 · Cloud` vs `🧠 Claude Opus · Cloud` vs `🧠 Ollama · Local`).

This substitutability is also why the brain layer must be **stateless with respect to conversation continuity**. The brain does not own the conversation; the `WorkspaceSession` owns the conversation. The brain receives a prompt plus assembled context, returns a response, and is done. If the brain provider disappears mid-session (an API outage, a key revoked, a quota hit), the founder switches brains and continues the same conversation with a different provider. The conversation does not restart.

### 3.3 Layer 3: PRIVATE

**What never leaves the PC.** This layer owns sensitive memory, private models, and confidential context.

Members of this layer: Founder Vault, Ollama, Phala, Local Models, Private Memory.

The private layer is a hard boundary. No component in this layer ever transmits plaintext to the cloud. The API may see metadata (e.g. "vault sync occurred at 14:03, 12 entries") but never the contents of vault entries. Ollama runs locally and prompts to it never leave the laptop. Phala is used for confidential compute when a private model needs to run on remote hardware without exposing prompts to the host.

This is the layer that earns founder trust. Without it, Founder OS is just another cloud AI tool with a privacy policy. With it, founders can keep company intelligence — goals, roadmap, private notes, lessons learned — on hardware they control while still getting AI assistance.

The private layer also defines the **confidentiality contract** for the rest of the system. When Founder Brain reasons over vault contents, the reasoning path is constrained by where the contents are allowed to go. If the contents are strictly local, Founder Brain must use a local model (Ollama) or a confidential-compute path (Phala). If the contents are tagged as shareable with a specific cloud provider, Founder Brain may use that provider. The tagging is explicit and founder-controlled; Founder OS never silently upgrades a private context to a cloud context for convenience.

A subtle but important rule: the private layer is **additive, not fallback**. The private layer is not what the system uses when cloud is unavailable; it is what the system uses when the founder has decided that a given piece of context must never leave the laptop. Treating the private layer as a fallback would invert the trust model — the founder would have to opt out of cloud, rather than opt in. Founder OS inverts correctly: cloud is the opt-in, private is the default for sensitive context.

### 3.4 The IDE Bridge Interface

A central architectural concept: Founder OS does not assume every IDE exposes the same features. Instead, it defines a capabilities interface and asks each IDE adapter to implement as much of it as it can.

> Founder Node should define capabilities: Discover workspaces, List recent sessions, Resume a session, Send a prompt, Stream events, Report Git state, Report terminal state. Each IDE implements as much of that interface as it can. Founder OS adapts to available capabilities instead of assuming every IDE exposes the same features.

This is the extension point for the entire IDE ecosystem. A new IDE is supported by writing an adapter that implements whichever of those capabilities the IDE exposes — no more, no less. The browser UI then enables or disables features based on what the connected IDE actually supports. This is described in detail in §6.

### 3.5 Current Monorepo Tech Stack

The Founder OS codebase is a monorepo. The current stack is:

- **Web app:** Next.js (the browser experience — Remote Control)
- **API:** NestJS (the control plane backend)
- **Shared code:** `packages/utils` (shared TypeScript utilities, including money-feed logic, mission state, and context assembly helpers)
- **ORM:** Prisma
- **Web hosting:** Vercel
- **API hosting:** Railway
- **Database:** Neon (PostgreSQL)
- **CI/CD:** GitHub Actions

Founder Node is a separate Electron desktop application that pairs with the API and bridges to the local machine. It is documented separately in §4.

The monorepo layout keeps web, API, and shared code in lockstep. A change to a shared utility propagates to both surfaces in the same PR. The Prisma schema is the single source of truth for server-side persistence models, including `WorkspaceSession` and `ConnectedWorkspace` described in §9.

---

## 4. Founder Node

Founder Node is **the operating system runtime**. It is not a sync app, not a tray application, not a helper utility. Those framings undersell it. Founder Node is the persistent process on the founder's laptop that makes the laptop the compute and makes continuity possible.

### 4.1 What It Watches

Founder Node runs forever in the background and watches the following surfaces:

- **Cursor** — active workspace, repository, branch, recent sessions, agent state
- **Git** — status, diff, recent commits, current branch, remotes
- **Terminal** — long-running processes, scrollback, exit codes
- **Build** — build success/failure, build logs, test results
- **Deploy** — deployment triggers, deployment status, deployment logs
- **Files** — file watchers on repositories and vault directories
- **Community drafts** — pending "build in public" drafts ready to publish
- **Founder Vault** — encrypted local memory store
- **Ollama** — local model availability, loaded models, running inference jobs
- **Infrastructure** — connection health to GitHub, Railway, Vercel, Neon

Because Founder Node watches all of these surfaces continuously, the browser never has to ask "what is the desktop doing right now?" — it receives a live stream of events and a snapshot of the current state on demand.

### 4.2 Desktop ↔ Cloud Control Plane

Founder Node maintains a persistent connection to the Founder OS API. Over that connection it exchanges:

- **Heartbeat** — a periodic "I'm alive" signal with desktop metadata (online status, current workspace, current branch, connected IDE)
- **Vault sync** — encrypted vault entries synchronized to the cloud as metadata only; plaintext never leaves the laptop
- **Local inference** — prompts routed to Ollama (or other local models) and results returned to the browser via the API
- **Desktop bridge metadata** — the snapshot the browser reads to render the resume screen (workspace, repository, branch, agents, deploy status, git status, community drafts)

This control plane is what makes "the laptop is the compute" real. The browser is the remote control; Founder Node is the machine being controlled.

### 4.3 The Workflow

The canonical Founder OS workflow, end to end:

**At the desktop:**
1. The founder opens Cursor on a repository.
2. Founder Node detects the active workspace and reports it to Founder Bridge.
3. Founder Bridge registers the workspace as available for remote resume.

**Away from the desktop (phone or any browser):**
4. The founder opens Founder OS in a phone browser.
5. Founder OS shows the resume screen: "Good evening. Desktop Online. Cursor Connected. Repository: Founder OS. Branch: master. Agents: 3 Running. Deploy: Healthy. Git: Clean. Community: 1 Draft Ready. Continue."
6. The founder picks a Recent Workspace and continues the conversation.
7. The founder dispatches an instruction ("finish the auth tests and deploy").
8. Cursor executes the instruction (via the IDE Bridge Interface).
9. Live events stream back to the browser: "Searching auth.ts → Running tests → Reading README → Creating commit → Deploying → Waiting."
10. The founder closes the phone. The conversation, the events, the terminal scrollback, and the branch state are all persisted. Nothing is lost.

### 4.4 Security Model

Founder Node's security model is defined by what it does **not** do:

- **No remote desktop.** Founder Node never exposes a remote desktop protocol. The founder does not see a screen being mirroed; they see structured events and conversation.
- **No VPN.** Founder Node does not create a virtual network interface. It uses an outbound WebSocket to the Founder OS API — the same direction as a browser visiting a website.
- **No port forwarding.** Founder Node does not open any inbound ports on the founder's router or firewall. The laptop does not become a server.
- **Secure pairing via code.** A founder pairs a desktop to their account by generating a one-time code in Founder Node and entering it in the browser. The code binds the desktop to the account over the existing outbound connection.

This model is the reason Founder Node works behind corporate firewalls, home NATs, and mobile hotspots. It only ever speaks outbound.

### 4.5 Current Implementation

The existing Founder Node is an Electron tray application. Today it implements:

- **Pairing** — generate code, pair desktop to account
- **Heartbeat** — 45-second interval to the API with desktop metadata
- **Vault sync** — encrypted vault entries pushed to the API as metadata
- **Ollama inference** — local prompt execution against Ollama models
- **Sync-job polling** — polls the API for pending sync jobs (e.g. "run this local inference job")

Future versions of Founder Node will expand this surface to include the full IDE Bridge Interface implementation for Cursor and additional IDEs, direct git and terminal proxies, and richer event streaming. The current implementation is the foundation, not the final state.

### 4.6 Why Founder Node Is Not a Sync App

It is worth restating what Founder Node is not, because the framing of "sync app" is the most common misread. A sync app (Dropbox, iCloud Drive, Google Drive) watches a folder and mirrors it to the cloud. Founder Node does not mirror repositories to the cloud — git already does that through GitHub. Founder Node does not mirror the vault to the cloud as plaintext — it syncs only encrypted metadata. Founder Node does not mirror the terminal to the cloud — it streams terminal events to a connected browser session, and the stream ends when the session ends.

The distinction matters because it dictates the data flow. A sync app's job is to make two stores identical. Founder Node's job is to make a remote view of one store (the laptop) available to a remote control (the browser), with strict rules about what crosses the boundary. The laptop is never made identical to the cloud; the cloud is told only what it needs to know to render the resume screen and route the founder's instructions back.

### 4.7 Lifecycle and Restart Behavior

Founder Node is designed to run forever. On laptop boot, it starts (or is started by the founder) and immediately re-establishes its outbound connection to the API. If the laptop sleeps, the connection drops; on wake, Founder Node reconnects, re-sends its heartbeat, and re-publishes its desktop bridge snapshot. If the API is briefly unreachable, Founder Node retries with backoff and buffers events locally until the connection returns. If the laptop shuts down, Founder Node is simply absent; the browser shows "Desktop Offline" until the laptop returns.

This lifecycle behavior is what makes "everything reconnects" (Engineering Rule 7) real at the desktop layer. The founder never manually reconnects Founder Node. They open their laptop and the desktop is back; the browser notices within one heartbeat interval.

---

## 5. Browser Experience

The browser is **not** the operating system. The browser is **Remote Control**.

This distinction is critical. If the browser tries to be the operating system, it ends up reinventing an IDE in a tab — competing with Cursor, losing, and breaking continuity. If the browser is Remote Control, then its job is small and achievable: show the founder where they were, let them tell the desktop what to do next, and stream back what happens.

### 5.1 The Resume Experience

The first screen a founder sees when they open Founder OS is the resume screen. It is not a dashboard. It is not a chat. It is not a list of features. It is a single, dense, instant summary of the work they left behind.

> You are 20 km away. Open Founder OS in phone browser. Immediately:
>
> **Good evening Danish.**
> **Desktop Online.**
> **Cursor Connected.**
> **Repository: Founder OS.**
> **Branch: master.**
> **Agents: 3 Running.**
> **Deploy: Healthy.**
> **Git: Clean.**
> **Community: 1 Draft Ready.**
> **Continue.**

> No loading. No searching. No fake thinking. Just resume.

The resume screen is the product. If a founder opens Founder OS and has to think about what they were doing, Founder OS has failed. Every millisecond between opening the browser and seeing the resume screen is friction. Every piece of state that has to be reconstructed by the founder (which branch was I on? what was I building? what did the last agent do?) is a failure of continuity.

### 5.2 What the Browser Is Responsible For

- Rendering the resume screen from desktop bridge metadata
- Rendering the Recent Workspaces list (the most important screen — see §8)
- Hosting the conversation view (Cursor-style, not ChatGPT-style)
- Rendering the Live Events panel from the event stream
- Rendering the terminal panel from terminal scrollback
- Persisting WorkspaceSession state to the API on every meaningful change
- Surfacing the AI runtime dropdown, the publish panel, and infrastructure status

### 5.3 What the Browser Is NOT Responsible For

- Editing code (that happens in the connected IDE)
- Running build or test commands (that happens in Founder Node or the connected IDE)
- Running AI inference (that happens in Cursor Auto, an external brain via Founder Node, or a cloud provider)
- Storing the source of truth for repository state (the desktop is the source of truth)
- Inventing state when the desktop is offline (the browser shows the last known state and labels it as such)

The browser is a thin, honest view of a remote machine. It never pretends to be more.

---

## 6. IDE Integration

Founder OS supports multiple IDEs through a single abstraction: the **IDE Bridge Interface**. This is the contract every IDE adapter implements, and it is the only way an IDE connects to Founder OS.

### 6.1 The Interface

The IDE Bridge Interface defines these capabilities:

- **Discover workspaces** — list the workspaces currently open in the IDE
- **List recent sessions** — list the recent coding sessions the IDE knows about
- **Resume a session** — bring a specific session back to the foreground in the IDE
- **Send a prompt** — send a prompt to the IDE's AI assistant (when the IDE brain is selected)
- **Stream events** — subscribe to a stream of events from the IDE (file edits, terminal output, agent steps, git operations)
- **Report Git state** — report the current git status, branch, and recent commits for the active workspace
- **Report terminal state** — report the current terminal scrollback and running processes for the active workspace

### 6.2 Capability-Driven Adaptation

Each IDE implements as much of that interface as it can. Cursor, as the flagship integration, implements nearly all of it. A minimal IDE adapter might implement only "Discover workspaces" and "Report Git state." Founder OS does not penalize adapters for being partial — it adapts.

> Founder OS should detect and display what the connected IDE is doing — its active workspace, repository, branch, and recent sessions where available — but it should not promise to manipulate proprietary internal state that the IDE doesn't expose. The UI should enable or disable capabilities based on what the connected IDE actually supports.

This is the transparency principle. If an IDE does not expose "Send a prompt," the browser disables the prompt input and shows "Prompting not supported for this IDE — connect Cursor for full remote control." If an IDE does not expose "Report terminal state," the terminal panel shows "Terminal not available for this IDE." Founder OS never lies about what it can do.

### 6.3 Adapter Contract Sketch

A concrete sketch of the adapter contract, in TypeScript-shaped pseudocode, makes the abstraction tangible:

```typescript
interface IdeBridgeAdapter {
  readonly id: string;            // "cursor" | "claude-code" | "openhands" | ...
  readonly capabilities: Set<IdeCapability>;

  discoverWorkspaces(): Promise<Workspace[]>;
  listRecentSessions(workspaceId: string): Promise<Session[]>;
  resumeSession(workspaceId: string, sessionId: string): Promise<void>;
  sendPrompt(workspaceId: string, prompt: string): Promise<void>;
  streamEvents(workspaceId: string): AsyncIterable<IdeEvent>;
  reportGitState(workspaceId: string): Promise<GitState>;
  reportTerminalState(workspaceId: string): Promise<TerminalState>;
}

type IdeCapability =
  | 'discover-workspaces'
  | 'list-recent-sessions'
  | 'resume-session'
  | 'send-prompt'
  | 'stream-events'
  | 'report-git-state'
  | 'report-terminal-state';
```

Each adapter declares its `capabilities` upfront. The browser reads the declared set and renders only the UI affordances that map to declared capabilities. An adapter that declares only `discover-workspaces` and `report-git-state` produces a browser view with the workspace list and a read-only git status panel — no prompt input, no terminal panel, no resume button. The founder sees an honest, reduced surface.

The contract is intentionally async and stream-oriented. `streamEvents` returns an async iterable so the browser can consume a continuous feed without polling. The other methods are request-response because they answer discrete questions ("what are my workspaces?", "what is my git state?"). Adapters that cannot stream events can instead emit a periodic snapshot and let the browser diff; the contract treats a snapshot-only feed as a degraded but valid implementation of `streamEvents`.

### 6.4 Why an Interface, Not a Protocol

A common mistake would be to define a wire protocol and require every IDE to speak it. That fails because IDEs do not expose uniform surfaces — Cursor has Cursor-specific ways to be controlled, Claude Code has a terminal-based interaction model, VS Code has an extension API, and so on.

The interface is a capabilities contract, not a wire protocol. Each adapter is free to use whatever mechanism the IDE actually exposes (extension API, file watching, HTTP bridge, terminal automation, MCP). The adapter translates between that mechanism and the interface. The rest of Founder OS only ever talks to the interface.

This is also why Founder OS does not assume every IDE exposes the same features. The browser renders capabilities that exist and hides capabilities that don't. No founder sees a "Send prompt" button that silently fails.

---

## 7. Cursor Integration (Flagship)

Cursor is the flagship integration. It is the IDE Founder OS is designed against first, and it is the IDE the onboarding flow recommends by default. The Cursor integration is the reference implementation of the IDE Bridge Interface — every other IDE adapter is measured against it.

### 7.1 The Two Roles

In the Cursor integration, Cursor occupies two distinct roles, and the founder chooses between them at runtime:

- **Cursor IDE = Execution.** Cursor is always the execution layer. It owns the editor, the terminal, the filesystem, git, and debugging. This never changes.
- **Cursor Auto = Brain (default).** By default, Cursor's own AI (Cursor Auto) is the brain. The founder does not need to configure an external API key. Founder OS does not interfere with whatever model Cursor Auto uses internally — it simply forwards prompts to Cursor and streams Cursor's events back to the browser. This is the zero-config path.

### 7.2 The External Brain Option

When the founder selects an external brain (e.g. GLM, Claude, DeepSeek, Ollama), the workflow changes:

- **Founder Node executes tasks using the chosen AI.** The prompt goes to the external brain, not to Cursor Auto.
- **Repository updates are applied directly.** Founder Node writes file changes to the repository on disk.
- **Cursor reflects file changes automatically.** Cursor's file watcher picks up the changes and the editor updates without any Cursor-specific integration needed.

### 7.3 The Two Workflows

Two canonical Cursor workflows, side by side:

**Workflow A — Cursor + Auto (default):**
```
Cursor  ↓  Auto (included, no API needed)
```
The founder's prompt goes to Cursor Auto. Cursor Auto reasons, edits files, runs commands. Founder OS streams Cursor's events back to the browser. Cost: whatever the founder already pays Cursor. Configuration: none.

**Workflow B — Cursor + External Brain:**
```
Cursor  ↓  GLM  →  Founder Node  →  Repository  →  Cursor updates automatically
```
The founder's prompt goes to the external brain (GLM in this example) via Founder Node. Founder Node applies changes to the repository on disk. Cursor's file watcher reflects the changes in the editor. Cost: the external brain's cost (often a fraction of Cursor Auto). Configuration: API key for the external brain, stored encrypted.

### 7.4 What the Browser Shows for Cursor

When Cursor is the connected IDE, the browser surfaces:

- **Recent Cursor conversations** — the sessions Cursor knows about, available to resume
- **Repositories** — the repositories currently open in Cursor
- **Branches** — the active branch per workspace, plus recent branches
- **Agents** — Cursor agents currently running (with live status: Running, Waiting, Completed)
- **Context** — the files and context Cursor has loaded for the active session

All of this is rendered from the desktop bridge snapshot and the event stream. None of it is invented. If Cursor is offline, the browser shows the last known state with a "Cursor Disconnected" label.

### 7.5 The No-Interference Promise

When Cursor Auto is the selected brain, Founder OS does not interfere with Cursor's model choice, Cursor's prompt handling, Cursor's context assembly, or Cursor's billing. The founder's Cursor subscription is the only cost; Founder OS adds zero AI cost on top. This is the "no API needed" path and it is the path onboarding recommends by default.

This promise is the foundation of the Founder OS / Cursor relationship. Founder OS is not a competitor to Cursor; it is a remote control surface for Cursor. A founder who is happy with Cursor Auto pays nothing extra to use Founder OS. A founder who wants a different brain opts in via the runtime dropdown and accepts the associated cost. The default is the cheapest path, not the most profitable one for Founder OS.

### 7.6 What "Cursor Reflects File Changes Automatically" Means

In the external-brain workflow, the founder might worry that their IDE and their brain will diverge — the brain edits files on disk while the IDE shows a stale buffer. This does not happen because Cursor (and every modern IDE) watches files on disk and reloads them when they change externally. When Founder Node writes a change to a file, Cursor's file watcher picks it up within milliseconds and the editor updates. The founder sees the brain's work appear in the editor as if they had typed it.

The corollary is that the founder must save their open buffers before dispatching an external-brain task, or accept that unsaved changes may be overwritten. Founder OS surfaces this in the dispatch UI: "You have unsaved changes in 2 files. Save before dispatching?" This is the one place Founder OS proactively warns about file state, because it is the one place where the external-brain path can conflict with the IDE's in-memory state.

---

## 8. UX & UI

The Founder OS workspace layout is designed to feel like a continuous extension of the IDE the founder was just using — not like a separate chat app they have to learn.

### 8.1 Workspace Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Top bar: Repository · Branch · Desktop Connected · Brain Current · Community │
├──────────┬──────────────────────────────────────────────┬───────────────────┤
│          │                                              │                   │
│  Left    │   Middle: Conversation                       │  Right: Live      │
│  sidebar │   "Feels exactly like Cursor, not ChatGPT"   │  Events           │
│          │                                              │  "Never fake"     │
│          │                                              │                   │
├──────────┴──────────────────────────────────────────────┴───────────────────┤
│  Bottom: Terminal — Resizable · Persistent · Reconnects                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Top bar.** Repository, Branch, Desktop Connected, Brain Current, Community Status. This is the at-a-glance state of the workspace. A founder should be able to read the top bar and know exactly which workspace they are in, which branch they are on, whether the desktop is online, which brain is selected, and whether there is a community draft waiting to be published.

**Left sidebar.** The left sidebar is the navigation surface. Its sections, in priority order:

- **Recent Workspaces** — the most important screen in the entire product. Not chats. Not a dashboard. Workspaces. This is the resume entry point.
- **Explorer** — file explorer for the active workspace's repository
- **Repositories** — list of connected repositories
- **Community** — drafts and published updates
- **Deployments** — recent deployments and their status
- **Infrastructure** — health of connected infrastructure (GitHub, Railway, Vercel, Neon)
- **Settings** — account, IDE, brain, and Founder Node configuration

**Middle.** The conversation view. This is where the founder talks to the brain. It must feel exactly like Cursor's composer and chat surface — not like ChatGPT's thread model. The difference is critical: Cursor restores existing conversations; ChatGPT starts new ones. Founder OS restores existing conversations because continuity is the moat.

**Right panel.** The Live Events panel. This is the second most important surface in the product after Recent Workspaces. It shows what the desktop is doing right now, in real time, as a stream of honest events.

> Searching auth.ts → Running tests → Reading README → Creating commit → Deploying → Waiting. Never fake.

Every event in this panel is a real event from a real adapter. Founder OS never synthesizes events to make the panel look busy. If the desktop is idle, the panel shows "Waiting." If the desktop is offline, the panel shows "Desktop Offline." Honesty is non-negotiable.

**Bottom.** The terminal. Resizable. Persistent. Reconnects. The terminal is the founder's escape hatch when they need to do something the conversation surface does not expose. Its scrollback is persisted as part of the WorkspaceSession (see §9) so closing and reopening the browser does not lose command history.

### 8.2 Recent Workspaces

Recent Workspaces is the most important screen in Founder OS. It is the answer to "where was I?" The screen is a list of workspaces, each with a status dot, a title, the IDE used, the AI used, the current state, and the last activity time.

```
🟢 Founder OS redesign      Cursor          Running          2 min ago
🟡 Founder Vault            Claude Code     Waiting
🟢 Trading Bot              Cursor          Running Tests
⚪ Landing Page                             Completed
⚪ Community                DeepSeek        Draft Ready
```

Each workspace row shows:
- **Title** — the same title as in Cursor (or the connected IDE)
- **Repository** — the repository the workspace is operating on
- **Branch** — the active branch
- **Last activity** — relative timestamp
- **IDE used** — Cursor, Claude Code, OpenHands, etc.
- **AI used** — Cursor Auto, GLM, Claude, DeepSeek, etc.
- **State** — Running, Waiting, Completed, Running Tests, Draft Ready
- **Number of agents** — how many agents are currently active in the workspace

### 8.3 Clicking a Workspace Restores Everything

Clicking a workspace row is the resume action. It restores, in one click:

- **Conversation** — the full chat history, exactly as it was left
- **Events** — the rolling event log, hydrated from the persisted last 50
- **Attachments** — files, diffs, and context attached to the conversation
- **Branch** — the active branch in the IDE
- **Repository** — the active repository in the IDE
- **AI provider** — the brain that was selected for this workspace
- **Terminal** — the terminal scrollback
- **Community drafts** — any "build in public" drafts associated with this workspace

This is what "Never lose context" means in practice. The founder clicks one row and is back in the exact state they left — on any device, in any browser.

### 8.4 The Status Dot Semantics

The status dot in each workspace row is the founder's at-a-glance read on what is happening. The semantics are strict and shared across the workspace list, the top bar, and the Live Events panel:

- 🟢 **green — running.** An agent, a build, a deploy, or a long-running terminal command is actively producing events right now. If the dot is green, there is real work in flight.
- 🟡 **yellow — waiting.** The workspace is in a state that expects more work but is paused — an agent waiting for input, a deploy waiting for CI, a test run waiting for a lock. Yellow means "the system expects to continue; it is blocked on something specific."
- ⚪ **gray — completed or dormant.** The workspace has no active work and no expected continuation. The last task finished. The founder can resume by dispatching a new instruction.

These three states cover everything. Founder OS does not introduce a fourth "thinking" state, because thinking is either backed by an in-flight AI request (green, running) or it is not happening (gray). A "thinking" animation with nothing behind it is the most common fake-progress pattern in AI tools, and Founder OS prohibits it (Engineering Rules 1 and 2).

### 8.5 Information Density Over Whitespace

The Founder OS UI is intentionally dense. A power-user tool that prioritizes whitespace over information forces the founder to scroll and click to find what they need. The dense layout — small font sizes, compact sidebar rows, compact event entries — means a single browser viewport shows the workspace list, the conversation, the live events, and the terminal without scrolling. Whitespace is reserved for the resume screen greeting, where the founder benefits from a moment of orientation before diving back in.

---

## 9. Session Persistence

> Never lose context. Refresh — Nothing disappears. Browser crashes — Nothing disappears. Phone changes — Nothing disappears. Everything reconnects.

Session persistence is the mechanism that makes continuity real. It is the difference between "the browser is a remote control" and "the browser is a remote control that forgets."

### 9.1 The WorkspaceSession Model

The `WorkspaceSession` Prisma model is the server-side source of truth for a workspace's session state. Its fields:

- `selectedAiProvider` — the brain selected for this workspace (e.g. `cursor-auto`, `glm`, `claude`, `ollama`)
- `selectedModelKey` — the specific model within the provider (e.g. `glm-5.2`, `claude-opus-4`)
- `selectedIdeProvider` — the IDE selected for this workspace (e.g. `cursor`, `claude-code`, `openhands`)
- `conversation` — the full conversation history as structured JSON
- `terminalScrollback` — the terminal scrollback buffer
- `openFiles` — the list of files open in the IDE at the time of save
- `activeNav` — the active left-sidebar section (Recent Workspaces, Explorer, etc.)
- `panelState` — the state of collapsible panels (open/closed, sizes)
- `publishDraft` — the current "build in public" draft, if any
- `eventLog` — the rolling event log (last 50 events, see §9.4)

A simplified Prisma sketch:

```prisma
model WorkspaceSession {
  id                  String   @id @default(cuid())
  userId              String
  workspaceId         String
  selectedAiProvider  String?
  selectedModelKey    String?
  selectedIdeProvider String?
  conversation        Json?
  terminalScrollback  Json?
  openFiles           Json?
  activeNav           String?
  panelState          Json?
  publishDraft        Json?
  eventLog            Json?
  createdAt           DateTime @default(now())
  updatedAt           DateTime @updatedAt

  connectedWorkspace ConnectedWorkspace @relation(fields: [workspaceId], references: [id])

  @@unique([userId, workspaceId])
}
```

### 9.2 The ConnectedWorkspace Model

The `ConnectedWorkspace` model represents a workspace the founder has connected to Founder OS. Its fields:

- `label` — the human-readable name of the workspace
- `repository` — the repository path or URL
- `branch` — the active branch at last contact
- `ideProvider` — the IDE the workspace is bound to
- `aiProvider` — the brain the workspace is bound to
- `lastActiveAt` — when the workspace was last active

```prisma
model ConnectedWorkspace {
  id          String   @id @default(cuid())
  userId      String
  label       String
  repository  String?
  branch      String?
  ideProvider String?
  aiProvider  String?
  lastActiveAt DateTime @default(now())
  createdAt    DateTime @default(now())
  updatedAt    DateTime @updatedAt

  sessions WorkspaceSession[]

  @@unique([userId, label])
}
```

### 9.3 The Uniqueness Constraint

`@@unique([userId, workspaceId])` on `WorkspaceSession` enforces **one session per user per workspace**. This is deliberate. If a founder opens the same workspace in two browsers, they share the same session — the most recent write wins, and both browsers see the same state. There is no "branching" of sessions per browser tab. Continuity is preserved by always reading and writing the same row.

### 9.4 Auto-Restore

> The browser always tries to restore the developer to the same workspace they left. It should never invent or simulate those sessions.

On load, the browser reads the active workspace ID from `localStorage` (`dcf:active-workspace-id`) and fetches the corresponding `WorkspaceSession`. **If the session has conversation data, the browser auto-restores it without blocking the resume modal.** The founder lands directly in their last workspace, with their last conversation visible, without clicking through any "welcome back" friction.

If no session exists or the session has no conversation data, the browser falls back to the Recent Workspaces list. The founder picks a workspace to enter.

### 9.5 The Event Log

The event log is a rolling buffer of the last 50 events, persisted as part of the `WorkspaceSession`. On reload, the browser hydrates the Live Events panel from this buffer so the founder immediately sees what was happening when they left. New events append to the buffer; the oldest events roll off once the buffer exceeds 50.

The in-memory event bus (see §17) holds up to 200 events for the current session; only the last 50 are persisted. The larger in-memory window keeps the current session responsive; the smaller persisted window keeps the row in the database small.

### 9.6 What Session Persistence Is Not

- It is not a chat history export feature. The conversation is persisted so it can be restored, not so it can be downloaded.
- It is not a per-browser-tab state. The session is per user per workspace, not per tab.
- It is not a substitute for the desktop bridge. The session persists what the founder saw; the desktop bridge reports what the desktop is doing now. Both are needed.

### 9.7 The Save Cadence

Session persistence is debounced, not instant. The `useDebouncedWorkspaceSessionSave` hook waits for a short quiet window after the last change before writing to the API. This avoids hammering the database on every keystroke or every event arrival, while still guaranteeing that the session is saved within a bounded window of the last change.

The cadence is tuned for two competing pressures:

- **Don't lose more than a moment's work.** If the browser crashes, the founder should lose at most a few seconds of conversation or a few events — never the whole session.
- **Don't overwhelm the API.** A streaming conversation can produce dozens of token chunks per second; persisting on every chunk would generate a write storm. Events can arrive in bursts; persisting on every event would do the same.

The debounce window is the balance. Token chunks accumulate in React state and are persisted as a batch when the assistant message completes. Events accumulate in the in-memory bus (max 200) and the persisted log (last 50) is updated on a slower cadence. Panel state and nav changes are low-frequency and persist immediately.

### 9.8 What Happens on Reload

The reload path, step by step:

1. The browser reads `dcf:active-workspace-id` from `localStorage`.
2. If an active workspace ID exists, the browser calls `GET /connected-workspace/:id/session`.
3. The API returns the persisted `WorkspaceSession` (or creates an empty one if none exists).
4. The browser validates the JSON shapes of the persisted fields (conversation, eventLog, terminalScrollback, etc.).
5. If the conversation has messages, the browser auto-restores directly into the workspace — no resume modal, no "welcome back" gate.
6. If the conversation is empty, the browser falls back to the Recent Workspaces list.
7. The Live Events panel is hydrated from the persisted `eventLog` (last 50 events).
8. The terminal panel is hydrated from the persisted `terminalScrollback`.
9. The browser opens a fresh SSE connection for live events and a fresh desktop-bridge poll for the current snapshot.

The founder lands in their workspace, with their conversation, their events, and their terminal — within one round-trip of opening the browser. This is the "no loading, no searching, no fake thinking, just resume" promise, implemented.

---

## 10. AI Runtime

The AI Runtime is the surface where the founder chooses what executes their work and what thinks about their work. It is exposed as a dropdown with five sections, organized by category.

### 10.1 The Five Sections

**1. Execution** — Where work happens.

- Cursor
- OpenHands
- Claude Code (Coming Soon)
- VS Code (Coming Soon)
- Windsurf (Coming Soon)

**2. Brains** — How to think.

- GPT-5
- Claude
- Gemini
- GLM
- DeepSeek

**3. Marketplace** — Multi-provider routers.

- OpenRouter
- Surplus
- Jatevo

**4. Private** — Never leaves the PC.

- Ollama
- Phala
- Founder Vault Memory

**5. Admin Promo** — Always visible.

- ⭐ GLM 5.2 (89 days remaining) — always visible

The Admin Promo section is the only section that is always visible regardless of context. It surfaces a promoted model with a countdown (days remaining on the promotional pricing or availability window). It is the one place Founder OS nudges the founder toward a specific choice — and it is clearly labeled as a promo so the nudge is honest.

### 10.2 The Selected Runtime Display

When a runtime is selected, the dropdown displays:

- **Execution client** — which IDE will execute (e.g. Cursor)
- **AI model** — which model will reason (e.g. GLM 5.2)
- **Local/Cloud** — whether the brain runs locally or in the cloud
- **Connected/Missing key** — whether the brain is ready to use or needs a key
- **Estimated cost** — a rough cost-per-prompt or cost-per-session estimate

This display is the founder's cost dashboard in miniature. At any moment they can see what they are about to spend and on what.

### 10.4 The Section Ordering Rationale

The five sections are ordered to reinforce the mental model:

1. **Execution** comes first because Execution is the foundation — without an IDE there is no work.
2. **Brains** comes second because the brain is the most common customization a founder makes after choosing an IDE.
3. **Marketplace** comes third because marketplaces are for founders who want choice without managing many provider relationships.
4. **Private** comes fourth because private compute is an opt-in for sensitive work, not a default.
5. **Admin Promo** is always visible at the bottom because it is a nudge, not a structural category.

A founder scanning the dropdown top-to-bottom reads it as "where does my work happen → what thinks about it → where do I route if I want choice → what stays private → what's being promoted." That is the right reading order.

### 10.5 The Fallback Rule

If the selected brain's API key is missing, the dropdown shows a "Missing key" badge and the runtime falls back to the connected IDE's built-in AI (e.g. Cursor Auto). The founder is never blocked — they always have a working brain — and they always know they are on the fallback.

---

## 11. Community

"Build in public" is a first-class workflow in Founder OS, not an afterthought. Every project has a Publish panel. The founder should never have to leave Founder OS to draft a build-in-public update.

### 11.1 The Publish Panel

Every workspace has a Publish panel that summarizes the day's work and offers one-click generation of social-ready content.

```
Today's Work
─────────────
12 commits
3 deployments
Authentication finished
2 issues closed
Ready to publish
```

Buttons:
- **Generate X Post** — drafts a post for X
- **Generate LinkedIn** — drafts a longer-form LinkedIn update
- **Generate DevLog** — drafts a developer log entry
- **Publish** — publishes the drafted content to the connected surfaces

### 11.2 Founder Brain Summarization

Founder Brain automatically summarizes today's work into a polished "build in public" update. The summary draws on:
- Commit history for the day (grouped by initiative, not raw commit noise)
- Deployments triggered and their outcomes
- Issues opened and closed
- Active workspace context (what the founder was actually trying to accomplish)

The output is a draft, not a publication. The founder reviews it, edits it, and decides whether to publish. Founder OS never auto-publishes without explicit consent.

### 11.3 Why Community Lives in Founder OS

A founder who has to switch to a separate tool to write their build-in-public update will often skip it. The friction of context-switching kills the habit. By putting the Publish panel inside the workspace, next to the conversation and the events, Founder OS makes "publish what I shipped today" a one-click action that happens in the same flow as the shipping itself.

### 11.4 The Draft-Then-Publish Discipline

Founder Brain generates drafts, never publications. The flow is always:

1. Founder Brain summarizes today's work into a draft.
2. The founder reviews the draft in the Publish panel.
3. The founder edits the draft (tighten the phrasing, drop a commit that isn't worth mentioning, add a personal note).
4. The founder clicks Publish.

Founder OS never auto-publishes. The discipline is deliberate: a build-in-public update that the founder didn't read is a liability, not an asset. An inaccurate or tone-deaf auto-posted update damages the founder's reputation and erodes the trust that build-in-public is supposed to build. The founder's explicit click is the consent gate.

### 11.5 Multi-Surface Publishing

The Publish panel can generate different drafts for different surfaces from the same day's work, because the surfaces have different conventions:

- **X** — short, punchy, one or two posts, link to the devlog
- **LinkedIn** — longer, narrative, professional framing
- **DevLog** — full technical detail, suitable for a blog or docs section

A day that produced "12 commits, 3 deployments, auth finished, 2 issues closed" becomes a 280-character X post, a 3-paragraph LinkedIn update, and a structured devlog entry — all from the same underlying work, all drafted by Founder Brain, all reviewed by the founder before publishing.

---

## 12. Founder Vault

Founder Vault is the founder's private memory. It is the place where the operating system remembers what the founder did, what they learned, and what they intend to do next — and it never leaves the PC.

### 12.1 Founder Memory

Every day ends with a memory entry. The structure:

> **What changed today?**
> - Summary
> - Commits
> - Lessons
> - Ideas
> - Next Tasks

This is not a manual journal. Founder Node gathers the raw material automatically (commits, deploys, agent runs, community drafts) and Founder Brain drafts the entry. The founder reviews and saves it. Over time, the vault accumulates a continuous, honest record of the founder's work.

### 12.2 The Morning Briefing

The next morning, the founder returns to:

> **Welcome back.**
> Yesterday: Builder finished authentication. Marketing drafted launch. Deploy succeeded. One bug remains. Continue?

The morning briefing is the resume experience applied to the founder's whole working life, not just one workspace. It pulls from the vault, from the current state of every workspace, and from any open threads (a remaining bug, a draft waiting to be published, a deployment that needs verification).

### 12.3 Founder Brain Is Context

> Founder Brain is Context — it knows Workspace, Branch, Repository, Community, Infrastructure, Memory, Everything.

Founder Brain is not a separate agent that runs in isolation. It is the reasoning layer that has access to the full context of the founder's working life. When the founder asks Founder Brain a question, the brain sees:
- The active workspace and its conversation
- The active branch and repository
- The community drafts and recent publications
- The infrastructure state (deploys, CI, database health)
- The vault memory (lessons, ideas, next tasks from prior days)
- Everything else Founder OS knows

This is what makes Founder Brain useful in a way that a generic chatbot is not. It answers from the founder's actual context, not from a cold start.

### 12.4 The Private Layer

The vault never leaves the PC. The cloud API sees metadata (vault sync occurred, entry count, timestamps) but never the plaintext contents. When Founder Brain needs to reason over vault contents, the reasoning either runs locally (Ollama) or over encrypted/confidential compute (Phala). The founder's private memory is never uploaded to a third-party LLM provider in plaintext.

### 12.5 Memory Tiers

The vault organizes memory into tiers, each with a different retention and access policy:

- **Ephemeral** — the current session's working context (open files, recent commands, current branch). Lives in memory, rolls off when the session ends.
- **Daily** — the "What changed today?" entries. Saved at the end of each working day, retained indefinitely, indexed for local search.
- **Lessons** — distilled, reusable insights ("the deploy script fails if the env var is missing," "this API rate-limits at 100/min"). Surfaced proactively when the founder hits a related situation.
- **Ideas** — captured sparks for future work. Not actionable yet, but preserved so they are not lost.
- **Next Tasks** — the queue the morning briefing reads from. Each completed task moves to the daily summary; each unfinished task carries forward.

The tiers matter because they define what Founder Brain can reach for when answering a question. A question about "what did I do last week" pulls from Daily. A question about "how did I fix the deploy last time" pulls from Lessons. A question about "what should I build next" pulls from Ideas and Next Tasks. The vault is not a flat notebook; it is a structured memory with retrieval semantics.

### 12.6 The Vault and Founder Brain Are Co-Designed

Founder Vault and Founder Brain are not independent subsystems that happen to talk to each other. They are co-designed: the vault's tiered structure exists because Founder Brain needs structured retrieval, and Founder Brain's context assembly exists because the vault holds the structured memory. A change to the vault schema is a change to what Founder Brain can reason over; a change to Founder Brain's retrieval is a change to which vault tiers are surfaced. Contributors changing either subsystem must consider the other.

---

## 13. Roadmap

The implementation priorities below are the canonical ordering. "DONE" means the feature is shipped and stable. "IN PROGRESS" means active development. "PARTIALLY DONE" means a foundation exists but the feature is not complete. "PLANNED" means the feature is designed but not started. "FUTURE" means the feature is on the long-term roadmap.

1. **Recent Workspaces** — DONE
   The most important screen in the product. The list of workspaces with status, IDE, AI, branch, repository, and last activity.

2. **Per-workspace session persistence** — DONE
   `WorkspaceSession` and `ConnectedWorkspace` models, auto-restore on load, event log hydration, debounced save.

3. **SSE streaming for real conversations** — IN PROGRESS
   `POST /copilot/ask/stream` with events: `context`, `chunk`, `attribution`, `liveSnapshot`, `done`, `error`. Replaces request-response chat with a streaming experience that matches Cursor.

4. **IDE adapter abstraction layer** — PARTIALLY DONE
   The IDE Bridge Interface is defined and Cursor is implemented. Additional IDEs (Claude Code, Windsurf, VS Code) still need adapters. The `/cursor-bridge/recent-agents` endpoint is aliased to `/ide-bridge/recent-agents` in anticipation of the generalization.

5. **Workspace Publish panel** — PLANNED
   The per-workspace "Today's Work" summary with Generate X Post, Generate LinkedIn, Generate DevLog, and Publish buttons.

6. **Desktop-bridge status strip** — PLANNED
   A persistent strip showing desktop online/offline, IDE connected, brain selected, cost today, saved vs. cloud.

7. **Remote IDE control (dispatch protocol)** — FUTURE
   The ability to dispatch an instruction from the browser and have the connected IDE execute it. Requires the dispatch protocol over the desktop bridge.

8. **Multi-IDE support (Claude Code, Windsurf, VS Code)** — FUTURE
   Additional IDE adapters behind the IDE Bridge Interface. Claude Code and Windsurf already have Prisma enums; the credential and adapter work remains. VS Code needs an extension or bridge.

9. **Mobile apps (Android, iOS)** — FUTURE
   Native mobile apps that replicate the browser Remote Control experience with better integration (push notifications, biometric auth, offline cache of the resume screen).

The ordering is deliberate. Recent Workspaces and session persistence come first because continuity is the moat. SSE streaming comes next because the conversation must feel like Cursor, not ChatGPT. The IDE adapter abstraction comes next because it unblocks multi-IDE support without rewriting the browser. Everything else builds on that foundation.

---

## 14. Engineering Rules

These are the rules every contributor follows. They are not aspirations; they are constraints. Violating them is a bug, even if the code compiles.

1. **Never fake progress.** The Live Events panel never shows an event that did not happen. The progress bar never advances without real work behind it.

2. **Never fake AI thinking.** The UI never shows a "thinking..." animation that is not backed by an actual in-flight AI request. If the AI is not thinking, the UI says "Waiting" or "Idle."

3. **Never lose state.** Session state is persisted on every meaningful change. A refresh, a crash, or a device switch never loses conversation, events, terminal, or branch state.

4. **Never reset chats.** The conversation is restored on load, not restarted. There is no "New chat" button that silently discards the prior conversation.

5. **Never invent repository status.** If the desktop is offline, the browser shows "Desktop Offline" and the last known status, not a fabricated "Git: Clean."

6. **Always ask Founder Node.** For any state that lives on the desktop (git, terminal, IDE), the browser asks Founder Node through the desktop bridge. It never guesses.

7. **Always reconnect.** When a connection drops (WebSocket, SSE, desktop bridge), the client reconnects automatically and resumes the stream. The founder should not have to refresh.

8. **Reuse components.** New features reuse existing UI components, hooks, and utilities. Duplicating a button, a panel, or a hook because "this is a different feature" is a bug.

9. **One source of truth.** Each piece of state has exactly one source. Server-side state lives in Prisma/Neon. Client-side state lives in the founder event bus and React state. LocalStorage and SessionStorage are mirrors, not sources.

10. **Everything event-driven.** The Live Events panel, the terminal, the conversation stream, and the desktop bridge are all event-driven. Polling is a last resort, not a default.

11. **Before modifying code: understand the whole system first.** No contributor changes a file without understanding how that file fits into the layer architecture, the session model, and the event flow.

12. **Architectural review before implementation.** Any change that crosses layers, adds a new model, or introduces a new external dependency gets reviewed against this specification before code is written.

13. **Preserve architectural integrity — never break workflows, create contradictory logic, or duplicate systems.** A change that "works in isolation" but breaks the resume flow, the session model, or the event stream is rejected.

14. **Continuously validate — run type checks, linting, builds, verify no regressions.** No PR is merged with failing types, failing lint, or a failing build. CI is the floor, not the ceiling.

15. **Keep the entire stack synchronized — GitHub, Neon, Railway, Vercel.** A schema change migrates Neon, deploys to Railway, and ships to Vercel in the same change. Drift between surfaces is a bug.

16. **Think like a lead architect, not just an implementer.** Every contributor is responsible for the system as a whole, not just the file in front of them.

17. **If a change requires schema or architectural modification, stop and ask before proceeding.** Schema changes, new models, new layers, and new external integrations are not silent changes. They get discussed first.

18. **Maintain production quality — leave the code better than you found it.** Every PR is an opportunity to clean up the surrounding code. A PR that leaves the codebase worse is rejected, even if the new feature works.

### 14.1 How the Rules Interact

The eighteen rules are not independent. They form a coherent stance, and several of them only make sense in relation to each other:

- **Rules 1, 2, and 5** (never fake progress, never fake AI thinking, never invent repository status) together define the **honesty stance**. The product never lies about what it is doing. This is the foundation of founder trust; without it, the resume screen and the Live Events panel are theater.
- **Rules 3, 4, and 7** (never lose state, never reset chats, always reconnect) together define the **continuity stance**. This is the moat from §1.5 made operational. Continuity is not a marketing claim; it is a set of engineering constraints that, taken together, produce the experience of "I never left my desk."
- **Rules 6 and 9** (always ask Founder Node, one source of truth) together define the **truth boundary**. The desktop is the source of truth for desktop state; the server is the source of truth for session state. The browser never invents a third source.
- **Rules 8 and 10** (reuse components, everything event-driven) together define the **engineering discipline**. The codebase stays small and consistent because features reuse existing primitives and communicate through events rather than ad-hoc wiring.
- **Rules 11, 12, 13, and 17** (understand the whole system first, architectural review before implementation, preserve architectural integrity, stop and ask on schema/architectural changes) together define the **governance stance**. No contributor makes a structural change in isolation. The spec is the reference; deviations get reviewed.
- **Rules 14, 15, and 16** (continuously validate, keep the stack synchronized, think like a lead architect) together define the **quality stance**. CI is the floor, stack sync is the wall, and architect-level thinking is the ceiling.
- **Rule 18** (leave the code better than you found it) is the **stewardship stance** — the commitment that the codebase improves over time, not just accumulates.

A contributor who internalizes these clusters does not need to memorize all eighteen rules; they need to hold the six stances. The rules follow.

### 14.2 When Rules Conflict

Occasionally two rules appear to conflict in practice. The resolution order, from highest to lowest priority:

1. **Honesty stance** (Rules 1, 2, 5) — never sacrificed. A feature that requires faking state is wrong, full stop.
2. **Continuity stance** (Rules 3, 4, 7) — never sacrificed for convenience. A feature that loses state to ship faster is wrong.
3. **Truth boundary** (Rules 6, 9) — never sacrificed. A feature that invents a third source of truth is wrong.
4. **Governance stance** (Rules 11, 12, 13, 17) — when in doubt, stop and ask.
5. **Engineering discipline and quality** (Rules 8, 10, 14, 15, 16) — traded only with explicit justification in the PR.
6. **Stewardship** (Rule 18) — the background commitment; never actively violated.

This ordering tells a contributor which rule wins when "ship the feature" and "follow the rule" pull in opposite directions. The answer is always: follow the rule that sits higher in this ordering, and write the justification in the PR.

---

## 15. API Contracts

The Founder OS API (NestJS) exposes the endpoints below. All endpoints require JWT authentication unless noted. All request and response bodies are JSON. SSE endpoints stream `text/event-stream`.

### 15.1 Connected Workspace Endpoints

**`GET /connected-workspace`** — list the authenticated user's connected workspaces.

Response:
```json
{
  "workspaces": [
    {
      "id": "ws_abc",
      "label": "Founder OS redesign",
      "repository": "doxedcryptofounder",
      "branch": "master",
      "ideProvider": "cursor",
      "aiProvider": "cursor-auto",
      "lastActiveAt": "2026-07-01T06:00:00Z"
    }
  ]
}
```

**`POST /connected-workspace`** — create a new connected workspace.

Request:
```json
{
  "label": "Trading Bot",
  "repository": "btc-conservative-agent",
  "branch": "main",
  "ideProvider": "cursor",
  "aiProvider": "glm"
}
```

**`PATCH /connected-workspace/:id`** — update a connected workspace. Partial update; any subset of fields is accepted.

**`DELETE /connected-workspace/:id`** — delete a connected workspace and its session.

### 15.2 Session Endpoints

**`GET /connected-workspace/:id/session`** — get or create the session for the workspace. If a session does not exist, it is created empty. If it exists, it is returned with all persisted fields (conversation, terminalScrollback, openFiles, activeNav, panelState, publishDraft, eventLog).

Response:
```json
{
  "session": {
    "id": "sess_xyz",
    "selectedAiProvider": "glm",
    "selectedModelKey": "glm-5.2",
    "selectedIdeProvider": "cursor",
    "conversation": { "messages": [] },
    "terminalScrollback": { "lines": [] },
    "openFiles": [],
    "activeNav": "recent-workspaces",
    "panelState": {},
    "publishDraft": null,
    "eventLog": []
  }
}
```

**`PUT /connected-workspace/:id/session`** — update the session. Called by the `useDebouncedWorkspaceSessionSave` hook on every meaningful change. Partial update; any subset of fields is accepted.

### 15.3 Copilot Endpoints

**`POST /copilot/ask`** — chat in request-response mode. Used as the fallback when SSE is unavailable.

Request:
```json
{
  "workspaceId": "ws_abc",
  "messages": [],
  "aiProvider": "glm",
  "modelKey": "glm-5.2"
}
```

Response: a single assistant message with attribution.

**`POST /copilot/ask/stream`** — chat in SSE streaming mode. The primary chat endpoint.

SSE events (in order):

| Event | Payload | Purpose |
|-------|---------|---------|
| `context` | `{ workspace, repository, branch, files }` | The context assembled for the prompt |
| `chunk` | `{ delta }` | A streamed token delta from the brain |
| `attribution` | `{ provider, model, local, cost }` | Attribution chip data for the assistant message |
| `liveSnapshot` | `{ events: [] }` | A snapshot of live events captured during generation |
| `done` | `{ messageId }` | The assistant message is complete |
| `error` | `{ message, code }` | An error occurred; the stream ends |

The browser renders `chunk` events as they arrive (the Cursor-feeling streaming experience), attaches the `attribution` chip to the completed message, and merges `liveSnapshot` events into the Live Events panel.

### 15.4 Streaming Protocol Notes

The SSE stream is a sequence of named events. Each event has a type (one of `context`, `chunk`, `attribution`, `liveSnapshot`, `done`, `error`) and a JSON payload. The browser's event-source handler dispatches each event to the right consumer:

- `context` → the context panel expands to show what was assembled
- `chunk` → the conversation view appends the delta to the in-flight assistant message
- `attribution` → the in-flight assistant message gets its attribution chip
- `liveSnapshot` → the Live Events panel merges the snapshot
- `done` → the in-flight assistant message is finalized and the session is saved
- `error` → the in-flight assistant message is marked failed, the stream ends, and the founder is notified

The stream is single-use: one request, one stream, one assistant message. Multi-turn conversations are a sequence of single-message streams. This keeps the protocol simple and the failure surface small — a failed stream affects exactly one message, not the whole conversation.

### 15.5 Error Semantics

Errors are first-class. An `error` event ends the stream and carries a `code` so the browser can render the right recovery affordance:

- `missing-key` → prompt the founder to add the provider key in Settings, offer fallback to IDE brain
- `provider-unreachable` → offer fallback to `POST /copilot/ask` (request-response) or to an alternate brain
- `rate-limited` → offer to retry after a backoff, or switch brains
- `context-too-large` → suggest trimming the conversation or selecting a model with a larger context window
- `internal` → generic error; report to the API for diagnosis

The founder never sees a raw stack trace. They see a one-line summary and a recovery action.

### 15.4 IDE Bridge Endpoints

**`GET /cursor-bridge/recent-agents`** — recent agents from the connected Cursor IDE. Aliased to **`/ide-bridge/recent-agents`** as part of the generalization to multiple IDEs. New code should use the `/ide-bridge/` path; the `/cursor-bridge/` path remains for backward compatibility.

Response:
```json
{
  "agents": [
    {
      "id": "agent_1",
      "title": "Refactor auth flow",
      "status": "running",
      "workspace": "Founder OS redesign",
      "startedAt": "2026-07-01T05:58:00Z"
    }
  ]
}
```

### 15.5 Desktop Bridge Endpoint

**`GET /copilot/desktop-bridge`** — the desktop bridge snapshot. Returns the current state of the paired desktop: online status, connected IDE, active workspace, repository, branch, git status, terminal state, agents, deploy status, community drafts. The browser reads this to render the resume screen.

Response:
```json
{
  "desktop": {
    "online": true,
    "ide": "cursor",
    "workspace": "Founder OS redesign",
    "repository": "doxedcryptofounder",
    "branch": "master",
    "gitStatus": "clean",
    "agents": { "running": 3, "waiting": 0 },
    "deploy": "healthy",
    "communityDrafts": 1
  }
}
```

### 15.6 Founder Node Endpoints

Founder Node itself exposes (and consumes) endpoints for:

- **Pairing** — generate a one-time code, bind the desktop to an account
- **Heartbeat** — 45-second interval; carries desktop metadata
- **Vault sync** — encrypted vault entries pushed as metadata
- **Inference job queue** — pending local inference jobs (Ollama) pulled by Founder Node and executed
- **Sync jobs** — pending sync jobs (e.g. repository refresh, file index) pulled by Founder Node

These endpoints are the control plane between the cloud API and the laptop. They are authenticated with the paired-desktop credential, not the user JWT, so a paired desktop can only act within its own account scope.

---

## 16. Design System

The Founder OS visual language is a dark, dense, professional surface. It is not a marketing site. It is a working tool, and the design reflects that.

### 16.1 Color

- **Background:** dark zinc (the base canvas)
- **Accents:** violet (the active/selected state, the primary action)
- **Status dots:**
  - 🟢 green — running
  - 🟡 yellow — waiting
  - ⚪ gray — completed or dormant

The palette is intentionally restrained. A working tool does not need a rainbow. The violet accent is the only color that calls attention to itself, and it is reserved for the active state so the founder's eye always lands on "where am I."

### 16.2 Typography

Font sizes are intentionally small because the surface is information-dense and the founder is expected to be a power user.

- `text-[8px]` — section headers
- `text-[9px]` — metadata, timestamps, secondary labels
- `text-[10px]` to `text-[11px]` — content, conversation text, event log entries

Larger sizes are reserved for the resume screen greeting and the workspace titles. The default reading surface is small, dense, and scannable.

### 16.3 Sidebar

The left sidebar uses collapsible sections with **uppercase `tracking-wider` headers**. Each section header is a small, all-caps, letter-spaced label. Sections expand and collapse with a chevron. The intent is that a founder can collapse sections they never use and keep the sidebar focused on what they actually need.

### 16.4 Chat

The conversation view is **Cursor-style, not ChatGPT-style**. The distinction:

- **Cursor-style:** restores an existing conversation. The founder arrives in a workspace and sees the conversation they were having, continuing from the last message. There is no "Start a new chat" affordance that discards history.
- **ChatGPT-style:** starts a new thread per topic. Each visit opens a blank thread. History is something you dig for in a sidebar.

Founder OS is Cursor-style because continuity is the moat. The conversation is a property of the workspace, not a property of "a chat."

### 16.5 Attribution Chips

Every AI assistant message carries an attribution chip:

```
🧠 GLM 5.2 · Cloud
```

The chip shows the brain (🧠), the model name, and the locality (Cloud or Local). This makes it always visible which brain produced which message — a critical transparency feature when the founder is comparing providers and costs.

### 16.6 Context Panel

The context panel is a collapsible **"Live scan"** that shows the collection steps the brain took to assemble context for the most recent prompt (which files were read, which recent commits were pulled, which vault entries were referenced). The founder can expand it to see exactly what the brain knew when it answered.

### 16.7 Identity Badge

The identity badge combines a **path label** with a **"build in public"** tag. It identifies the founder's working identity (which repository, which workspace) and their chosen posture (building in public). It is small, persistent, and present on every screen so the founder always knows which context they are operating in.

---

## 17. State Management

State in Founder OS is split across client and server, with strict rules about what lives where.

### 17.1 Client-Side State

- **React state in `dev-workspace.tsx`** — the workspace component holds the active session's working state (the in-memory conversation, the live events for the current session, the panel state, the active nav).
- **`founder-event-bus`** — an in-memory event bus that holds up to 200 events for the current session. It is the live backing store for the Live Events panel. Events roll off once the bus exceeds 200.
- **Event log persistence** — of the 200 in-memory events, the last 50 are persisted to `WorkspaceSession.eventLog` so reload hydrates the panel.

### 17.2 Session Persistence

- **`useDebouncedWorkspaceSessionSave` hook** — debounced save of the `WorkspaceSession`. Fires on every meaningful change to the session (new message, new event, panel toggle, nav change) but debounced so rapid changes do not hammer the API.
- **Per-workspace API when `activeWorkspaceId` is set** — the hook only saves when an active workspace is selected. If no workspace is active, no save fires.

### 17.3 Local Storage

- **`dcf:active-workspace-id`** — the mirror of the active workspace selection. The browser reads this on load to know which workspace to auto-restore. It is a mirror, not a source of truth — the server is the source of truth for the session itself.

### 17.4 Session Storage

- **`dcf-copilot-chat-v1`** — the Mission Control chat. This is legacy session storage that predates the `WorkspaceSession` model. It is **to be unified with `WorkspaceSession`** so that all chat state lives in one server-side source of truth.

### 17.5 Server-Side State

- **Prisma `WorkspaceSession`** — the server-side source of truth for a workspace's session (see §9).
- **Prisma `ConnectedWorkspace`** — the server-side source of truth for a workspace's identity and bindings.
- **Neon (PostgreSQL)** — the database backing Prisma.

The hierarchy is strict: the server is the source of truth, the React state is the working copy, and LocalStorage/SessionStorage are mirrors for bootstrapping. A reload always reads from the server; it never trusts a stale mirror.

### 17.6 The Active-Workspace Contract

The active workspace is the single most important piece of client state. It determines which session is loaded, which events stream, which terminal is shown, and which publish panel is rendered. The contract for active-workspace state:

- The active workspace ID is stored in `localStorage` (`dcf:active-workspace-id`) so a reload restores the same workspace.
- The active workspace ID is mirrored in React state for the duration of the session.
- The `useDebouncedWorkspaceSessionSave` hook only fires when `activeWorkspaceId` is set. With no active workspace, no session is loaded and no save fires — the founder is on the Recent Workspaces list, not in a workspace.
- Switching workspaces is a deliberate action: it saves the outgoing session, loads the incoming session, and updates `localStorage`. It is not a side-effect of navigation.

This contract keeps the workspace boundary crisp. A founder is always either "in a workspace" (with a loaded session and a live event stream) or "browsing workspaces" (with the Recent Workspaces list and no active session). There is no in-between state where half a session is loaded.

### 17.7 The Legacy SessionStorage Migration

`dcf-copilot-chat-v1` in `sessionStorage` is legacy state from the Mission Control chat that predates `WorkspaceSession`. The migration plan is to read from `sessionStorage` on load, write the legacy chat into a `WorkspaceSession` row on first visit, and then delete the `sessionStorage` entry. After migration, the only chat state is server-side. Until the migration is complete, the legacy entry is treated as read-only fallback — the founder's existing Mission Control chat is not lost, but no new state is written to `sessionStorage`.

---

## 18. Failure Modes

Every system fails. Founder OS is designed to fail honestly and recover gracefully. The modes below are documented so contributors know what the expected behavior is in each failure case.

### 18.1 Desktop Offline

The browser shows **"Desktop Offline"** in the top bar and on the resume screen. Workspace metadata from the last session is still available, and the conversation is fully restorable. Live IDE state (current branch, current git status, running agents) is unavailable and labeled as such. The founder can read their prior conversation and draft new messages, but live events do not stream until the desktop returns.

### 18.2 IDE Disconnected

The connected IDE's capabilities are greyed out in the UI. Where an action requires the IDE (e.g. "Send prompt to IDE"), the UI shows **"Connect in Settings"** and links to the IDE configuration. The founder is never shown an actionable control that silently fails.

### 18.3 AI Provider Missing Key

The runtime dropdown shows a **"Missing key"** badge next to the provider. The runtime falls back to the connected IDE's built-in AI (e.g. Cursor Auto) so the founder is never blocked. The founder is notified of the fallback so they know they are not using their selected brain.

### 18.4 SSE Endpoint Unavailable

If `POST /copilot/ask/stream` is unavailable (network failure, API deploy in progress, SSE proxy issue), the client falls back to `POST /copilot/ask` (request-response). The conversation continues to work, but without streaming. The fallback is graceful and automatic; the founder does not need to refresh or take action.

### 18.5 Session Corruption

If a `WorkspaceSession` row contains malformed JSON (a corrupted conversation, an unreadable event log), the browser validates the JSON shapes on load and falls back to an empty session. The founder lands in the workspace with an empty conversation rather than a crash. The corrupted row is reported to the API for diagnosis.

### 18.6 Build Failures

CI blocks deploy. No broken code reaches production. A failing build on `main` does not ship to Vercel or Railway; the deploy is held until the build passes. This is enforced by GitHub Actions and the deploy pipelines, not by contributor discipline.

### 18.7 Connection Loss and Reconnect

When a WebSocket or SSE connection drops, the client reconnects automatically with exponential backoff. During the disconnect window:

- The Live Events panel shows "Reconnecting…" with a yellow indicator.
- The conversation view keeps the last received state; new prompts are queued locally and dispatched on reconnect.
- The top bar's "Desktop Connected" indicator turns yellow ("Reconnecting") rather than red ("Offline") for the first retry window, so a momentary blip does not alarm the founder.

If reconnect fails after the backoff window, the indicators turn red/offline and the founder is given a manual "Retry connection" action. The system never silently hangs on a dead connection.

### 18.8 Partial State on Reload

If the browser reloads in the middle of an in-flight assistant message (a chunked stream interrupted by a refresh), the partial message is not preserved as a complete assistant turn. The session saves the conversation up to the last completed assistant message; the in-flight partial is discarded. The founder sees the conversation end at the last complete turn and can re-dispatch the prompt. This is honest: a half-streamed message is not a real assistant response, and pretending otherwise would violate "never fake progress."

---

## 19. Security

Security in Founder OS is defined as much by what the system does not do as by what it does.

### 19.1 The Network Posture

- **No remote desktop.** Founder Node never exposes a remote desktop protocol.
- **No VPN.** Founder Node does not create a virtual network interface.
- **No port forwarding.** Founder Node opens no inbound ports. It speaks only outbound to the Founder OS API.
- **Secure pairing via code.** A one-time code generated in Founder Node binds the desktop to the account over the existing outbound connection.

This posture is what makes Founder Node deployable on any network — home Wi-Fi, corporate LAN, mobile hotspot — without IT intervention.

### 19.2 The Private Layer

Founder Node's private layer (Founder Vault, Ollama, Phala) is **local-only**. The contents of the vault never leave the PC in plaintext. Ollama prompts never leave the PC. Phala is used when private compute is needed on remote hardware, with confidential-compute guarantees that the host cannot read the prompts.

### 19.3 Authentication and Credentials

- **JWT authentication** for the API. Every endpoint requires a valid JWT unless explicitly noted.
- **Credential encryption key** for stored credentials. API keys for AI providers are encrypted at rest; the plaintext is only ever available to the runtime that needs them.
- **Paired-desktop credential** for Founder Node endpoints. Founder Node authenticates with a credential scoped to its account, separate from the user JWT.

### 19.4 The Transparency Boundary

> No proprietary IDE state manipulation — only what IDEs expose.

Founder OS does not reverse-engineer proprietary IDE internals, scrape IDE state from private databases, or manipulate IDE state through unsupported channels. It uses only the surfaces IDEs expose (extension APIs, file watching, public bridges). This is both a security and a stability boundary: relying on undocumented internals would break on every IDE update.

### 19.5 Source of Truth

> Desktop is source of truth — browser is remote control.

The desktop is always the source of truth for repository, terminal, and IDE state. The browser is a view of that state. This means a compromised browser session cannot fabricate repository state — the worst it can do is dispatch instructions that the desktop validates and executes.

---

## 20. Future IDE Support

The IDE Bridge Interface is the extension point for the entire IDE ecosystem. Adding a new IDE is a matter of writing an adapter that implements whichever capabilities the IDE exposes — no more, no less.

### 20.1 The Extension Point

The IDE Bridge Interface (see §6) defines: Discover workspaces, List recent sessions, Resume a session, Send a prompt, Stream events, Report Git state, Report terminal state.

A new IDE adapter implements a subset of these. The browser's capability detection then enables or disables UI features based on what the adapter supports.

### 20.2 Claude Code

A Prisma enum already exists for Claude Code as an IDE provider. What remains:

- **Credential work** — storing and managing Claude Code credentials securely
- **Adapter work** — implementing the IDE Bridge Interface against Claude Code's terminal-based interaction model

Claude Code is a strong candidate for the second IDE adapter because its terminal-based model maps cleanly to the "Report terminal state" and "Send a prompt" capabilities.

### 20.3 Windsurf

A Prisma enum already exists for Windsurf. What remains:

- **Credential work** — storing Windsurf credentials
- **Adapter work** — implementing the interface against Windsurf's Cascade agent

### 20.4 VS Code

VS Code does not yet have an adapter. The path forward is either:

- A **VS Code extension** that exposes the IDE Bridge Interface over a local HTTP bridge, or
- A **file-watching bridge** in Founder Node that observes VS Code workspaces and reports state

The extension approach is preferred because it enables the full interface (including "Send a prompt"); the file-watching approach is a fallback that enables read-only capabilities.

### 20.5 Capability Adaptation

> Founder OS adapts UI to available capabilities — enables/disables features based on what each IDE supports.

The principle is restated because it is the key to multi-IDE support without browser sprawl. The browser does not have a "Cursor mode," a "Claude Code mode," and a "Windsurf mode." It has one mode that renders whatever capabilities the connected IDE exposes. A new IDE does not require a new browser surface; it requires only an adapter, and the existing surface adapts.

---

## 21. Onboarding Flow

The onboarding flow is the founder's first experience of Founder OS. It is five steps, ordered to establish the mental model before introducing any choices.

### 21.1 The Five Steps

**1. Connect IDE**

Options: Cursor (Recommended), Claude Code, OpenHands, Skip.

> "Founder OS controls your IDE remotely."

Cursor is recommended because it is the flagship integration with the most complete adapter. Claude Code and OpenHands are offered as alternatives. Skip is offered because forcing a choice is worse than letting the founder explore first.

**2. Pair Founder Node**

Steps: Install Founder Node → Generate Code → Pair Desktop → Done.

> "Founder Node securely connects your computer. No remote desktop. No VPN. No port forwarding."

The pairing step is where the founder installs the desktop runtime and binds it to their account. The security posture is stated up front so the founder knows what they are not installing (no VPN, no port forwarding, no remote desktop).

**3. Synchronize**

Founder OS scans the desktop and surfaces:

- Current workspaces
- Recent sessions
- Repositories
- Branches
- Agents
- Terminal

The synchronize step is the first moment the founder sees the value: their existing working life, reflected back at them in Founder OS, without any manual entry.

**4. Choose Brain**

Options:

- **Use IDE Brain (Recommended)** — Cursor Auto, no API key needed
- **Use External Brain** — expands to: GPT, Claude, GLM, DeepSeek, Gemini, OpenRouter, Surplus, Ollama

> "The mental model is simple: connect your development environment first, then optionally customize the AI."

The IDE Brain is recommended because it is the zero-config path and because the founder already pays for it. External brains are offered for founders who want to swap in a cheaper, local, or specialized model.

**5. Community**

Option: Enable Build in Public.

The final step asks whether the founder wants to build in public. Enabling it turns on the Publish panel for every workspace and connects the community surfaces (X, LinkedIn, Discord, Founder Feed). Disabling it keeps the founder's work private.

### 21.2 Why This Order

The order is ideological. IDE first, because Execution is the foundation — without an IDE there is no work to resume. Founder Node second, because the desktop bridge is what makes the laptop the compute. Synchronize third, because the founder needs to see their existing life reflected before they make new choices. Brain fourth, because the brain is the most interchangeable component and should not be the first thing the founder worries about. Community last, because building in public is a posture the founder opts into after the core loop works.

### 21.3 Skip Is Always Valid

Every step offers a Skip affordance. A founder can skip IDE selection, skip pairing, skip synchronization, skip brain choice, and skip community. Skipping does not block entry to the product — it lands the founder in a reduced surface where the skipped capabilities are greyed out with "Connect in Settings" prompts. The founder can complete onboarding later, in their own time, from Settings.

This is deliberate. Forced onboarding is friction; a founder who wants to look around before committing should be able to. The product earns its right to be configured by being useful in its default state, not by gating the door.

### 21.4 Onboarding Is Re-Entrant

Onboarding is not a one-time wizard. A founder who skipped pairing on day one and returns on day three to pair their desktop can resume the onboarding flow at the pairing step. The flow tracks which steps are complete and which are pending, and the founder can complete them in any order (with the natural dependency that IDE selection must precede synchronization, since there is nothing to synchronize without an IDE).

---

## 22. Cost Management

Cost is a first-class surface in Founder OS. The founder should always know what they are spending and what they are saving.

### 22.1 Cost Always Visible

> Today: $0.41. Saved: $27.90.

A persistent cost readout is shown in the desktop-bridge status strip. It reports:
- **Today** — what the founder has spent on cloud AI today
- **Saved** — what the founder has saved versus a reference cloud-only baseline

The saved figure is the motivational surface. It quantifies the value of "the laptop is the compute."

### 22.2 Celebrate Money Saved

> Founder OS should celebrate money saved.

When the founder completes a session on a local model or a cheap provider, Founder OS surfaces the savings — not as a notification to dismiss, but as part of the resume screen and the morning briefing. Saving money is treated as an achievement, because it is the core value proposition.

### 22.3 Local Models Are $0

When the brain is **Ollama** (or another local model), the cost per prompt is $0. The founder's laptop does the work. The cost readout reflects this: a session on Ollama adds nothing to "Today" and adds the equivalent cloud cost to "Saved."

### 22.4 Founder Node Does the Heavy Lifting

When the brain runs through Founder Node — whether local (Ollama) or cloud (GLM, DeepSeek via the laptop's outbound connection) — the laptop is doing the orchestration. There is no Founder OS cloud compute charge for the orchestration itself. The founder pays the brain provider (if any) and nothing else.

### 22.5 Cloud AI Only When It Creates Real Value

The corollary: cloud AI is used **only when it creates real value**. A frontier model is worth paying for when the task genuinely benefits (complex reasoning, large-context synthesis, specialized capabilities). It is not worth paying for when the task is mechanical (file reading, simple refactoring, git operations). Founder OS's cost philosophy is to default to the cheap path and escalate to the cloud only when the founder decides the value justifies it.

### 22.6 The Reference Baseline

The "Saved" figure is computed against a reference baseline: the cost of running the same work on a default cloud-only stack (a frontier cloud coding agent at standard per-seat pricing plus token usage). The baseline is conservative and clearly documented in the cost-readout tooltip so the founder can see what they are being compared against. The saved number is not a marketing fabrication; it is a defensible comparison the founder can audit.

### 22.7 Cost as a First-Class Metric

Cost is treated as a first-class product metric alongside continuity. A feature that increases cost without increasing value is rejected at the design stage, not just at the review stage. A feature that decreases cost while preserving value is celebrated. The product's incentive structure is aligned with the founder's incentive structure: both want more value per dollar, not more dollars spent.

This is the economic expression of the mission. "Let founders validate ideas, prototype products, and grow communities using their own hardware first, spending on cloud AI only when it creates real value" is not a tagline stitched onto the end of the spec; it is a constraint that shows up in the cost readout, in the runtime dropdown, in the onboarding flow, and in the engineering rules. Every contributor who adds a feature that consumes cloud AI should be able to answer the question "does this create real value for the founder, and is the founder going to see the cost it adds?" If the answer to either is no, the feature is wrong.

---

## Appendix A — Glossary

| Term | Definition |
|------|------------|
| **Founder OS** | The remote operating system for modern builders. The browser-based Remote Control and the cloud API. |
| **Founder Node** | The desktop runtime. The Electron tray app that watches the laptop and bridges to the API. |
| **Founder Bridge** | The cloud-side endpoint that receives desktop bridge metadata and serves it to the browser. |
| **Founder Vault** | The founder's private, local-only memory store. Never leaves the PC in plaintext. |
| **Founder Brain** | The reasoning layer with access to the full founder context (workspace, vault, infrastructure, community). |
| **WorkspaceSession** | The Prisma model for a workspace's session state (conversation, events, terminal, panels). |
| **ConnectedWorkspace** | The Prisma model for a workspace's identity and bindings (label, repository, branch, IDE, AI). |
| **IDE Bridge Interface** | The capabilities contract every IDE adapter implements. |
| **Execution layer** | Layer 1 — where work happens (IDEs, terminal, git). |
| **Brain layer** | Layer 2 — how to think (Cursor Auto, external brains). |
| **Private layer** | Layer 3 — what never leaves the PC (Vault, Ollama, Phala). |
| **Live Events panel** | The right-hand panel showing a real-time, honest stream of desktop events. |
| **Recent Workspaces** | The most important screen in the product — the list of workspaces to resume. |
| **Resume screen** | The first screen on open — the dense, instant summary of where the founder left off. |

---

## Appendix B — Reference Documents

This specification is the canonical source of truth. The following documents provide deeper detail on specific subsystems and predate this specification. Where they conflict with this document, this document wins.

- `docs/ARCHITECTURE.md` — system architecture detail
- `docs/MISSION.md` — platform mission and culture
- `docs/FOUNDER_OS_NORTH_STAR.md` — north-star product framing
- `docs/FOUNDER_OS_COMMAND_CENTER_ARCHITECTURE.md` — command-center architecture
- `docs/FOUNDER_NODE_V2.md` — Founder Node v2 detail
- `docs/FOUNDER_VAULT.md` — vault memory tiers
- `docs/PRIVACY_STACK.md` — five-step privacy architecture
- `docs/BYO_AI.md` — bring-your-own-AI provider details
- `docs/CURSOR_REMOTE_RESUME_FEASIBILITY.md` — Cursor remote-resume feasibility analysis
- `docs/REPOSITORY_LAYOUT.md` — repository public/private layout

---

## Appendix C — How to Use This Specification

**Before proposing a feature.** Read the relevant section. If the feature does not advance the vision in §1, do not propose it. If it conflicts with the engineering rules in §14, do not propose it.

**Before implementing a feature.** Read the relevant section and the engineering rules. If the implementation requires schema or architectural modification, stop and ask (Rule 17). If it crosses layers, get an architectural review (Rule 12).

**Before reviewing a PR.** Check the change against the engineering rules. A PR that fakes progress, fakes thinking, loses state, resets chats, or invents repository status is rejected on principle regardless of whether the tests pass.

**When this document is wrong.** Update it. This specification is the source of truth, not a fossil. When the product evolves, the specification evolves with it. A change to this document is an architectural change and should be treated with the same care as a schema migration.

---

*End of specification.*
