# Founder IDE — Void Fork Implementation Plan

> **Status:** Design / planning document. No code changes, no fork performed.
> **Date:** 2026-07-10
> **Author:** Implementation worker (subagent)
> **Supersedes (for the editor choice):** `docs/FOUNDER-IDE-FORK-PLAN.md` §1–§2 (the VSCodium path). The VSCodium plan's §3 (Open VSX), §5 (Gateway integration), §6 (build/bundle), §7 (strip/keep/add) remain valid and are referenced here.
> **Workspace rule respected:** `config/bot-architecture.lock.json` read; no bot sync scripts touched. This task does not involve `bot.py` or any sync script.

---

## 0. TL;DR — why Void instead of VSCodium

The original Founder IDE plan (the VSCodium path) gives us a *rebranded editor* plus a *chat box that calls our Gateway*. That ships, but it ships with a gap: **it has no inline edit, no autocomplete, no diff review, no chat sidebar.** Those are exactly the features that make a founder *feel* like they're using Cursor instead of "VS Code + a plugin."

**Void closes that gap.**

Void (`voideditor/void`) is a fork of `microsoft/vscode` that has already built the AI-native UX we'd otherwise spend 4–6 weeks building ourselves:

- **Inline edit (Ctrl+K)** — select code, type an instruction, get a streamed edit
- **Autocomplete** — Fill-In-the-Middle completions on every keystroke, debounced
- **Diff review** — accept/reject AI-proposed changes per hunk
- **Chat sidebar** — a React-based chat thread with tool use, context files, and history
- **Quick edit** — light-touch inline actions

Forking Void instead of VSCodium changes the project shape from **"build the AI UX on top of a rebranded editor"** to **"rewire an existing AI UX to talk to our Gateway."** That is a fundamentally smaller, faster project with a better outcome.

| | VSCodium fork (original plan) | Void fork (this plan) |
|--|--|--|
| What we inherit | An editor | An editor **+ full AI UX** |
| What we build | Chat panel, autocomplete, diff review, inline edit, Memory injection | **Rewiring only** — redirect AI to our Gateway |
| Cursor-gap closed by | Phase 3 (4+ weeks of bespoke UI work) | **Day 1** (the UX already exists) |
| Main risk | We build a worse Cursor | We rewire someone else's Cursor |

**Recommendation: pivot the editor foundation from VSCodium to Void.** Keep the VSCodium scaffolding's branding scripts, Inno Setup composition, and Gateway wiring — they transfer directly. Replace only the upstream (VSCodium → Void) and the integration point (extension LM provider → `LLMMessageService` rewire).

**Estimated total effort: ~2–3 weeks** (5 phases, 11–18 dev-days). The VSCodium path was 5–6 weeks to feature-complete.

---

## 1. Void's architecture — what we inherit

Void is a VS Code fork maintained by the Void team (`voideditor/void`). Its AI features live in a single contribution folder, which is what makes it forkable in a surgical way:

```
src/vs/workbench/contrib/void/browser/
├── autocompleteService.ts       # inline completions (FIM), LRU cache, debounce
├── editCodeService.ts           # Ctrl+K inline edit
├── chatThreadService.ts         # chat sidebar threads, history, tool calls
├── llmMessageService.ts         # *** THE CENTRAL CHOKEPOINT ***
├── settingsService.ts           # IVoidSettings — provider list, model config
└── react/
    ├── sidebar/                 # chat thread React UI
    ├── ctrlK/                   # inline edit React UI
    ├── quickEdit/               # light inline actions
    └── diff/                    # accept/reject diff overlay
```

### 1.1 `LLMMessageService` — the single integration point

This is the most important architectural fact about Void. **Every** AI request — autocomplete, inline edit, chat, agent — flows through one service:

- `autocompleteService.ts` calls `LLMMessageService` for FIM completions
- `editCodeService.ts` calls `LLMMessageService` for Ctrl+K edits
- `chatThreadService.ts` calls `LLMMessageService` for chat turns
- Any future agent/quick-edit feature calls `LLMMessageService`

It currently dispatches to a hardcoded provider list (OpenAI, Anthropic, Gemini, Ollama, Mistral, Groq, DeepSeek, etc.). **We replace that dispatch with a single call to our Gateway.** One service, one rewire point. That is why this fork is small.

### 1.2 `AutocompleteService`

- Registers with VS Code's `inlineCompletionsProvider` API (the stable public hook for ghost-text completions)
- Uses **FIM (Fill-In-the-Middle)** capable models — passes prefix + suffix so the model fills in the middle
- **LRU cache:** `MAX_CACHE_SIZE = 20`
- **Debounce:** 500ms after the last keystroke
- **Timeout:** 60s per request
- **Concurrency:** max 2 in-flight requests
- **Prediction types:** single-line-fill-middle, single-line-redo-suffix, multi-line-start-on-next-line
- **Post-processing:** strips markdown fences, trims whitespace, bracket matching, applies stop tokens
- **FIM message builder:** `prepareFIMMessage()` converts the prefix/suffix/stopTokens into the format each provider expects

This is a complete, production-quality autocomplete implementation. We do not rewrite it — we only point its model calls at our Gateway (see §5 and §6).

### 1.3 `editCodeService.ts` (Ctrl+K)

The inline-edit service. Select code → press Ctrl+K → type an instruction → streamed edit appears inline with an accept/reject diff. Calls `LLMMessageService` with the selection + instruction as the prompt.

### 1.4 `chatThreadService.ts`

The chat sidebar. Maintains thread state (messages, attached files, tool calls), renders via the `react/sidebar/` components, and dispatches each turn through `LLMMessageService`. Supports tool use (read file, run command, etc.) wired into the React UI.

### 1.5 React UI components

`react/sidebar/`, `react/ctrlK/`, `react/quickEdit/`, `react/diff/` — these are the actual UI surfaces. They are already built, already themed, and already call the services above. We inherit them as-is; the only thing that changes is where the model responses come from.

### 1.6 License & maintenance state

- **License:** Apache 2.0 — we can fork, rebrand, and ship commercially
- **Maintenance:** Void's team has paused active development (exploring other things). The code is **functional but unmaintained upstream.** This is a fork opportunity, not a dependency — once we fork, we own it. See §10 for the upstream-sync risk.

---

## 2. The fork process — downstream void-builder

Void ships via two repos:

| Repo | Role |
|--|--|
| `voideditor/void` | The forked VS Code source **with AI features** (the contribution folder above) |
| `voideditor/void-builder` | The build scripts: clones `microsoft/vscode`, applies Void's patches, compiles |

`void-builder` is the analog of VSCodium's build-script repo. It works the same way:

1. Clone `microsoft/vscode` at a pinned commit
2. Apply Void's patches via `prepare_vscode.sh` (sed replacements + file overlays)
3. Drop in Void's `src/vs/workbench/contrib/void/` contribution folder
4. Compile with VS Code's gulp + Inno Setup toolchain

### 2.1 The branding mechanism we extend

`void-builder`'s `prepare_vscode.sh` uses `sed` to patch `build/win32/code.iss` (the Inno Setup script):

```bash
# Void's existing pattern (paraphrased from prepare_vscode.sh):
sed -i 's/code\.visualstudio\.com/voideditor.com/g'  build/win32/code.iss
sed -i 's/Microsoft Corporation/Void/g'              build/win32/code.iss
```

**This is the exact pattern we extend for Founder IDE.** We add a third sed pass that rewrites Void strings → Founder IDE strings, drops in our icon/splash, and patches `product.json` with our GUIDs. This is the same `prepare_founder_ide.sh` approach documented in the VSCodium plan — the mechanism is identical because both Void and VSCodium are VS Code forks using the same Inno Setup pipeline.

### 2.2 Why our existing toolchain works

The toolchain we already installed for the VSCodium build is sufficient for Void, because Void is also a VS Code fork using the same build system:

| Tool | VSCodium needs it | Void needs it |
|--|--|--|
| Node 24 (pinned via `.nvmrc`) | ✅ | ✅ |
| Python 3.11 (node-gyp) | ✅ | ✅ |
| jq (product.json patches) | ✅ | ✅ |
| 7-Zip | ✅ | ✅ |
| Rustup (native modules) | ✅ | ✅ |
| Inno Setup (Windows installer) | ✅ | ✅ |
| Git Bash (POSIX sed/grep/find) | ✅ | ✅ |

**No new build prerequisites.** The same Windows machine that builds VSCodium builds Void.

### 2.3 The downstream layout

We keep our existing `packages/founder-ide/` scaffolding and retarget it:

```
packages/founder-ide/
├── build/
│   ├── build-founder-ide.sh      # now wraps void-builder instead of vscodium dev/build.sh
│   ├── prepare-founder-ide.sh    # sed Void → Founder IDE (was: sed VSCodium → Founder IDE)
│   └── brand-product.json.patch  # jq filter — UNCHANGED (product.json shape is the same)
├── assets/                       # icon.ico, splash, product.json.template — UNCHANGED
├── installer/                    # founder-stack.iss, build-stack-installer.ps1 — UNCHANGED
└── config/build-env.sh           # branding env vars — UNCHANGED
```

Only the upstream checkout changes: `vscodium` → `void` (+ `void-builder`). The patches, branding, and installer composition carry over.

---

## 3. Branding — product.json patches, code.iss sed, rename to Founder IDE

### 3.1 `product.json` patches (via `jq`)

Same shape as the VSCodium plan. `prepare-founder-ide.sh` applies `brand-product.json.patch` to rewrite:

- `nameShort` → "Founder IDE"
- `nameLong` → "Founder IDE"
- `applicationName` → "founder-ide"
- `win32MutexName` → "founderide"
- `win32DirName` → "Founder IDE"
- `win32RegValueName` → "FounderIDE"
- `win32AppUserModelId` → `digital.doxxedcrypto.FounderIDE`
- `dataFolderName` → `.founder-ide"
- `urlProtocol` → `founder-ide`
- **Fresh Windows GUIDs** for `win32AppId` and `win32x64UserAppId` (the existing GUIDs in `packages/founder-ide/assets/product.json.template`, generated 2026-07-10, are reused — they were created independent of VSCodium's and don't collide with VS Code, VSCodium, or Void)
- `extensionsGallery` → Open VSX URLs (unchanged from the VSCodium plan)

### 3.2 `code.iss` sed replacements

`prepare-founder-ide.sh` runs three sed passes in order:

1. **Void's existing pass** (already in `void-builder`'s `prepare_vscode.sh`): `code.visualstudio.com` → `voideditor.com`, `Microsoft Corporation` → `Void`
2. **Our Founder IDE pass** (new): `voideditor.com` → `doxxedcrypto.digital`, `Void` → `Founder IDE`, `Void` (publisher) → `Doxxed Crypto`

We layer on top of Void's patches the same way the VSCodium plan layers on top of VSCodium's patches.

### 3.3 Icons, splash, theme

- Replace `resources/app/resources/win32/code.ico` with our `icon.ico`
- Replace the Inno Setup `installerIcon` and `setupIconFile`
- Drop a Founder OS dark/light theme into the built-in themes (same approach as the VSCodium plan §7)

### 3.4 App identity

- **App name:** Founder IDE
- **Binary name:** `founder-ide.exe`
- **Publisher:** Doxxed Crypto
- **Install dir:** `%LOCALAPPDATA%\Founder IDE`
- **User data dir:** `%APPDATA%\Founder IDE` (via `dataFolderName: .founder-ide`)

Fresh GUIDs ensure side-by-side install with VS Code, VSCodium, **and** Void.

---

## 4. The critical rewiring: redirecting all AI through our Gateway

> **This is the most important section of the document.** Everything else is plumbing. This is where Founder IDE stops being "Void with our name on it" and becomes "Founder IDE."

### 4.1 The rewire in one sentence

**Replace `LLMMessageService`'s provider dispatch with a single call to our Gateway's `/api/v1/chat/completions`** (OpenAI-compatible, SSE streaming).

After this change, no Founder IDE AI request ever talks to OpenAI/Anthropic/Gemini/Ollama directly. Every token flows through:

```
Void AI feature (autocomplete / Ctrl+K / chat / agent)
   │
   ▼
LLMMessageService   ← WE REWIRE THIS
   │  fetch (HTTPS, Authorization: Bearer fos_{nodeId}:{nodeToken})
   ▼
Founder OS Gateway — /api/v1/chat/completions
   AiProxyController → AiProxyRuntimeService
     decideRoute (Routing Engine v2: cache → capability gate → scoring)
     invoke (GLM / DeepSeek / etc., streaming)
     afterRequest (DDollar spend, AiTokenUsageLog, Flight Recorder)
```

### 4.2 What the Gateway gives us that replaces Void's provider system

Void's `LLMMessageService` currently implements N provider clients (OpenAI, Anthropic, Gemini, Ollama, Mistral, Groq, DeepSeek, ...). Our Gateway already does all of this server-side and adds capabilities Void doesn't have:

| Void's provider system | Our Gateway |
|--|--|
| N hardcoded provider clients | One OpenAI-compatible endpoint |
| User pastes API keys per provider | Founder Node bearer (`fos_{nodeId}:{nodeToken}`) — no keys in the IDE |
| User picks a model manually | Routing Engine v2 picks the best model automatically (cache → capability gate → scoring) |
| No spend tracking | DDollar spend tracking per request |
| No routing observability | Flight Recorder logs every routing decision |
| No project context | Memory Engine injects project context as a system message |
| No per-message cost UI | `founderOs` SSE metadata line (tier, provider, model, ddollarCost) |
| Manual model selection per feature | Model aliases: `founder-os-auto` / `code` / `reasoning` / `fast` |

**The Gateway is a strict superset of what `LLMMessageService` does today.** The rewire is a replacement, not a layering.

### 4.3 Map Void's feature types to our model aliases

Void has four AI entry points. Each maps to one of our model aliases:

| Void feature | Gateway alias | Why |
|--|--|--|
| **Chat** (`chatThreadService`) | `founder-os-auto` | Let the Routing Engine decide — chat is general-purpose |
| **Inline edit / Ctrl+K** (`editCodeService`) | `founder-os-code` | Code-focused; Gateway routes to GLM 5.2 (the coding tier) |
| **Autocomplete** (`autocompleteService`) | `founder-os-fast` | Cheapest, fastest tier; FIM model (DeepSeek V4 Flash, see §6) |
| **Agent / quick edit** | `founder-os-reasoning` | Deep multi-step reasoning; Gateway routes to DeepSeek |

The rewire maps each caller's feature-type flag to the alias before constructing the Gateway request. Concretely, inside the rewritten `LLMMessageService`:

```ts
// PSEUDOCODE — the shape of the rewire, not a patch
function routeToAlias(feature: VoidFeature): string {
  switch (feature) {
    case 'autocomplete':  return 'founder-os-fast';       // FIM, cheapest
    case 'inline-edit':   return 'founder-os-code';        // coding tier
    case 'chat':          return 'founder-os-auto';        // routing decides
    case 'agent':         return 'founder-os-reasoning';   // deep reasoning
  }
}

async function sendLlmRequest(req: VoidLlmRequest): Promise<Stream> {
  const body = {
    model: routeToAlias(req.feature),
    messages: req.messages,        // already includes Memory Engine system msg (§4.5)
    stream: true,
    founder_os_metadata: true,     // ask Gateway for the cost/route SSE pre-line
    // FIM-specific fields pass through for autocomplete (§6)
    ...req.fim && { prefix: req.fim.prefix, suffix: req.fim.suffix, stop: req.fim.stopTokens },
  };
  return fetch(`${gatewayUrl}/api/v1/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${bearerFromVault()}`,
    },
    body: JSON.stringify(body),
    signal: req.cancelToken,
  });
}
```

This single function replaces the entire `switch (provider)` dispatch in `LLMMessageService`.

### 4.4 Parse the `founderOs` SSE metadata line in Void's UI

Our Gateway emits a non-standard SSE line *before* the first OpenAI chunk when `founder_os_metadata: true` is in the request body (design from the VSCodium plan §5.3 / §8.2):

```
data: {"founderOs":{"requestId":"...","tier":"code","provider":"glm","model":"glm-4.6","ddollarCost":2}}

data: {"choices":[{"delta":{"content":"Hello"}, ...}]}
data: {"choices":[{"delta":{"content":" world"}, ...}]}
data: [DONE]
```

The rewire teaches Void's stream parser to:

1. Detect `founderOs` lines and route them to a UI store (per-turn cost, provider, model, tier)
2. Pass standard OpenAI `choices[0].delta.content` chunks through to the existing React renderers (autocomplete ghost text, Ctrl+K streamed edit, chat thread)

This gives us **routing transparency for free**: every chat turn shows which provider/model the Routing Engine picked and what it cost in DDollars. Void's React components already have status slots where this fits.

### 4.5 Wire Memory Engine into Void's system prompt builder

Before calling the Gateway, the rewritten `LLMMessageService` prepends a system message built from:

1. **Project memory** — `.founder/project-memory.json` in the workspace (coding conventions, active task, recent decisions)
2. **Founder memory** — from the Founder Node vault or `/api/memory/context` (founder preferences, past Flight Recorder patterns)

This is the same `buildSystemPrompt()` design from the VSCodium plan §8.3, moved from the extension layer into `LLMMessageService`. The Gateway's Routing Engine never sees the difference — it routes the resulting prompt. The Flight Recorder logs the full prompt hash so Memory injection is observable.

**This is the one place the rewire adds logic rather than replacing it.** The provider dispatch (N clients → 1 Gateway call) is a pure replacement; the Memory injection is additive.

### 4.6 What the rewire does NOT touch

- **The React UI** (sidebar, ctrlK, quickEdit, diff) — unchanged; they consume the same stream shape
- **`AutocompleteService`'s cache/debounce/timeout/concurrency** — unchanged; it still calls `LLMMessageService`, which now calls us
- **`editCodeService` and `chatThreadService`** — unchanged; they still call `LLMMessageService`
- **VS Code's `inlineCompletionsProvider` registration** — unchanged
- **Void's diff review UI** — unchanged

The blast radius of the rewire is one service file (`llmMessageService.ts`) plus the stream parser that feeds the React UI. That's the entire scope of "make Void talk to our Gateway instead of OpenAI."

---

## 5. Autocomplete through the Gateway — FIM model routing

Autocomplete is the one feature that needs special handling because it uses **FIM (Fill-In-the-Middle)** models, not standard chat models. FIM models take a prefix + suffix and fill in the middle; chat models take a message list.

### 5.1 The FIM model

**DeepSeek V4 Flash** as the FIM model:

- Cheapest option in our stable: ~$0.14 / 1M input tokens
- Fast (sub-300ms first token), which is mandatory for autocomplete — anything slower feels broken
- FIM-capable

When `founder-os-fast` is requested **with FIM fields** (`prefix`/`suffix`/`stop`), the Gateway's Routing Engine routes to DeepSeek V4 Flash. When `founder-os-fast` is requested **without** FIM fields (e.g. a quick chat Q&A), the Routing Engine picks the cheapest chat-capable model.

### 5.2 Mapping Void's `prepareFIMMessage()` to our Gateway

Void's `AutocompleteService` builds FIM requests via `prepareFIMMessage()`, which converts the prefix/suffix/stopTokens into each provider's FIM format. After the rewire, `prepareFIMMessage()` produces our Gateway's FIM shape:

```ts
// PSEUDOCODE — the Gateway's FIM request shape
{
  model: 'founder-os-fast',
  messages: [],                          // empty for pure FIM
  fim: {
    prefix: extractedPrefix,             // code before the cursor
    suffix: extractedSuffix,             // code after the cursor
    stop: ['\n\n', '```', ...],          // stop tokens
  },
  stream: true,
  max_tokens: 64,                        // autocomplete is short
}
```

The Gateway recognizes the `fim` field, selects DeepSeek V4 Flash, and streams completion tokens back in the standard OpenAI `choices[0].delta.content` shape so Void's existing stream parser handles them without modification.

### 5.3 What we keep from Void's autocomplete

All of it:

- The `inlineCompletionsProvider` registration
- The LRU cache (`MAX_CACHE_SIZE = 20`)
- The 500ms debounce
- The 60s timeout
- The max-2-concurrent-requests limiter
- The three prediction types (single-line-fill-middle, single-line-redo-suffix, multi-line-start-on-next-line)
- The post-processing (markdown fence stripping, whitespace trim, bracket matching, stop-token application)

These are all good, production-quality behaviors. We only change where the raw completion comes from.

### 5.4 Cost control

Autocomplete is high-volume (a completion per keystroke cluster). The Gateway's Routing Engine v2 cache layer is critical here — identical prefix/suffix pairs hit the cache and cost zero DDollars. DeepSeek V4 Flash at $0.14/1M input keeps uncached completions cheap. The Flight Recorder logs every autocomplete request so we can monitor spend.

---

## 6. What we strip / keep / change

### Strip

- **Void's provider settings UI** — the entire settings panel where users paste OpenAI/Anthropic/Gemini/Ollama API keys. With the Gateway rewire, no user ever enters a key; Founder Node pairing provides the bearer.
- **Void's per-provider client code** in `LLMMessageService` — replaced by the single Gateway call
- **Void branding strings** — name, publisher, URLs → Founder IDE (via sed)
- **Void's marketplace config** if it points anywhere other than Open VSX — we standardize on Open VSX (same as the VSCodium plan)

### Keep

- **`LLMMessageService`** — the service stays; we rewrite its internals (the provider dispatch) but keep its position as the central chokepoint
- **`AutocompleteService`** — entirely (cache, debounce, timeout, concurrency, post-processing, `inlineCompletionsProvider` registration)
- **`editCodeService`** (Ctrl+K) — entirely
- **`chatThreadService`** — entirely (threads, history, tool calls)
- **All React UI components** — sidebar, ctrlK, quickEdit, diff (unchanged; they consume the same stream shape)
- **All VS Code editor functionality** — file tree, multi-cursor, search, terminals, debugging, source control, tasks, keybindings, themes
- **The VS Code extension API surface** — so standard extensions work
- **Open VSX marketplace** (configured in `product.json`)
- **Void's diff review UX** — accept/reject per hunk (this is the Cursor-feel feature we inherit for free)

### Change

- **`LLMMessageService` internals** — provider dispatch → single Gateway call (§4)
- **`AutocompleteService` model routing** — `prepareFIMMessage()` → Gateway FIM shape (§5)
- **Stream parser** — add `founderOs` metadata line handling (§4.4)
- **System prompt builder** — Memory Engine injection (§4.5)
- **Branding** — name, icons, splash, GUIDs (§3)
- **Settings** — replace provider-key settings with Founder Node pairing (auto-discovered from vault)

---

## 7. Phased implementation plan

| Phase | Scope | Effort | Calendar |
|--|--|--|--|
| **A** | Fork + build + rebrand | 3–5 dev-days | ~1 week |
| **B** | Rewire `LLMMessageService` → Gateway | 3–5 dev-days | ~1 week |
| **C** | Autocomplete FIM routing | 2–3 dev-days | ~3–4 days |
| **D** | Memory Engine + DDollar integration | 2–3 dev-days | ~3–4 days |
| **E** | Bundle into Founder Stack installer | 1–2 dev-days | ~2 days |
| **Total** | | **11–18 dev-days** | **~2–3 weeks** |

### Phase A — Fork + build + rebrand (3–5 dev-days)

**Goal:** A Windows build of Void that boots as "Founder IDE" with our branding, no AI changes yet.

**Tasks:**
1. Downstream `void-builder` (clone as a submodule or vendored copy under `packages/founder-ide/upstream/`)
2. Pin to a specific Void release commit (don't track `main` — Void is paused, so pinning is low-burden)
3. Retarget `build/build-founder-ide.sh` to wrap `void-builder`'s build instead of VSCodium's `dev/build.sh`
4. Extend `build/prepare-founder-ide.sh` with the third sed pass (Void → Founder IDE strings in `code.iss`)
5. Apply `brand-product.json.patch` to rewrite `product.json` (name, GUIDs, Open VSX, data dir)
6. Drop in our `icon.ico`, splash, theme
7. Run a clean Windows build; verify `Founder-IDE-Setup-x64.exe` installs and boots
8. Verify side-by-side install with VS Code (no mutex/installer conflict — fresh GUIDs)

**Acceptance criteria:**
- `Founder-IDE-Setup-x64.exe` installs an app titled "Founder IDE"
- The app boots, opens files, runs terminals — it's a working editor
- Void's AI features are present but unconfigured (they'll error on first use — that's Phase B)
- Installs alongside VS Code without conflict

### Phase B — Rewire `LLMMessageService` → Gateway (3–5 dev-days)

**Goal:** Chat and inline edit (Ctrl+K) talk to our Gateway. This is the most important phase.

**Tasks:**
1. Read `LLMMessageService` end-to-end; identify the provider dispatch switch
2. Replace the dispatch with the single Gateway call (`/api/v1/chat/completions`, SSE)
3. Implement `routeToAlias()` — map Void feature types to our model aliases (§4.3)
4. Implement credential discovery — read `~/FounderVault/node-config.json` for the bearer (same logic as `packages/founder-ide-extension/src/credentials.ts`)
5. Extend the stream parser to handle the `founderOs` metadata line (§4.4) — route it to a UI store
6. Surface the metadata in Void's React UI (status slot in chat thread, Ctrl+K panel)
7. Test: send a chat message → tokens stream from our Gateway; status shows tier/provider/DDollar cost
8. Test: Ctrl+K inline edit → streamed edit from our Gateway; diff review works
9. Strip Void's provider settings UI (no more API-key panels)

**Acceptance criteria:**
- Chat sidebar: type a message → Gateway responds, streaming, with Routing Engine tier visible
- Ctrl+K: select code → type instruction → streamed edit → accept/reject diff
- No provider-key UI anywhere; pairing is via Founder Node
- Flight Recorder has a row for each chat/edit request
- `/api/v1/usage` reflects the spend
- Cancellation works (abort the fetch on user cancel)

### Phase C — Autocomplete FIM routing (2–3 dev-days)

**Goal:** Ghost-text autocomplete streams from DeepSeek V4 Flash via our Gateway.

**Tasks:**
1. Modify `prepareFIMMessage()` to emit our Gateway FIM shape (prefix/suffix/stop) instead of per-provider FIM formats
2. Set autocomplete to use `founder-os-fast` alias
3. Verify the Gateway's Routing Engine routes FIM requests to DeepSeek V4 Flash
4. Tune: confirm the 500ms debounce, 60s timeout, max-2-concurrent, and LRU cache still behave correctly against the Gateway
5. Verify post-processing (markdown fence strip, bracket matching, stop tokens) still produces clean completions
6. Cost check: monitor Flight Recorder for autocomplete volume; confirm DDollar spend is sane

**Acceptance criteria:**
- Typing in an editor produces ghost-text completions within ~300ms
- Completions are context-aware (FIM — they respect code after the cursor)
- Cache hits cost zero DDollars; uncached completions route to DeepSeek V4 Flash
- No duplicate completions on backspace/retypes (LRU cache works)

### Phase D — Memory Engine + DDollar integration (2–3 dev-days)

**Goal:** Memory Engine context is injected into every request; DDollar cost is visible per turn.

**Tasks:**
1. Implement `buildSystemPrompt()` in `LLMMessageService` — reads project + founder memory, prepends as system message (§4.5)
2. Wire project memory source: `.founder/project-memory.json` in the workspace
3. Wire founder memory source: `/api/memory/context` (or the Founder Node vault, whichever exists)
4. Confirm the Flight Recorder logs the full prompt hash (Memory injection is observable)
5. Surface per-turn DDollar cost in the chat thread UI (from the `founderOs` metadata line)
6. Add a status-bar item showing lifetime DDollar spend in this session (reuse the pattern from `packages/founder-ide-extension/src/cost-tracker.ts`)

**Acceptance criteria:**
- Memory context appears in the Flight Recorder prompt hash for each request
- Chat turns show "2 D$ · GLM 4.6 · code tier" (or similar) per message
- Status bar shows session DDollar total
- Memory injection is toggleable (setting to disable for debugging)

### Phase E — Bundle into Founder Stack installer (1–2 dev-days)

**Goal:** One `Founder-Stack-Setup-<v>.exe` installs Founder IDE + Founder Node.

**Tasks:**
1. Reuse `packages/founder-ide/installer/founder-stack.iss` (Inno Setup) — it already composes two installers
2. Point it at the Phase A `Founder-IDE-Setup-x64.exe` and the existing `Founder-Node-<v>-win-x64.exe`
3. Run `installer/build-stack-installer.ps1` to produce `Founder-Stack-Setup-<v>.exe`
4. End-to-end test: install Founder Stack → Founder Node pairs → open Founder IDE → chat works with zero manual config

**Acceptance criteria:**
- `Founder-Stack-Setup-<v>.exe` installs both apps; both appear in Start Menu
- After install, opening Founder IDE → chat works immediately (Founder Node paired during install)
- One uninstall entry per app (clean separation)

---

## 8. Comparison: VSCodium fork vs Void fork

| Dimension | VSCodium fork (original plan) | Void fork (this plan) |
|--|--|--|
| **Upstream** | `VSCodium/vscodium` (build scripts) + `microsoft/vscode` | `voideditor/void` (forked source) + `voideditor/void-builder` (build scripts) |
| **What we inherit** | A rebranded editor, nothing AI | A rebranded editor **+ full AI UX** (autocomplete, Ctrl+K, diff, chat sidebar) |
| **Inline edit (Ctrl+K)** | Build from scratch (Phase 3, bespoke) | **Inherited, day 1** |
| **Autocomplete** | Build from scratch (bespoke `inlineCompletionsProvider`) | **Inherited** — Void's `AutocompleteService` (cache, debounce, FIM, post-processing) |
| **Diff review** | `vscode.diff` per file (extension-level, weak) | **Inherited** — Void's accept/reject per-hunk diff overlay |
| **Chat sidebar** | Built-in VS Code Chat view (via extension LM provider) | **Inherited** — Void's React chat thread with tool use |
| **AI integration point** | Extension: `vscode.lm.registerLanguageModelChatProvider` | In-tree: `LLMMessageService` rewire |
| **Branding mechanism** | `product.json` (jq) + `code.iss` (sed) | **Same** — `product.json` (jq) + `code.iss` (sed); Void already does this, we extend |
| **Build toolchain** | Node 24, Python 3.11, jq, 7-Zip, Rustup, Inno Setup, Git Bash | **Identical** — same toolchain |
| **Build time / disk** | ~45–90 min clean, ~50 GB | **Similar** — same VS Code build system |
| **License** | MIT | Apache 2.0 (both allow commercial fork + rebrand) |
| **Upstream sync burden** | Low — VSCodium actively tracks VS Code monthly | **Higher** — Void is paused; we own sync from VS Code → Void → us (see §10) |
| **Terminal-output feedback loop** | Hard (extension sandbox) / needs in-tree work | **Easier** — we're already in-tree; Void's `chatThreadService` has tool hooks |
| **Effort to feature-complete** | 23–33 dev-days (5–6 weeks) | **11–18 dev-days (2–3 weeks)** |
| **What we build** | Chat panel, autocomplete, diff review, inline edit, Memory injection | **Rewiring only** — redirect AI to Gateway |

**The tradeoff in one line:** Void gives us the Cursor-grade UX for free but costs us upstream sync (Void is paused). VSCodium gives us upstream sync for free but costs us building the UX. Since the UX is the product and sync is a monthly chore, **Void wins.**

---

## 9. Risks and unknowns

| Risk | Severity | Mitigation |
|--|--|--|
| **Upstream sync — Void is paused** | **High** | Once we fork, we own the VS Code → Void → us sync. Budget ~2–4 days/month for rebasing our patches on new VS Code releases. Mitigation: keep our diff *additive* (a patch script that runs after Void's); pin to specific Void commits; only rebase when a VS Code release has a feature we need. Since Void is paused, we're not chasing a moving target — we're syncing **VS Code** into our fork directly. |
| **Build toolchain compatibility** | Medium | Void uses the same VS Code build system we already set up for VSCodium. Risk is low but the first clean build is the long pole (45–90 min). Mitigation: get the Phase A clean build done early; use CI (`windows-2022` runner) for release builds. |
| **FIM API compatibility** | Medium | Our Gateway doesn't currently have a FIM endpoint — it has `/api/v1/chat/completions`. We need to add FIM support (the `fim: {prefix, suffix, stop}` field + DeepSeek V4 Flash routing). Mitigation: this is a small server-side addition (~1 day) scoped into Phase C. Verify DeepSeek V4 Flash's FIM API shape before committing. |
| **`LLMMessageService` rewire complexity** | Medium | We don't know Void's internal request shape until we read the source. Mitigation: Phase B starts with a thorough read of `llmMessageService.ts`; the rewire is mechanical once the dispatch switch is identified. |
| **Void's React UI assumes provider-specific fields** | Low-Medium | Void's UI may surface provider-specific metadata (e.g. an OpenAI model name) that our Gateway doesn't emit in the same shape. Mitigation: the `founderOs` metadata line gives us tier/provider/model/cost; we map these into whatever slots Void's UI expects. |
| **GUID collision with VS Code / VSCodium / Void** | High if missed | Reuse the fresh GUIDs from `packages/founder-ide/assets/product.json.template` (generated 2026-07-10). Test side-by-side install with all three before any release. |
| **Code signing** (SmartScreen on unsigned installer) | Medium | Reuse the Founder Node signing cert (`apps/founder-node/build/sign.js`). Same cert, different appId. |
| **Void license attribution** | Low | Apache 2.0 requires preserving the NOTICE and license. Keep Void's `LICENSE` and `NOTICE` in the install dir. |
| **Void's autocomplete cache assumes provider locality** | Low | Void's LRU cache keys on the request shape. After the rewire, all requests go to one Gateway alias; cache behavior should improve (more hits). Verify in Phase C. |
| **Memory Engine endpoint may not exist** | Low | Phase D needs `/api/memory/context` or equivalent. If it doesn't exist, fall back to `.founder/project-memory.json` (file-based) for Phase D and build the endpoint later. |

**The #1 risk is upstream sync burden** — because Void is paused, we inherit the full VS Code → us sync chain. This is manageable (additive patches, pinned commits, monthly rebase) but it's the one place the VSCodium path was strictly easier.

---

## 10. Migration from current VSCodium scaffolding

The existing `packages/founder-ide/` scaffolding was built for the VSCodium path. **Most of it transfers directly to the Void path.** Here's the breakdown.

### Keep (unchanged)

| Asset | Why it transfers |
|--|--|
| `packages/founder-ide/config/build-env.sh` | Branding env vars (`APP_NAME`, `BINARY_NAME`, `ORG_NAME`, `WIN32_APP_USER_MODEL_ID`, Open VSX URLs) — these are read by VS Code's build system, which Void inherits unchanged |
| `packages/founder-ide/build/brand-product.json.patch` | jq filter for `product.json` — the `product.json` shape is identical (both are VS Code forks) |
| `packages/founder-ide/assets/product.json.template` | Our GUIDs, name, data dir — fresh and non-colliding; reused as-is |
| `packages/founder-ide/assets/icon.ico` + splash | Branding assets — editor-agnostic |
| `packages/founder-ide/installer/founder-stack.iss` | Inno Setup bundle script — composes two installers; works for Founder IDE (Void) + Founder Node |
| `packages/founder-ide/installer/build-stack-installer.ps1` | Orchestrates the bundle build — editor-agnostic |
| `packages/founder-ide-extension/` (the VS Code extension) | **Still useful** — see §10.1 below |

### Replace

| Asset | From (VSCodium) | To (Void) |
|--|--|--|
| Upstream checkout | `VSCodium/vscodium` (build-script repo) | `voideditor/void` + `voideditor/void-builder` |
| `build/build-founder-ide.sh` wrap target | VSCodium's `dev/build.sh` | void-builder's build entry point |
| `build/prepare-founder-ide.sh` sed passes | `code.visualstudio.com` → VSCodium → Founder IDE | `code.visualstudio.com` → Void → Founder IDE (Void's pass replaces step 1; our pass rewrites Void → Founder IDE) |

### Add

| Asset | Purpose |
|--|--|
| `LLMMessageService` rewire (Phase B) | The Gateway integration — the core of the Void fork |
| `prepareFIMMessage()` rewrite (Phase C) | FIM routing through the Gateway |
| Memory Engine injection in `LLMMessageService` (Phase D) | Project/founder context as system message |
| Gateway FIM endpoint (server-side, Phase C) | `fim` field handling + DeepSeek V4 Flash routing in `ai-proxy.controller.ts` |

### 10.1 Is the existing extension code still useful?

**Yes — for a different audience.**

`packages/founder-ide-extension/` is our VS Code extension that registers the Founder OS Gateway as a `LanguageModelChatProvider`. It works in **Cursor, VSCodium, stock VS Code, and Windsurf** — no fork required.

After the Void fork:

- **For Founder IDE (the Void fork):** the extension's *logic* (Gateway client, credential discovery, model aliases, SSE parser, Memory injection, cost tracker) gets **absorbed into the in-tree `LLMMessageService` rewire.** Founder IDE doesn't load the extension; it has the logic built in.
- **For everyone else (founders who already use Cursor/VS Code/Windsurf and don't want to install Founder IDE):** the extension remains the distribution vehicle. A founder can install our `.vsix` into their existing editor and get Founder OS chat without switching editors.

So the extension code is **dual-purpose**:

| Consumer | Mechanism | Status |
|--|--|--|
| Founder IDE (Void fork) | Absorbed into `LLMMessageService` rewire | New (Phases B–D) |
| Cursor / VS Code / Windsurf users | `.vsix` extension install | Existing, unchanged |

The extension code is also **a reference implementation** for the rewire — the Gateway client (`gateway-client.ts`), credential discovery (`credentials.ts`), model alias mapping (`models.ts`), and cost tracking (`cost-tracker.ts`) are all directly portable into the in-tree `LLMMessageService`. Phase B should read these files before writing the rewire.

---

## 11. References

**Void**
- Void repo: https://github.com/voideditor/void
- Void builder: https://github.com/voideditor/void-builder
- `prepare_vscode.sh` (branding): Void's sed-based patch pipeline (analogous to VSCodium's)

**Our existing code (read for this plan)**
- `docs/FOUNDER-IDE-FORK-PLAN.md` — the original VSCodium plan (§3 Open VSX, §5 Gateway integration, §6 build/bundle, §7 strip/keep/add remain valid)
- `packages/founder-ide/README.md` — existing scaffolding layout
- `packages/founder-ide/config/build-env.sh` — branding env vars (transfers unchanged)
- `packages/founder-ide-extension/README.md` — the extension (dual-purpose: absorbed into the fork + still shipped as `.vsix`)
- `packages/founder-ide-extension/src/` — Gateway client, credentials, models, SSE parser, cost tracker, Memory injection (reference implementation for the `LLMMessageService` rewire)
- `apps/api/src/ai-proxy/ai-proxy.controller.ts` — `/api/v1/chat/completions` (the endpoint we rewire to)
- `apps/api/src/ai-proxy/ai-proxy.constants.ts` — model aliases (`founder-os-auto/code/reasoning/fast`)
- `config/bot-architecture.lock.json` — read per workspace rule; not modified

**Build toolchain (unchanged from VSCodium plan)**
- Node 24, Python 3.11, jq, 7-Zip, Rustup, Inno Setup, Git Bash — all already installed

**FIM / autocomplete**
- VS Code `inlineCompletionsProvider` API — the stable hook Void's `AutocompleteService` uses
- DeepSeek V4 Flash — the FIM model (cheapest, fast, ~$0.14/1M input)
