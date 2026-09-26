# Founder IDE — architecture audit and ASAP plan

Read-only audit of the Founder IDE / Nucleus / Founder Node surface in this monorepo (2026-09-26). No trading-path changes. This is the execution doc for Danish and the Founder IDE bot.

**Headline:** Founder IDE is a **desktop Void fork plus a web remote-control page**, not an in-browser coding IDE. **Nucleus does not exist** in the repo (zero matches for `nucleus`). The closest graph is a JSON chain of initiative → commit → PR → deploy with **no visual UI and no click → LLM**. The extension chat provider sets `toolCalling: false`, so the registered file/terminal tools are not part of the model loop.

---

## 1. Current architecture map

```text
Browser (apps/web)
  /founder-ide          remote pair + prompt dispatch (NOT an editor)
  /founder-ide/byok     localStorage stub ("set" flag only)
  /founder-ide/decisions  flight-recorder viewer
  /account?tab=connected  GitHub, infra, builder providers
        │ JWT via Next /api proxy
        ▼
apps/api (Nest, prefix /api) on Railway
  Founder Node pairing + heartbeat     /founder-node/*
  IDE dispatch queue                   /ide-bridge/sessions/:id/dispatch
  OpenAI-compat gateway                /v1/chat/completions   (FounderNodeGuard)
  Copilot / memory graph / founder graph  /copilot/*
  Builder BYOK (encrypted)             /builder/providers/*
  Admin provider registry              /ai-routing/*   (platform sections, not the IDE picker)
  Memory context for the extension     /memory/context
        │ fos_{nodeId}:{nodeToken}
        ▼
apps/founder-node (Electron tray)
  ~/FounderVault  (files stay local; cloud gets metadata + encrypted relay)
  Ollama job runner (127.0.0.1:11434) — cloud cannot call localhost
  IDE discovery + clipboard/SendKeys dispatch into Cursor
  Writes OpenAI base URL + bearer into IDE settings (connect-ide)
        │
        ▼
Editor
  packages/founder-ide     Void fork build scripts + gateway rewire overlay
                           (Void source is NOT vendored; built on a Windows machine)
  packages/founder-ide-extension   VS Code chat provider for Cursor / VS Code / Windsurf
```

### How “Nucleus” relates to the editor, agents, and LLMs today

It does not. There is no concept map, no node click handler, and no graph component that opens a model.

What exists instead:

| Artifact | What it actually is | Wired to a model? |
|---|---|---|
| `FounderMemoryGraph` in `packages/utils/src/founder-memory-graph.ts` | One JSON object: goal, sprint, task, blocker, branch, PR. Stored on `FounderBuilderSettings.memoryGraph`. | Copilot reads it. Not a graph UI. |
| `FounderGraph` in `packages/utils/src/founder-graph.ts` | Nodes typed `initiative \| task \| commit \| pr \| deploy \| founder_update \| decision \| agent_run \| vault_doc`. Built server-side. `GET /copilot/founder-graph`. | `formatFounderGraphForPrompt` can inject text into a prompt. `fetchFounderGraph()` in `apps/web/src/lib/api.ts` has **no caller**. |
| Founder economics knowledge lineage | Parent/child list, `apps/web/src/components/founder-economics/knowledge-graph-viz.tsx`. | Unrelated to the IDE. |
| Discover universe map | Project bubbles → `/project/[slug]`. | Not an LLM. |

### Data flow that *is* live

1. User signs in on `/founder-ide`.
2. `FounderIdePair` calls `POST /founder-node/pairing-code`. Tray app completes `POST /founder-node/pair`. Page polls `GET /founder-node/status`.
3. `FounderIdeChat` lists IDE sessions (`GET /ide-bridge/sessions`) and posts a prompt with `ideProvider: 'founder-ide'` (`POST /ide-bridge/sessions/:id/dispatch`).
4. Founder Node polls pending dispatches and delivers them into the desktop IDE (clipboard / session relay). The browser never edits a file.
5. Optional: `POST /second-brain/critique` critiques the last assistant reply.
6. Separately, the **extension or Void rewire** calls `POST /api/v1/chat/completions` with the node bearer. Routing Engine v2 picks a cloud model alias (`founder-os-auto|code|reasoning|fast`). Local Ollama is a **queued job** on the node, not a model the chat provider selects.

### Stranded product (built, not routed)

These components are not imported by any page under `apps/web/src/app`:

- `apps/web/src/components/founder-workspace.tsx`
- `apps/web/src/components/minimal-dev-workspace.tsx`
- `apps/web/src/components/founder-copilot-chat.tsx`
- `apps/web/src/components/settings/founder-node-hub-panel.tsx`

`/settings/builder` is gone. `/settings/integrations` redirects to `/founder-ide` (`apps/web/src/app/settings/integrations/page.tsx`). Founder Node’s README still tells people to open Settings → Builder.

### Three LLM catalogs that disagree

| Catalog | Path | Who it serves |
|---|---|---|
| `AI_PROVIDERS` | `packages/utils/src/ai-providers.ts` | Builder / Copilot: OpenAI, Anthropic, Gemini, DeepSeek, GLM, OpenRouter, Jatevo, Surplus, Ollama, Phala, OpenHands, Cursor. No xAI, Groq, Mistral, LM Studio. |
| `PROVIDER_SEEDS` | `apps/api/src/ai-routing/ai-routing.constants.ts` | Admin section routing (copilot, quick build, wall, …). Includes a `xiaomi` seed whose `baseUrl` is `https://api.x.ai/v1` (wrong host). `ollama` seed points at `localhost:11434`, which the API process cannot reach. |
| BYOK page list | `apps/web/src/app/founder-ide/byok/page.tsx` | UI-only: OpenAI, Anthropic, DeepSeek, GLM, Google, Groq, Mistral, xAI, Cohere, Together. Save writes `localStorage['dcf.founder-ide.byok.'+provider] = 'set'`. The key is discarded. |

---

## 2. Feature inventory

Quality scale: **real** (works if the rest of the stack is up), **partial** (code exists, loop is broken or UI is unreachable), **stub**, **absent**.

| Feature | Present? | Quality | End-to-end? | Evidence |
|---|---|---|---|---|
| **Nucleus** (visual concept map, click node → any LLM) | No | absent | No | Repo-wide search: no `nucleus`. No react-flow / cytoscape / force-graph dependency. |
| **Founder graph API** | Yes | partial | No UI | `packages/utils/src/founder-graph.ts`, `GET /copilot/founder-graph`, unused `fetchFounderGraph`. |
| **Code editor** | Desktop only | real shell, our AI is a rewire | Editor yes; our agent loop no | Void fork scripts in `packages/founder-ide/`. Web has no Monaco/CodeMirror. `apps/web/src/app/founder-ide/page.tsx` calls itself “the remote-control surface”. |
| **Inline edit, diffs, terminal, autocomplete** | Inherited from Void | real in upstream Void | Only if the Windows build is installed | Mapping in `packages/founder-ide/README.md`: FIM → `founder-os-fast`, Ctrl+K → `founder-os-code`, chat → `founder-os-auto`, agent chat → `founder-os-reasoning`. |
| **Extension chat** | Yes | partial | Text chat yes; tools no | `packages/founder-ide-extension`. `chat-provider.ts` sets `capabilities.toolCalling: false`. Participant in `chat-participant.ts` streams one gateway call and returns. |
| **Edit / read / terminal tools** | Registered | partial | Not called by our chat | `package.json` `languageModelTools`: `founder.editFile`, `founder.readWorkspace`, `founder.runCommand`. README still says tool use is “Phase 3”. |
| **Founder Node local AI** | Yes | partial | Ollama jobs yes; not a coding agent | Tray app. `ollamaEnabled` on `FounderNode`. Cloud queues `FounderNodeInferenceJob`; node runs `/api/chat` on localhost. No LM Studio, llama.cpp, or vLLM adapter. Node is a relay, not an agent loop. |
| **Vault / data layer** | Yes | real for metadata + local files | Yes for pairing/sync | Neon models: `FounderNode`, `FounderBuilderSettings` (`memoryGraph`, `founderGraph`), `IntegrationCredential`, `WorkspaceSession`, `ConnectedWorkspace`, `FounderNodeVaultRelay`, `FounderNodeInferenceJob`. Files in `~/FounderVault`. Memory Engine `GET /api/memory/context` is a separate, thin store. |
| **Browser connections (accounts)** | Yes | real | Yes on Account → Connected | `connected-accounts-panel.tsx` → GitHub, Vercel, Railway, Neon, and builder providers. IDE page itself does not host this hub. |
| **Browser agent (computer use)** | Yes, separate product | partial | LAM only | `apps/api/src/lam/computer-use.adapter.ts` (`runAgentLoop`, Anthropic computer-use beta). Not connected to Founder IDE or Nucleus. |
| **Founders dashboard** | Several different things | real for directory; partial for OS | Split | Public `/founders` directory. `FounderOsDashboard` via `/founder-os/dashboard` on the account connected tab. `FounderWorkspace` (the IDE-like dashboard) is unmounted. |
| **Settings** | Split and partly retired | partial | Pairing yes; IDE settings no | Live: `/founder-ide` pair/disconnect, `/account?tab=connected`, `/account?tab=security`, `/settings/ai-usage`. BYOK page is a stub. Node README still points at `/settings/builder`. |
| **Help** | Scattered | partial | No single help route | `/founder-ide/local`, `/founder-ide/byok` (stub copy), `platform-setup-guide.tsx` (lives in the unmounted hub), integration connect guides, legal pages. No `/help`. |
| **Multi-LLM routing** | Yes, three layers | partial | Cloud aliases yes; “any free/OSS model” no | Gateway aliases in `packages/utils/src/ai-proxy.ts`. User picks a **tier**, not a model or a local runtime. Admin registry is not the IDE picker. |
| **Bot / agent fleet (Grok-bot style)** | Workforce templates + dispatch | partial | Prompt templates yes; fleet control no | `WORKFORCE_TEMPLATES` in `packages/utils/src/founder-agents.ts`. `/agents` deep-links into Copilot. Permissions: `build_queue`, `github_issues`, `cursor_agent`, `community_draft`, `raise_room`. No create/assign/chat/tool UI per bot. `recent-agents-panel.tsx` is unmounted. |
| **Second Brain** | Yes on the IDE page | real as a critique button | Yes | `founder-ide-chat.tsx` → `/second-brain/critique`. Not a coding agent. |
| **Cursor / OpenHands as workers** | Yes | partial | Remote dispatch | `packages/utils/src/task-router.ts` prefers Cursor, then OpenHands, then Founder Node. This routes **build tasks**, it does not make Founder IDE the editor. |

---

## 3. KEEP / HARDWIRE / REMOVE / DEFER

### KEEP (required for a shipping coding IDE)

- **Void as the editor.** Do not build Monaco in the browser. Void already has the file tree, tabs, terminal, diff review, Ctrl+K, and chat sidebar. Our job is the rewire plus Nucleus.
- **Founder Node** as the local hinge: pairing, `~/FounderVault`, Ollama (and later any OpenAI-compatible local server), gateway bearer `fos_{nodeId}:{nodeToken}`.
- **`/api/v1/chat/completions`** as the single cloud gateway. Extension and Void overlay should keep speaking OpenAI-compatible SSE.
- **Encrypted Builder credentials** (`IntegrationCredential` + `/builder/providers/connect`). This is the real BYOK. `docs/BYO_AI.md` matches the code. `docs/PRODUCT.md` saying BYOK is dead does not.
- **`FounderGraph` + `FounderMemoryGraph`** as the first Nucleus nodes (mission, commits, PRs, decisions). Extend them; do not replace them with a second memory product.
- **Extension tools** `founder.readWorkspace`, `founder.editFile`, `founder.runCommand` as the first agent tool set.
- **Flight recorder** (`/founder-ide/decisions`) so a route can be inspected after a bad answer.
- **Account → Connected** for GitHub and hosts. The IDE needs the repo; it does not need a new OAuth stack.

### HARDWIRE (exists but does not complete the loop)

- **Nucleus click → selected LLM.** New UI. Seed it from `buildFounderGraph` plus a local file/symbol scan on the node. P0.
- **`toolCalling: true`** in `packages/founder-ide-extension/src/chat-provider.ts`, and a real tool loop in the participant (the current handler never issues a tool call).
- **One user-scoped provider registry** the IDE dropdown reads. Today the user cannot point chat at an arbitrary free/OSS endpoint or at LM Studio.
- **Context packet.** `GET /api/memory/context` returns memory-store snippets keyed by workspace folder name. It does not include the founder graph, open files, or the clicked node. `packages/founder-ide-extension/src/memory.ts` must send that packet.
- **Settings that match the binary.** Pairing, provider list, and “which local server” must live where the README and the IDE send people (`/founder-ide` or an IDE webview), not a retired `/settings/builder`.
- **Dispatch must land in Founder IDE chat**, not only a clipboard paste aimed at Cursor. Confirm the node path for `ideProvider: 'founder-ide'` before calling the remote page “drive your IDE”.

### REMOVE (or stop advertising)

- **`/founder-ide/byok` localStorage stub.** It tells the user keys are vaulted and then stores the string `set`. Either delete the page or make it call `/builder/providers/connect` and never touch `localStorage` with key material.
- **The xiaomi seed base URL** `https://api.x.ai/v1` in `ai-routing.constants.ts`. Fix or drop before any registry UI copies it.
- **Do not mount the whole stranded `FounderWorkspace` as the IDE.** It is a second product (raise room, social, copilot modes). Extract provider-connect and session list if still needed, then leave the rest unrouted until a later cleanup PR. Do not delete it in the P0 slice; it is large and easy to break.
- **Do not revive the VSCodium 0.8 path** under `packages/founder-ide/legacy/`.
- **Stop calling the web page a coding environment** in product copy until Nucleus + apply loop exist. The page is a remote.

### DEFER

- Phala TEE as the default coding path. Purpose-gated unwrap in `packages/utils/src/secrets-storage.ts` blocks using a Phala key for general IDE tools. Keep it as an optional private provider.
- LAM computer-use inside the coding loop.
- MCP marketplace, full repo semantic index, checkpoints/time-travel, background cloud agents.
- In-browser editor.
- Per-agent billing redesign. DDollar metering can stay as-is for gateway calls.
- Trading / Agent Hub. Different product. Do not share PRs with `services/btc-conservative-agent`.

---

## 4. Gap vs Cursor, Codex, and Claude Code

Honest bar: Danish should be able to open this repo, see how a concept connects, ask a chosen model about that node, and accept a reviewed diff. That is “good enough to live in”, not leaderboard parity.

| Capability | Cursor / Codex / Claude Code | Founder IDE today | Minimum to feel as good for daily work |
|---|---|---|---|
| Editor | Native, fast, multi-file | Void fork (if 0.9.1 is installed) | Ship the existing Void build; do not rewrite |
| Agent loop | Edit → test → observe until done | One streamed completion; `toolCalling: false` | 8–12 step loop with read / patch / terminal |
| Diffs and apply | Reviewable multi-file diffs | `founder.editFile` is exact `oldText` replace, confirm dialog, not a diff editor | Unified diff, accept/reject per hunk, via Void’s diff UI |
| Terminal | First-class, model can run tests | Tool exists, chat does not call it | Model runs `npm test` / typecheck and reads the output |
| Context | Index, @files, rules, MCP | Folder-name memory snippets; graph unused | Clicked Nucleus node + neighbors + open file excerpts |
| Model choice | Many models, local in some tools | Four gateway aliases | Dropdown: local OpenAI-compat server **or** any configured cloud model |
| Multi-file edit | Yes | String replace on one path | Patch format, several files per turn |
| MCP | Yes | No | Defer. Three local tools cover the first loop |
| Checkpoints | Yes | No | Defer. Git is the checkpoint until P2 |
| Where we can win | — | Local models, vault, founder memory, **Nucleus as the navigator** | Nucleus is the only feature they do not have in this form |

**Where Founder IDE wins if we build it:** the map is the navigation and the context. Cursor’s chat is a sidebar next to files. Nucleus should be the thing you click to decide *what the model is allowed to see*.

**Where it loses today:** the agent loop, apply UX, terminal, multi-file edit, MCP, and repo context. The web app cannot close that gap. The extension and the Void rewire can.

---

## 5. ASAP delivery plan

Effort is relative (S / M / L) against this repo, not a calendar. Order is the order to merge. Each slice is its own PR. None of them touch BTC services.

### P0 — usable loop (do these first)

**Milestone:** In Founder IDE, a graph renders, a click sends that node’s context to a model the user picked (including a local OpenAI-compatible server), and the model can read a file and propose an edit the user accepts.

| Slice | What | Effort | Done when |
|---|---|---|---|
| **P0-1 Provider registry the IDE can read** | New user-scoped list, not the admin `AiRoutingProvider` table. Fields: `id`, `label`, `kind` (`openai_compat` \| `anthropic`), `baseUrl`, `model`, `secretRef` (Builder credential id or `local`), `capabilities` (`chat`, `tools`). Seed from `AI_PROVIDERS` plus explicit local presets: Ollama `http://127.0.0.1:11434/v1`, LM Studio `http://127.0.0.1:1234/v1`. Endpoint: `GET/POST /api/ide/providers` (JWT for the web, node bearer for the extension). Local URLs are probed **by Founder Node**, never by Railway. | M | Dropdown in the extension lists at least one cloud alias and one local model after the node probes it. |
| **P0-2 Nucleus graph v0 + click → chat** | Webview in the extension (and later a Void sidebar). Data: `GET /copilot/founder-graph` when JWT is available, plus a **local** scan the node already can do: open files from the desktop bridge, headings in `docs/`, top-level folders. Click builds a `ContextPacket` `{ nodeId, label, type, neighborLabels, excerpts }` and starts a chat whose system prompt is that packet. Render with a small canvas (custom SVG or `@xyflow/react` inside the webview). Do not block on a perfect layout. | M | Click the current task node, ask “what should change?”, answer cites that node. |
| **P0-3 Turn tools on** | Set `toolCalling: true`. In `chat-participant.ts`, if the model returns a tool call, invoke `founder.readWorkspace` / `founder.editFile` / `founder.runCommand` and send the result back (cap 8 rounds). Edit tool should emit a unified diff and use Void/VS Code’s diff preview instead of silent `applyEdit` when possible. | M | “Add a comment to `packages/utils/src/founder-graph.ts`” produces a reviewable edit. |
| **P0-4 Kill the fake BYOK** | Remove the localStorage save or replace the page with a link to the real provider form from P0-1. | S | No code path writes API keys to `localStorage`. |

P0-2 is the Nucleus requirement. P0-1 and P0-3 are what make the click useful. Ship P0-1 and P0-2 even if P0-3 slips a PR.

### P1 — feels like a coding IDE

| Slice | What | Effort |
|---|---|---|
| **P1-1 Local coding agent** | A small loop hosted in Founder Node (or the extension host): same three tools, model from the registry, default to a small local model for routing and a stronger model for edits. Stop conditions: tests passed, user rejected, or step cap. | L |
| **P1-2 Nucleus as agent context** | Every agent turn receives the focused node + 1-hop neighbors, not the whole repo. Selecting a node changes the agent’s scope. | M |
| **P1-3 Repo scan nodes** | On the machine: parse import/export and markdown headings into graph nodes (`file`, `symbol`, `doc`). Store the index in `~/FounderVault/nucleus.json`. Sync **node ids and labels** to `founderGraph`, not file bodies. | L |
| **P1-4 Settings and help inside the IDE** | One webview: providers, pairing status, “how Nucleus works”. Point `/founder-ide` and the Node README at it. Retire the claim that Settings → Builder is the setup path. | M |
| **P1-5 Dispatch proof** | Integration test or smoke script: web dispatch with `founder-ide` creates a visible chat turn in the editor, not only a clipboard paste. | S |

### P2 — fleet and parity extras

| Slice | What | Effort |
|---|---|---|
| **P2-1 Agent fleet** | Table `IdeAgent`: name, providerId, tool allow-list, nucleus scope, system prompt. UI: create, assign to a workspace, open a chat. Presets copied from `WORKFORCE_TEMPLATES` (Builder, Researcher). This is the Grok-bot shape: several named agents, each with a thread and tools, not one hidden Copilot. | L |
| **P2-2 MCP** | One MCP client in the extension. Nucleus node type `tool_server`. Defer until P0 tools feel solid. | L |
| **P2-3 Checkpoints** | Snapshot `git stash`-style before an agent apply. | M |
| **P2-4 Free-model catalog** | Curated OpenRouter/Groq free-tier entries in the registry, still user-keyed. Do not bake platform keys into the IDE for arbitrary OSS hosts. | S |

### First PR to open (when implementation starts)

**PR A — `cursor/ide-provider-registry-*`:** user provider model + `GET/POST /api/ide/providers` + extension dropdown. No UI graph yet. No BTC files.

**PR B — `cursor/nucleus-webview-*`:** extension webview, render `FounderGraph` JSON, click copies a context packet into the chat participant. This is the Nucleus P0.

**PR C — `cursor/ide-tool-loop-*`:** `toolCalling: true` + 8-step loop + diff preview.

Do not combine A+B+C. Do not “mount FounderWorkspace” as a shortcut.

---

## 6. Recommended architecture

Free rein, constrained by what is already true: the editor is Void, the cloud cannot see `localhost`, secrets already have a vault, and the graph types already exist.

### 6.1 Multi-LLM provider registry

One registry, two probe sites.

```text
IdeProvider
  id, userId, label
  kind: openai_compat | anthropic
  baseUrl, model
  secretRef → IntegrationCredential.id | "none" (local, no key)
  placement: cloud | local
  capabilities: chat, tools, fim
  probedAt, probeOk
```

- **Cloud rows** are called by `AiProxyRuntimeService` (existing gateway). Add a pass-through mode: alias `founder-os-auto` keeps today’s router; a specific provider id skips the alias and calls that base URL with the user’s key.
- **Local rows** are called by Founder Node or the extension directly (`http://127.0.0.1:11434/v1`, LM Studio `:1234`, any other OpenAI-compatible server). The gateway only stores the row and the last probe result the node reported. Railway must never fetch `127.0.0.1`.
- **Free / OSS cloud** (Groq free tier, OpenRouter free models, Together, Gemini free) are just `openai_compat` rows with the user’s key. Do not special-case ten vendors in the web stub.
- **Anthropic** stays a second adapter (already in `PROVIDER_SEEDS`). Everything else goes through OpenAI-compat.
- xAI/Grok is an OpenAI-compat row (`https://api.x.ai/v1`) when the user adds a key. It is not in `AI_PROVIDERS` today; the registry makes that a data row, not a code change per vendor.
- Phala stays optional and purpose-gated. Do not unwrap Phala secrets for local tools.

The extension dropdown shows providers, not only the four aliases. Aliases remain as “auto route on the platform pool”.

### 6.2 Small local coding agents

Run the loop **on the machine**, in the extension host (first) and move it into Founder Node only if the editor is closed and a web dispatch must continue.

```text
loop(packet, provider, tools, maxSteps=8):
  messages = [system(packet), user]
  repeat:
    reply = provider.chat(messages, tools)
    if reply.toolCalls is empty: return reply
    for call in reply.toolCalls:
      result = tools[call.name](call.args)   # read | patch | terminal | nucleus.expand
      messages.append(result)
  stop on user reject, step cap, or test command exit 0
```

Default split:

- **Router model:** small local (Ollama `llama3.2` or whatever the probe found) to decide which node and which tool.
- **Editor model:** the user’s chosen stronger model (cloud or a larger local) for the actual patch.

Tools stay the three that already exist, plus `nucleus.expand(nodeId)` which returns neighbor labels and excerpts from `~/FounderVault/nucleus.json`. No new tool framework in P0.

### 6.3 Bot / agent fleet (Grok-bot shape)

Grok-bot style here means: a list of named agents, each with a model, a tool allow-list, a scope, and a chat thread. It does **not** mean the trading Agent Hub.

```text
IdeAgent
  id, userId, name
  providerId → IdeProvider
  tools: subset of read | patch | terminal | nucleus.expand
  scopeNodeId → Nucleus node (nullable = whole workspace)
  systemPrompt
  presetKey: BUILDER | RESEARCHER | custom   # from WORKFORCE_TEMPLATES
```

UI (extension webview, not a new marketing page):

- Create agent from a preset.
- Assign it to the current workspace.
- Chat with that agent only (its thread, its tools, its Nucleus scope).
- A “fleet” strip shows who is idle vs mid-loop.

Keep `executeWorkforceRuntime` for the existing Copilot marketing flow. New IDE agents should not go through that hidden orchestrator. One visible agent, one thread.

### 6.4 Nucleus is the navigation and the context source

Extend `FounderGraphNodeType` with `file | symbol | doc | concept`. Edges stay explicit (`contains`, `imports`, `documents`, `informed`, `led_to`).

**Build (on the laptop, Founder Node or extension):**

1. Start from `buildFounderGraph` (mission, commits, PRs, decisions).
2. Add workspace files the desktop bridge already knows are open.
3. Add a cheap scan: markdown H1/H2, and TS/JS import specifiers. Store in `~/FounderVault/nucleus.json`.
4. Upload labels, ids, and edges only. File bodies stay local (same rule as the vault).

**Navigate:** sidebar graph. Focus follows the active editor file when that file is a node. Click selects; it does not navigate away to a project page (unlike the Discover universe map).

**Ask:** the click builds the context packet (node, 1-hop neighbors, excerpts under a token budget). The packet is the system prefix for whichever provider is selected. Changing provider does not change the packet.

**Agents:** an agent’s `scopeNodeId` is a filter on that same graph. `nucleus.expand` is how it walks one hop. It does not get a fresh whole-repo dump each turn.

```text
click(node) → ContextPacket → provider.chat
agent.step  → same packet, plus tool results from nodes inside scope
```

That is the product difference versus Cursor: the map chooses the context, the model is swappable, and local models are first-class.

---

## 7. Risks and blockers

| Risk | Why it blocks | What to do |
|---|---|---|
| **Auth split** | Web uses JWT. Gateway and `/memory/context` use `FounderNodeGuard` (`fos_…`). A Nucleus webview that calls the graph API with the wrong token 401s. | Extension calls graph/providers with the node bearer. Add node-bearer variants of `GET /copilot/founder-graph` or a new `/api/ide/nucleus` that accepts the node token. |
| **Secrets** | BYOK stub implies keys live in the browser. Real keys live in `IntegrationCredential` and `~/FounderVault/node-config.json`. Phala keys cannot be unwrapped for arbitrary tools. | P0-4. Never log bearer tokens. Local providers use `secretRef: none` or a node-local secret file, not Neon. |
| **Cloud cannot reach Ollama** | `PROVIDER_SEEDS` ollama URL is localhost on the API host. Inference already works only as a node job. | Registry `placement: local`. Probe from the node. |
| **`toolCalling: false`** | Models that support tools are told they cannot. The loop cannot start. | P0-3. Gate on `capabilities.tools` per provider so a tiny local model that cannot tool-call still works as chat. |
| **Void fork weight** | README: ~140 GB disk, 60–90 min Windows build, Python/Rust toolchain. 0.9.0 shipped without `Founder IDE.exe`. Overlay is a patch script, not a vendored tree, so CI in this Linux repo cannot rebuild the IDE. | P0 Nucleus lives in the **extension**, which installs into the already-built Founder IDE and into Cursor. Do not block Nucleus on a Void rebuild. |
| **Download link drift** | `/founder-ide` “Download for Windows” points at `github.com/danishhaiderau-maker/founder-next/releases/latest`. Update manifest points at `doxed-founders-website` release `founder-stack-v0.9.1`. Extension `package.json` repository URL is `doxedcryptofounder`. | One release URL. Fix in a tiny PR beside P0. |
| **Retired settings** | Node README and older docs send users to `/settings/builder`, which now redirects to `/founder-ide` or account security. | P1-4. Until then, pairing on `/founder-ide` is the only supported path. |
| **Stranded UI** | Mounting `FounderWorkspace` looks like progress and drags raise-room and copilot modes into the IDE. | Do not mount it for P0. |
| **Two graphs** | Memory graph (state) vs founder graph (chain) vs memory-engine stores vs economics knowledge nodes. | Nucleus reads founder graph + local scan. Leave economics and memory-engine alone. |
| **Unsigned installers** | Node README: SmartScreen and macOS quarantine. | Out of scope for the coding loop. Do not block P0. |
| **Deploy surfaces** | Web on Vercel, API on Railway, Neon for Postgres. IDE binaries are GitHub releases. A provider-registry API change needs an API deploy; the extension can ship as a VSIX without a Void rebuild. | Ship P0 as API + extension. No Founder Stack installer required for the first loop if Danish runs the extension inside Founder IDE 0.9.1 or Cursor. |
| **BTC / invent paths** | Same repo, same Railway/Neon. Easy to “while I’m here” a bot change. | IDE PRs must not touch `services/btc-conservative-agent/` or bot sync scripts. |
| **Docs drift** | `docs/PRODUCT.md` anti-BYOK vs shipped Builder BYOK. `docs/KERNEL.md` marks pieces partial that have since grown. | Trust `packages/utils/src/ai-providers.ts`, `apps/api/src/ai-proxy/`, and `apps/founder-node/` over the constitution doc. |

---

## Success check for the first milestone

1. Extension installed, Founder Node paired, `node-config.json` present.
2. Provider list shows a platform alias and a probed local model.
3. Nucleus sidebar shows at least the current task / initiative node from `FounderGraph`.
4. Click that node, ask a question, the prompt contains that node’s label.
5. A follow-up that requests a one-file edit opens a diff the user can accept.
6. No API key in `localStorage`. No change under `services/btc-conservative-agent/`.
