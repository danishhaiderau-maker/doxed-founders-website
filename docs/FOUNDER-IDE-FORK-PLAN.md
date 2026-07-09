# Founder IDE — VSCodium Fork Design Report

> **Status:** Design / research only. No code changes, no fork performed.
> **Date:** 2026-07-10
> **Author:** Investigation worker (subagent)
> **Workspace rule respected:** `config/bot-architecture.lock.json` read; no bot sync scripts touched. This task does not involve `bot.py` or any sync script.

---

## 0. TL;DR — the single most important finding

**The VS Code `LanguageModelChatProvider` API was finalized as a *stable* public extension API in VS Code 1.104 (commit `a18d41e`, 2025-08-27).** This means a VS Code / VSCodium **extension** can register our AI Gateway as a first-class language model and the *built-in* VS Code Chat view will stream tokens from it — **without forking the editor at all**.

This splits the project into two cleanly separable layers:

| Layer | What it needs | Mechanism |
|---|---|---|
| **Chat that calls our Gateway** | Stable extension API | `vscode.lm.registerLanguageModelChatProvider('founder-os', provider)` |
| **Branding + deep editor hooks** | Fork VSCodium | `product.json` + `code.iss` patches, in-tree chat panel |

**Recommendation:** Build the chat **extension first** (works in plain VSCodium today, shippable in days). Fork VSCodium for **branding and deep integration** (terminal execution, multi-file diff overlays, Memory Engine injection) that the extension sandbox cannot reach. The fork is not required for MVP chat — it is required for the *product* to feel like Founder IDE instead of "VSCodium + a plugin."

This is the same lesson Cursor and Windsurf learned: GitHub Copilot is "a feature" because it's an extension; Cursor/Windsurf are "the product" because they fork. **But the chat-provider API now lets an extension own the model layer**, so we can ship the model routing immediately and earn the right to fork the editor for the rest.

---

## 1. VSCodium fork process — what the research actually says

### 1.1 VSCodium is *not* a fork

The VSCodium README states it plainly:

> *"This is not a fork. This is a repository of scripts to automatically build Microsoft's `vscode` repository into freely-licensed binaries with a community-driven default configuration."*

— [github.com/VSCodium/vscodium](https://github.com/vSCodium/vscodium)

**Implication for us:** To make "Founder IDE," we have two options:

- **(A) Downstream of VSCodium** — clone VSCodium's build-script repo, override the branding env vars (`APP_NAME`, `BINARY_NAME`, `ORG_NAME`) and `product.json` patches, run their build. We inherit VSCodium's upstream-sync work for free. **This is the recommended path** — it's the lowest-effort way to stay current with VS Code.
- **(B) Direct downstream of Microsoft's `vscode`** — clone `microsoft/vscode`, run VSCodium's patch scripts ourselves. More control, more merge burden. Only worth it if we diverge so far that VSCodium's patches conflict with ours.

### 1.2 Build prerequisites (Windows)

From [`docs/howto-build.md`](https://github.com/VSCodium/vscodium/blob/master/docs/howto-build.md):

- **Git for Windows** (Git Bash + POSIX utilities: `sed`, `grep`, `find`) — `winget install --id Git.Git -e`
- **Node.js** — exact version pinned in [`.nvmrc`](https://github.com/VSCodium/vscodium/blob/master/.nvmrc); use nvm-windows. Enable "Automatically install the necessary tools" during Node install to get C++ build tools.
- **Python 3.11** (for native addons / `node-gyp`)
- **jq** (VSCodium uses `jq` to patch `product.json` programmatically)
- **7-Zip**
- **Rustup** (for some native modules)
- **Git Bash** is the *recommended* shell on Windows (the scripts rely on POSIX utilities). WSL2 is the alternative.

### 1.3 Build commands

```bash
# Development build (Git Bash — recommended on Windows):
"C:\Program Files\Git\bin\bash.exe" ./dev/build.sh

# Or PowerShell:
powershell -ExecutionPolicy ByPass -File .\dev\build.ps1
```

The `dev/build.sh` script sets these branding env vars ([source](https://github.com/VSCodium/vscodium/blob/master/dev/build.sh)):

```bash
export APP_NAME="VSCodium"
export ASSETS_REPOSITORY="VSCodium/vscodium"
export BINARY_NAME="codium"
export ORG_NAME="VSCodium"
export VSCODE_QUALITY="stable"
```

**For Founder IDE, we override these** (the script reads them from the env):

```bash
export APP_NAME="Founder IDE"
export BINARY_NAME="founder-ide"
export ORG_NAME="Doxxed Crypto"
export ASSETS_REPOSITORY="doxxedcrypto/founder-ide"
```

### 1.4 What the build actually does

The Windows packaging flow (`build/windows/package.sh`, [source](https://github.com/VSCodium/vscodium/blob/bfaa0ebd/build/windows/package.sh)):

1. Extract the upstream `vscode` tarball.
2. `npm ci` inside the vscode tree.
3. `node build/azure-pipelines/distro/mixin-npm.ts` — mixin distro-specific npm deps.
4. Delete prebuilt `.node` native files (forces rebuild for target arch).
5. Run `../build/windows/rtf/make.sh` — generate the RTF license for Inno Setup.
6. Generate Group Policy definitions.
7. `npm run gulp "vscode-win32-${VSCODE_ARCH}-min-ci"` — the actual compilation (minified VS Code build).
8. `build_cli.sh` — build the `founder-ide` CLI (tunnel/reuse-window commands).
9. For x64: optionally build REH (remote server) and REH-web.

**Output formats** come from separate gulp tasks (see the [1.33.1 build.sh](https://github.com/VSCodium/vscodium/blob/1.33.1/build.sh)):

| Gulp task | Output |
|---|---|
| `vscode-win32-x64-min-ci` | The compiled app |
| `vscode-win32-x64-inno-updater` | Inno updater |
| `vscode-win32-x64-user-setup` | **NSIS user installer** (`.exe`) |
| `vscode-win32-x64-system-setup` | **NSIS system installer** (`.exe`) |
| `vscode-win32-x64-archive` | **Portable zip** |

**Critical:** VSCodium/VS Code uses **Inno Setup** (`build/win32/code.iss`), **not** `electron-builder`. Founder Node uses `electron-builder`. These are different toolchains. Bundling the two together needs care (see §6).

### 1.5 Branding — how VSCodium replaces Microsoft strings

The `prepare_vscode.sh` script ([source](https://github.com/VSCodium/vscodium/blob/8cc366bb76d6c0ddb64374f9530b42094646a660/prepare_vscode.sh)) does the rebranding in two ways:

**`product.json` via `jq`** — sets `nameShort`, `nameLong`, `applicationName`, `win32MutexName`, `win32DirName`, `win32RegValueName`, `win32AppUserModelId`, `win32ShellNameShort`, `urlProtocol`, `dataFolderName`, and unique Windows GUIDs (`win32AppId`, `win32x64UserAppId`). **We must generate our own GUIDs** so Founder IDE and VSCodium/VS Code can be installed side-by-side without mutex/installer conflicts.

**`sed` replacements** in platform-specific files — `build/win32/code.iss` (Inno Setup), `resources/linux/debian/postinst.template`, `resources/linux/rpm/code.spec.template`, `snapcraft.yaml`. Replaces URLs (`code.visualstudio.com` → `vscodium.com`), publisher names (`Microsoft Corporation` → `VSCodium`), etc.

**For Founder IDE:** we add a `prepare_founder_ide.sh` that runs *after* VSCodium's `prepare_vscode.sh` and `sed`s VSCodium strings → Founder IDE strings, plus drops our icon (`build/icon.ico`) and splash assets.

### 1.6 Upstream sync cadence

VSCodium tracks Microsoft's `vscode` repo by commit SHA. The `upstream/stable.json` and `upstream/insider.json` files pin the exact commit. Releases ship roughly in step with VS Code's monthly cadence. **If we downstream VSCodium, we inherit their sync work** — we just rebase our `prepare_founder_ide.sh` + chat-panel patches on each VSCodium tag. This is the single biggest reason to be a VSCodium downstream rather than a direct `vscode` downstream.

### 1.7 License

MIT, confirmed. The VSCodium README: *"These binaries are licensed under the MIT license. Telemetry is disabled."* Microsoft's `vscode` source is MIT; the proprietary pieces (marketplace, telemetry, branding) are added by Microsoft's *build* and are exactly what VSCodium strips. **Bundling VSCodium + Founder Node in one installer is fine under MIT** (Founder Node's own code is the user's). See §7 for the one marketplace ToS gotcha.

---

## 2. How Cursor and Windsurf forked VS Code — lessons that apply to us

### 2.1 Cursor

Sources: [How Cursor Actually Works (theaiengineer)](https://theaiengineer.substack.com/p/how-cursor-actually-works), [howworks.ai](https://howworks.ai/blog/how-cursor-actually-works), [Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/cursor), [julien-riel.com](https://julien-riel.com/en/case-studies/cursor/), [dasarpai.com](https://dasarpai.com/dsblog/cursor-chat-architecture-data-flow-storage/).

**What they changed vs kept:**
- **Kept:** The VS Code editor shell, Electron, the extension host, extension compatibility (most VS Code extensions work in Cursor).
- **Changed (framework-level):** Internal rendering pipeline (for inline diff overlays, speculative suggestions), the file system / extension host hooks (for the Shadow Workspace, Background Agents), and the chat UI (native editor component, **not** an extension).
- **Built bespoke:** Priompt (open-sourced at `github.com/anysphere/priompt`) — a JSX-based prompt compiler where each element has a priority score; when the prompt exceeds the token budget, low-priority elements are dropped via binary search. Context engine: Tree-sitter chunking, Merkle-tree sync every ~3–5 min, embeddings in Turbopuffer, a fine-tuned 7B CodeLlama reranker. Composer 2: a custom multi-file-edit model.

**How they built the chat panel:** It is a **native component of the editor binary**, not a VS Code extension. Chat history is stored locally in SQLite (`state.vscdb`) under `globalStorage` and `workspaceStorage`. The server never stores source code — only obfuscated paths + encrypted code chunks + embeddings (metadata only).

**How they handle extensions:** Their own extension marketplace compatibility (they support Open VSX-style + many Microsoft-compiled extensions), plus their own first-party extensions for the AI features.

**Build infrastructure:** VS Code's own build system (gulp + Inno Setup on Windows), inherited from the fork. Not electron-builder.

**The lesson, quoted directly:**
> *"If your AI integration is constrained by the host platform's API, it will always be a feature. A fork lets it become the product."* — [julien-riel.com](https://julien-riel.com/en/case-studies/cursor/)

**The cost, also quoted:**
> *"Every time Microsoft updates VS Code, Cursor must merge upstream changes into a diverging codebase. That's real engineering overhead."*

### 2.2 Windsurf

Sources: [pickuma.com review](https://pickuma.com/for-dev/windsurf-ide-review-ai-native-code-editor/), [lowcode.agency](https://www.lowcode.agency/blog/windsurf-vs-code-fork), [myengineeringpath.dev](https://myengineeringpath.dev/tools/windsurf-ai/), [pixlrun.com](https://pixlrun.com/ai/windsurf/), [devin.ai Wave 7](https://devin.ai/blog/windsurf-wave-7).

**What they changed vs kept:**
- **Kept:** VS Code editor shell, extension compatibility, keybindings, Electron windowing.
- **Built from scratch:** Cascade (the agentic layer) — *not* an extension. SWE-1 (their proprietary planning model). M-Query retrieval engine. A semantic graph from AST parsing (not keyword search). Supercomplete (intent-aware multi-line prediction).

**Why they forked instead of extending — the binding constraint:**
> *"Windsurf's fork architecture allows Cascade to access editor state, terminal output, and file system events natively. A plugin running inside VS Code's extension sandbox cannot reach those same hooks."* — [lowcode.agency](https://www.lowcode.agency/blog/windsurf-vs-code-fork)

> *"Unlike VSCode, where the limited set of APIs exposed to extensions restricted our ability to create a great agentic experience via our VSCode extension (thus prompting the fork of VSCode into the Windsurf Editor)..."* — [Windsurf Wave 7, devin.ai](https://devin.ai/blog/windsurf-wave-7)

This is the same conclusion Cursor reached. **The sandbox is the ceiling for extensions.** Terminal output capture, atomic multi-file diffs, file-system-event interception, and a "plan → approve → execute" agent loop all require editor-level access.

**The pattern that applies to us:** Both Cursor and Windsurf kept the *editor* and replaced the *AI layer*. We are doing the inverse-by-design: we keep the *AI layer* (our Gateway/Routing/Memory/Flight Recorder already exists) and need an *editor* that talks to it. That asymmetry is the core of this report.

---

## 3. Open VSX — extension marketplace for forks

### 3.1 How to point a fork at Open VSX

Edit `product.json` (in `resources/app/product.json` of the built app, or patched at build time) — from the [Open VSX wiki](https://github.com/eclipse-openvsx/openvsx/wiki/Using-Open-VSX-in-VS-Code):

```jsonc
{
  "extensionsGallery": {
    "serviceUrl": "https://open-vsx.org/vscode/gallery",
    "itemUrl": "https://open-vsx.org/vscode/item",
    "resourceUrlTemplate": "https://open-vsx.org/vscode/unpkg/{publisher}/{name}/{version}/{path}",
    "extensionUrlTemplate": "https://open-vsx.org/vscode/gallery/{publisher}/{name}/latest"
  },
  "linkProtectionTrustedDomains": ["https://open-vsx.org"]
}
```

VSCodium **already ships with this configured** (see [docs/extensions.md](https://github.com/VSCodium/vscodium/blob/a6a4322e/docs/extensions.md)). If we downstream VSCodium, we inherit Open VSX by default. We can also self-host via [`openvsx-server`](https://github.com/eclipse/openvsx) if we later want a curated marketplace.

Environment-variable alternative (no file edit): `VSCODE_GALLERY_SERVICE_URL`, `VSCODE_GALLERY_ITEM_URL`, `VSCODE_GALLERY_EXTENSION_URL_TEMPLATE`, `VSCODE_GALLERY_RESOURCE_URL_TEMPLATE`.

### 3.2 What's available vs missing on Open VSX

**Available (confirmed common usage in VSCodium):** Python (Microsoft's open builds), TypeScript, ESLint, Prettier, GitLens, Vim, Material Icon Theme, Docker, YAML, TOML, Go, Rust-analyzer, Java, C/C++, most language packs.

**NOT available on Open VSX (proprietary Microsoft-only):**
- **GitHub Copilot / Copilot Chat** (we replace these with our own — not a problem)
- **Pylance** (the proprietary Python language server) — the open-source `pyright` extension is on Open VSX and is the basis of Pylance anyway; users get 95% of the experience
- **Remote - SSH / Remote - Containers / Remote - WSL** (Microsoft's Remote Development pack) — **this is the biggest gap.** There are community alternatives but none as polished. If the founder needs remote dev, this is a real loss.
- **Live Share** (Microsoft)
- Some Microsoft debugging extensions (e.g. the C# debugger is partially open, the .NET tools vary)

**Mitigation:** For our use case (a founder coding on their own machine with our AI), the missing remote-dev pack is acceptable for Phase 1. We document it as a known limitation and let power users point at the Microsoft marketplace via the `VSCODE_GALLERY_*` env vars (with the ToS caveat below).

### 3.3 The ToS gotcha (§7 risk)

Microsoft's Marketplace Terms of Use restrict the marketplace to "Microsoft products and services." Using the Microsoft marketplace from a non-Microsoft VS Code build is a *grey area* that VSCodium explicitly avoids by defaulting to Open VSX. **We should default to Open VSX and not ship Microsoft-marketplace URLs baked in.**

---

## 4. VS Code extension API for chat — the part that changes the plan

### 4.1 Two stable APIs that matter to us

Both are now **stable, finalized** public APIs (not proposed):

1. **Language Model Chat Provider API** — finalized in VS Code 1.104 (`a18d41e`, 2025-08-27). Docs: [code.visualstudio.com/api/extension-guides/ai/language-model-chat-provider](https://code.visualstudio.com/api/extension-guides/ai/language-model-chat-provider). Lets an extension *provide language models* to VS Code's chat. The built-in Chat view picks them up in the model dropdown.
2. **Chat Participant API** — [code.visualstudio.com/api/extension-guides/ai/chat](https://code.visualstudio.com/api/extension-guides/ai/chat). Lets an extension register a `@participant` (e.g. `@founder`) that handles chat requests, streams responses, and can use tools.

### 4.2 The minimum code to register our AI Gateway as a chat model provider

`package.json` contribution:

```jsonc
{
  "contributes": {
    "languageModelChatProviders": [
      { "vendor": "founder-os", "displayName": "Founder OS" }
    ],
    "chatParticipants": [
      { "id": "founder.founder", "name": "founder", "description": "Founder OS AI (routed via your Gateway)" }
    ]
  }
}
```

`extension.ts`:

```ts
import * as vscode from 'vscode';

class FounderOsChatProvider implements vscode.LanguageModelChatProvider {
  constructor(private gatewayUrl: string, private bearer: string) {}

  provideLanguageModelChatInformation(): vscode.LanguageModelChatInformation[] {
    return [
      { id: 'founder-os-auto',       name: 'Founder OS Auto',       vendor: 'founder-os' },
      { id: 'founder-os-code',       name: 'Founder OS Code',       vendor: 'founder-os' },
      { id: 'founder-os-reasoning',  name: 'Founder OS Reasoning',  vendor: 'founder-os' },
      { id: 'founder-os-fast',       name: 'Founder OS Fast',       vendor: 'founder-os' },
    ];
  }

  async provideLanguageModelChatResponse(
    model: vscode.LanguageModelChatInformation,
    messages: readonly vscode.LanguageModelChatRequestMessage[],
    options: vscode.ProvideLanguageModelChatResponseOptions,
    progress: vscode.Progress<vscode.LanguageModelResponsePart>,
    token: vscode.CancellationToken,
  ): Promise<void> {
    const body = {
      model: model.id,
      messages: messages.map(m => ({ role: m.role, content: m.content })),
      stream: true,
    };
    const res = await fetch(`${this.gatewayUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.bearer}`,
      },
      body: JSON.stringify(body),
      signal: token.isCancellationRequested ? AbortSignal.abort() : undefined,
    });
    if (!res.ok || !res.body) throw new Error(`Gateway ${res.status}`);

    // Parse the SSE stream and report text chunks as they arrive.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') return;
        try {
          const evt = JSON.parse(payload);
          const delta = evt?.choices?.[0]?.delta?.content;
          if (delta) progress.report(new vscode.LanguageModelTextPart(delta));
        } catch { /* ignore malformed chunk */ }
      }
    }
  }

  provideTokenCount(text: string | vscode.LanguageModelChatMessage[]): Thenable<number> {
    const s = typeof text === 'string' ? text : text.map(m => m.content).join('\n');
    return Promise.resolve(Math.ceil(s.length / 4));
  }
}

export function activate(ctx: vscode.ExtensionContext) {
  const cfg = vscode.workspace.getConfiguration('founderOs');
  const gatewayUrl = cfg.get<string>('gatewayUrl')!;
  const bearer = cfg.get<string>('bearerToken')!;
  ctx.subscriptions.push(
    vscode.lm.registerLanguageModelChatProvider('founder-os', new FounderOsChatProvider(gatewayUrl, bearer))
  );
}
```

**That is the entire MVP chat panel.** The built-in VS Code Chat view (`workbench.action.chat.open`) renders the conversation, handles the model dropdown, streaming UI, markdown, and tool-use surface. We provide the model; it provides the UI.

### 4.3 How does the chat panel trigger file edits and terminal commands?

This is where the extension API has *real* power and where the "do we need a fork?" question gets nuanced:

- **File edits from chat** — the Chat Participant handler receives a `ChatContext` with `vscode.workspace` access. The extension can call `vscode.workspace.openTextDocument`, `editor.edit(...)`, `workspace.applyEdit(workspaceEdit)`, and `commands.executeCommand('vscode.diff', ...)`. The Chat Participant API supports **tool use**: you declare tools via `vscode.lm.registerTool` and the model can call them. Cursor's apply-patch / find-replace edit tools map onto this.
- **Terminal commands from chat** — `vscode.window.createTerminal({ name })` then `terminal.sendText(cmd)`, and `vscode.tasks` for task-based execution. You can read terminal output via the [`vscode.window.onDidWriteTerminalData` *proposed* API] or by writing to a file and reading it back. **Reading arbitrary terminal output is still a proposed/limited API** — this is one place the extension sandbox genuinely is weaker than a fork. (See Windsurf quote in §2.2.)
- **Tool-use loop** — `vscode.lm.registerTool(id, { invoke, prepare })` lets the model call structured tools (open file, run shell command, web search) and receive results. The Chat API handles the tool-call/tool-result round-trips. This is exactly how Cursor's Composer and Windsurf's Cascade structure their agent loops, and it *is* reachable from an extension now.

**Honest assessment:** File editing from chat is fully doable as an extension. Terminal execution is doable but **reading terminal output back into the model** is the one agentic capability that is materially harder as an extension than as a fork. For Phase 1–2 we can run commands blind (send and show the user the terminal), or write a small wrapper that captures stdout to a temp file and reads it back. For a true self-correcting agent (like Cascade reading build errors and retrying), a fork is materially better.

### 4.4 The decision: extension vs editor fork

| Capability | Extension (stable API) | Editor fork |
|---|---|---|
| Chat UI with streaming | ✅ Built-in Chat view | ✅ Custom UI |
| Call our `/v1/chat/completions` with SSE | ✅ | ✅ |
| Model dropdown (Turbo/Balanced/Architect) | ✅ via `provideLanguageModelChatInformation` | ✅ |
| File edits from chat (applyEdit, diff) | ✅ | ✅ |
| Tool-use loop (run command → read result) | ⚠️ Run yes, read-back limited | ✅ Native |
| Show DDollar cost per message | ⚠️ Via status-bar item / chat followup | ✅ Inline in chat |
| Memory Engine context injection | ✅ In provider before fetch | ✅ |
| Flight Recorder logging | ✅ Server already does it | ✅ |
| Terminal output → model feedback loop | ❌ Hard (proposed API) | ✅ |
| Multi-file diff overlay UI | ⚠️ `vscode.diff` per file | ✅ Atomic review |
| Custom branding (icons, splash, name) | ❌ | ✅ |
| Bundle with Founder Node (one download) | ⚠️ Two apps in one installer | ✅ One process tree |
| Upstream sync cost | None (it's an extension) | Real merge burden |

**Verdict:** Phase 1 = extension. Phase 2 = add the fork for branding + the agentic terminal-feedback loop + bundling. The extension remains the *router* layer even after the fork (the fork's in-tree chat can call the same `FounderOsChatProvider` logic).

---

## 5. Integration points with our existing code

### 5.1 The Gateway is already OpenAI-compatible and SSE-streaming

`apps/api/src/ai-proxy/ai-proxy.controller.ts`:

```98:143:apps/api/src/ai-proxy/ai-proxy.controller.ts
  /** OpenAI-compatible /v1/chat/completions — routes through the proxy. */
  @UseGuards(FounderNodeGuard)
  @Post('chat/completions')
  async chatCompletions(
    @Req() req: { founderNode: FounderNodeRequestUser },
    @Body() body: ChatCompletionRequestDto,
    @Res({ passthrough: true }) res: Response,
  ): Promise<unknown> {
    const auth: ProxyAuth = {
      userId: req.founderNode.userId,
      nodeId: req.founderNode.nodeId,
    };

    const route = await this.runtimeService.decideRoute(auth, body);
    const result = await this.runtimeService.invoke(auth, body, route);
    // ... error handling ...
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    const nodeStream = Readable.fromWeb(result.body as unknown as Parameters<typeof Readable.fromWeb>[0]);
    nodeStream.pipe(res);
    return null;
  }
```

The route is mounted under `/v1` but the API has a global `/api` prefix (see `connect-ide.ts` `proxyBaseUrl`):

```32:36:apps/founder-node/src/connect-ide.ts
export function proxyBaseUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/$/, '')}/api/v1`;
}

export function bearerFromConfig(config: FounderNodeConfig): string {
  return `fos_${config.nodeId}:${config.nodeToken}`;
}
```

**So Founder IDE's chat provider calls:**
- URL: `{apiBaseUrl}/api/v1/chat/completions` (e.g. `https://doxxedcrypto.digital/api/v1/chat/completions`)
- Header: `Authorization: Bearer fos_{nodeId}:{nodeToken}` (the `FounderNodeGuard` accepts the `fos_` bearer form; the existing `connectCursor` writes exactly this into Cursor's `settings.json`)
- Body: standard OpenAI `ChatCompletionRequestDto`
- `stream: true` → `text/event-stream` with `data: {chunk}\n\n` lines and a terminal `data: [DONE]`

### 5.2 Model aliases the IDE will expose

`apps/api/src/ai-proxy/ai-proxy.constants.ts`:

```9:23:apps/api/src/ai-proxy/ai-proxy.constants.ts
export const FOUNDER_OS_AUTO_MODEL = 'founder-os-auto';
export const MODEL_ALIASES = [
  FOUNDER_OS_AUTO_MODEL,
  'founder-os-code',
  'founder-os-reasoning',
  'founder-os-fast',
] as const;
```

These map 1:1 to the `LanguageModelChatInformation` entries we expose in the chat-provider extension (§4.2). The IDE's model dropdown = our routing tier selector. **The user already has "execution profile" semantics for free** — `founder-os-fast` = Turbo, `-code` = Architect, `-reasoning` = Balanced/Architect, `-auto` = Autonomous (let the Routing Engine decide).

### 5.3 What the Routing Engine / Flight Recorder / DDollar already give us

From `ai-proxy-runtime.service.ts`, every request already:
- Decides provider+model+tier (`decideRoute`, with `USE_ROUTING_ENGINE_V2` for the v2 cache→capability→scoring path)
- Spends DDollars (`spendingEngine.spend` with `AI_PROXY_DDOLLAR_COST[route.tier]`)
- Writes an `AiTokenUsageLog` row
- Writes a Flight Recorder decision row (`flightRecorder.record` or `.updateUsage`)
- Feeds the Learning Engine retry detector
- Computes USD cost from the Capability Registry

**This means the IDE does not need to re-implement any of this.** The Gateway already does it server-side. The IDE only needs to **display** the results. To show per-message DDollar cost and chosen model in the chat UI, we need a way to surface the route decision back to the client. Two options:

- **(A) Custom SSE event** — the Gateway injects a non-standard `data: {"founderOs":{"tier":"code","model":"glm-4.6","ddollarCost":2,"requestId":"..."}}\n\n` line *before* the first OpenAI chunk. The chat provider parses it, surfaces it as a chat followup / status bar update, and the rest is standard streaming. **Requires a small server-side addition** (a one-line `res.write` before piping the upstream stream).
- **(B) Separate `/v1/last-route` poll** — the IDE polls a new endpoint with the `requestId`. More latency, more complexity. Not recommended.

**Recommendation:** option (A), gated behind a `founder_os_metadata: true` field in the request body so standard OpenAI clients are unaffected.

### 5.4 Auth — how the IDE gets its token

The Founder Node already owns the credential lifecycle (`vault-manager.ts` `readNodeConfig` returns `{ apiBaseUrl, nodeId, nodeToken, ... }`). The existing `connect-ide.ts` writes these into Cursor's `settings.json` and shell profiles. For Founder IDE we have three options:

1. **Founder IDE reads the same vault config file** — Founder IDE's extension reads `~/FounderVault/node-config.json` directly on activation. Zero new infra. Works because Founder IDE is bundled with Founder Node and runs on the same machine.
2. **Founder Node exposes a localhost IPC** — Founder Node's Electron main process adds an IPC handler and a tiny localhost HTTP server (e.g. `127.0.0.1:7801/credentials`) that the IDE extension fetches. Cleaner separation; supports token rotation.
3. **User pastes a pairing code into the IDE** — same flow as today's Cursor connect, but in-IDE. Worst UX; only useful if Founder Node isn't installed.

**Recommendation:** Option 1 for Phase 1 (simplest, reuses existing vault), option 2 for Phase 2 (enables bundling without shared filesystem assumptions).

### 5.5 Bundling Founder IDE with Founder Node

Founder Node is an `electron-builder` NSIS tray app (`apps/founder-node/package.json`, `appId: digital.doxxedcrypto.founder-node`, `electronVersion: 35.1.5`). Founder IDE is a VSCodium (Inno Setup) editor. They are **two different Electron apps with two different build toolchains**.

Bundling options:

| Option | How | Pros | Cons |
|---|---|---|---|
| **One installer, two apps** | NSIS wrapper installs Founder Node to `%LOCALAPPDATA%\FounderNode` and Founder IDE to `%LOCALAPPDATA%\Founder IDE`, registers both Start Menu shortcuts | Clean separation; each app updates independently | Two uninstall entries; bigger installer |
| **IDE embeds Node as a child process** | Founder IDE's main process spawns the Founder Node Electron main as a background child on startup | One app, one tray | Diverges from VSCodium's build; harder to update Node independently |
| **Node embeds IDE** | Founder Node spawns Founder IDE on demand | Single entry point | Founder Node is a tray app, not a window manager — wrong shape |

**Recommendation:** Option 1 (one installer, two apps). Use a thin top-level NSIS bootstrapper (or a single `.exe` from `electron-builder`'s `nsis.web` installer) that lays down both payloads. The Founder IDE extension reads Founder Node's vault for credentials (§5.4 option 1). This keeps the two build pipelines independent and lets us ship IDE updates without re-shipping Node.

---

## 6. Build process — Windows installer + bundling

### 6.1 Founder IDE build (downstream of VSCodium)

```bash
# 1. Clone our fork (downstream of VSCodium)
git clone --recurse-submodules https://github.com/doxxedcrypto/founder-ide
cd founder-ide

# 2. Install build prereqs (one-time)
winget install --id Git.Git -e
nvm install $(cat .nvmrc) && nvm use $(cat .nvmrc)
winget install --id Python.Python.3.11 -e
winget install --id jqlang.jq -e
winget install --id 7zip.7zip -e

# 3. Set Founder IDE branding (overrides VSCodium defaults)
export APP_NAME="Founder IDE"
export BINARY_NAME="founder-ide"
export ORG_NAME="Doxxed Crypto"
export ASSETS_REPOSITORY="doxxedcrypto/founder-ide"
export VSCODE_QUALITY="stable"

# 4. Apply our patches on top of VSCodium's
./founder-ide/prepare_founder_ide.sh   # seds VSCodium -> Founder IDE, drops our icon.ico/splash

# 5. Build (Git Bash)
"C:\Program Files\Git\bin\bash.exe" ./dev/build.sh

# 6. Outputs (in VSCode/.../win32-x64):
#    - Founder-IDE-Setup-x64.exe      (NSIS user installer)
#    - Founder-IDE-x64-portable.zip   (portable)
```

### 6.2 Customizing the installer (name, icons, splash)

- **App name / mutex / GUIDs** — patch `product.json` via `jq` in `prepare_founder_ide.sh`. **Generate fresh GUIDs** for `win32AppId` / `win32x64UserAppId` so installs don't collide with VSCodium/VS Code.
- **Icons** — replace `resources/app/resources/win32/code.ico` (and the Inno Setup `installerIcon`) with our `icon.ico`.
- **Installer branding** — `sed` the `build/win32/code.iss` Inno Setup script: app name, publisher, URLs, license text. This is exactly what VSCodium's `prepare_vscode.sh` does; we layer on top.
- **Splash** — VS Code doesn't have a traditional splash screen, but the loading window background can be themed via `product.json` `welcomePage` + a custom CSS patch (VSCodium already does CSS patches for telemetry removal; we extend that file).

### 6.3 Bundled installer (Founder IDE + Founder Node)

Build each separately, then compose:

```bash
# Founder IDE -> founder-ide-Setup.exe  (Inno Setup)
# Founder Node -> Founder-Node-<v>-win-x64.exe  (electron-builder NSIS)

# Compose with a bootstrapper NSIS script:
makensis bundle.nsi   # installs both, two Start Menu shortcuts, one uninstall entry per app
```

`bundle.nsi` (sketch):

```nsis
OutFile "Founder-Stack-Installer.exe"
InstallDir "$LOCALAPPDATA\FounderStack"
Section "Founder IDE"
  SetOutPath "$INSTDIR\FounderIDE"
  File "founder-ide-Setup.exe"
  ExecWait '"$INSTDIR\FounderIDE\founder-ide-Setup.exe" /SILENT'
SectionEnd
Section "Founder Node"
  SetOutPath "$INSTDIR\FounderNode"
  File "Founder-Node-x64.exe"
  ExecWait '"$INSTDIR\FounderNode\Founder-Node-x64.exe" /SILENT'
SectionEnd
```

### 6.4 Disk space + time estimates

- **Disk for a VSCodium build:** ~25–40 GB (the vscode repo + `node_modules` + build artifacts + `.build` cache). Plan for 50 GB free to be safe.
- **Windows build time:** ~45–90 min on a modern 8-core machine for a clean build; incremental ~10–20 min. CI (`windows-2022` runner) is similar. These are estimates from the workflow complexity, not a measured number — **the first local build is the slowest** because of native module compilation.
- **Founder Node build:** ~2–5 min (small Electron app, `electron-builder`).

---

## 7. Strip vs keep vs add

### Strip
- VSCodium branding strings (name, publisher, URLs) → replace with Founder IDE
- VSCodium's default `product.json` extensionsGallery is fine (Open VSX) — keep, don't strip
- Any remaining "Codium" references in `code.iss`, postinst templates, snapcraft (we don't ship snap)
- Microsoft marketplace URLs (already stripped by VSCodium — verify, don't re-add)

### Keep
- **All editor functionality** — file tree, multi-cursor, search, terminals, debugging, settings sync off by default
- **The full VS Code extension API** — so VS Code extensions work (this is the whole point of forking vs building from scratch)
- **Open VSX marketplace** — already configured by VSCodium
- **The built-in Chat view** — we use it as our chat UI in Phase 1 (via the LM Chat Provider extension)
- **Terminal integration, debugging, source control, tasks** — unchanged
- **Keybindings, themes, settings schema** — unchanged

### Add
- **Founder OS branding** — `product.json` name/icon/GUID, `code.iss` publisher, splash, app icon
- **Founder OS chat extension** (bundled as a built-in extension in `product.json` `builtInExtensions`) — the `LanguageModelChatProvider` from §4.2, pre-installed so the user doesn't need to fetch it from Open VSX
- **Founder Node credential bridge** — extension reads `~/FounderVault/node-config.json` (Phase 1) or localhost IPC (Phase 2)
- **DDollar cost display** — status bar item + chat followup, fed by the custom SSE metadata event (§5.3)
- **Execution-profile selector** — the model dropdown *is* this (founder-os-auto/code/reasoning/fast)
- **Memory Engine context injection** — the chat provider prepends project/founder memory to the system message before calling the Gateway
- **Routing transparency** — show chosen provider+model+tier per message (from the SSE metadata)
- **Tool use** — `vscode.lm.registerTool` for file-edit, run-command, web-search tools (Phase 3)
- **Founder IDE themes** — a default dark/light theme matching the Founder OS brand

---

## 8. Chat panel architecture — the design

### 8.1 Layered architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Founder IDE (VSCodium fork)                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Built-in VS Code Chat View (workbench.action.chat)  │  │
│  │  Model dropdown: [founder-os-auto ▾]  Cost: 2 D$     │  │
│  └───────────────▲───────────────────────────────────────┘  │
│                  │ vscode.lm API                             │
│  ┌───────────────┴───────────────────────────────────────┐  │
│  │  founder-os-chat extension (built-in)                 │  │
│  │  - FounderOsChatProvider (LanguageModelChatProvider)  │  │
│  │  - Memory preflight (injects project/founder memory)  │  │
│  │  - SSE parser → progress.report(TextPart)             │  │
│  │  - Tools: edit-file, run-command, web-search          │  │
│  └───────────────●───────────────────────────────────────┘  │
│                  │ fetch (OpenAI-compat)                     │
└──────────────────┼──────────────────────────────────────────┘
                   │ HTTPS, Authorization: Bearer fos_node:tok
                   ▼
┌──────────────────────────────────────────────────────────────┐
│  Founder OS API — /api/v1/chat/completions                   │
│  AiProxyController → AiProxyRuntimeService                   │
│    decideRoute (cache → capability gate → scoring)           │
│    invoke (GLM / DeepSeek, streaming)                        │
│    afterRequest (DDollar spend, usage log, Flight Recorder)  │
└──────────────────────────────────────────────────────────────┘
```

### 8.2 Streaming design

- Client sends `{...,"stream":true,"founder_os_metadata":true}`.
- Server: `decideRoute` → `invoke` → writes a one-line `data: {"founderOs":{"requestId":"...","tier":"code","provider":"glm","model":"glm-4.6","ddollarCost":2}}\n\n` *before* piping the upstream SSE.
- Server pipes the upstream `text/event-stream` through (already implemented via `Readable.fromWeb(...).pipe(res)`).
- Client (extension): parse each `data:` line; route `founderOs` lines to the status-bar cost item; route OpenAI `choices[0].delta.content` to `progress.report(new LanguageModelTextPart(delta))`.
- On `data: [DONE]`, the provider returns; the Chat view finalizes the turn.

### 8.3 Memory Engine injection

Before `fetch`, the provider calls a `buildSystemPrompt()` that:
1. Reads project memory (e.g. `.founder/project-memory.json` in the workspace) — coding conventions, active task, recent decisions.
2. Reads founder memory (from the Founder Node vault or the `/api/memory` endpoint) — founder preferences, past Flight Recorder patterns.
3. Prepends these as a `system` message at the start of the `messages` array sent to the Gateway.

The Gateway's Routing Engine never sees this difference — it just routes the resulting prompt. The Flight Recorder logs the full prompt hash so Memory injection is observable.

### 8.4 Tool use (Phase 3)

`vscode.lm.registerTool('founder.editFile', { prepare, invoke })` — the model emits a `LanguageModelToolCallPart`; the Chat API routes it to our `invoke`, which calls `vscode.workspace.applyEdit`. Result returned as `LanguageModelToolResultPart`. Same pattern for `founder.runCommand` (creates a terminal, sends text, captures output to a temp file, reads it back — the workaround for the limited terminal-read API). And `founder.webSearch` if we wire it.

---

## 9. Phased implementation plan

### Phase 1 — Minimum viable chat (3–5 days, no fork required)

**Goal:** A founder can install plain VSCodium, install our extension, and chat with their Founder OS AI in the built-in Chat view, streaming, routed through their Gateway.

**Tasks:**
1. Create `apps/founder-ide-extension/` (a VS Code extension project, `vsce package` → `.vsix`).
2. Implement `FounderOsChatProvider` (§4.2) with the four model aliases.
3. Implement credential discovery: read `~/FounderVault/node-config.json` on activation; fall back to `founderOs.gatewayUrl` + `founderOs.bearerToken` settings.
4. Implement SSE parsing → `progress.report(LanguageModelTextPart)`.
5. Add a status-bar item showing the last request's tier + DDollar cost (parse the `founderOs` metadata line).
6. Server: add the `founder_os_metadata` SSE pre-line in `ai-proxy.controller.ts` (one `res.write` before the pipe; ~10 lines).
7. Add a `founder.founder` chat participant that wraps the provider with a default system prompt.
8. `vsce package` → ship the `.vsix` via Open VSX and via a direct download.
9. Docs: a one-page "Connect Founder IDE" guide mirroring the existing Cursor connect flow.

**Files to create/modify:**
- `apps/founder-ide-extension/package.json` + `extension.ts` + `provider.ts` + `sse.ts` + `credentials.ts` + `status-bar.ts` (new)
- `apps/api/src/ai-proxy/ai-proxy.controller.ts` — add metadata pre-line (modify)
- `apps/api/src/ai-proxy/dto/ai-proxy.dto.ts` — add optional `founder_os_metadata?: boolean` (modify)
- `docs/FOUNDER-IDE-CONNECT.md` (new)

**Dependencies:** `@types/vscode`, `vsce`. No new runtime deps on the server.

**Acceptance criteria:**
- Install VSCodium → install our `.vsix` → open Chat view → select "Founder OS Auto" → type a question → tokens stream in real time.
- Status bar shows `Founder OS: code · 2 D$` after each message.
- `/v1/usage` (already exists) reflects the spend.
- Flight Recorder has a row for each IDE message.
- Works with the existing Founder Node bearer (no new auth).

### Phase 2 — Fork VSCodium + bundle with Founder Node (10–14 days)

**Goal:** A single "Founder Stack" Windows installer that installs a rebranded "Founder IDE" + Founder Node, with the chat extension pre-built-in.

**Tasks:**
1. Create `services/founder-ide-build/` — a downstream of VSCodium's build-script repo as a git submodule or vendored copy.
2. Write `prepare_founder_ide.sh` — runs after VSCodium's `prepare_vscode.sh`: `sed` VSCodium→Founder IDE strings, patch `product.json` with our GUIDs/name/icon, patch `code.iss` publisher/URLs.
3. Add our icon.ico, splash, and a default Founder OS theme.
4. Add the `founder-ide-extension` to `product.json` `builtInExtensions` so it ships inside the IDE (no Open VSX fetch needed for the chat provider).
5. Generate fresh Windows GUIDs; set `win32AppUserModelId`, `urlProtocol: founder-ide`, `dataFolderName: .founder-ide`.
6. Build a Windows NSIS user installer + portable zip via the VSCodium build flow.
7. Write `bundle.nsi` (§6.3) — composes Founder IDE installer + Founder Node installer into one `Founder-Stack-Installer.exe`.
8. Set up CI (GitHub Actions, `windows-2022` runner, mirror of VSCodium's `stable-windows.yml`).
9. Founder Node: add a localhost credentials endpoint (§5.4 option 2) and update the extension to prefer it over reading the vault file (with fallback).
10. End-to-end test: install Founder Stack → Founder Node pairs → open Founder IDE → chat works with no manual config.

**Files to create/modify:**
- `services/founder-ide-build/` — the fork/downstream (new directory)
- `services/founder-ide-build/prepare_founder_ide.sh` (new)
- `services/founder-ide-build/branding/product.json.patch.jq` (new)
- `services/founder-ide-build/branding/icon.ico` + `splash.png` (new assets)
- `services/founder-ide-build/bundle.nsi` (new)
- `.github/workflows/founder-ide-windows.yml` (new CI)
- `apps/founder-ide-extension/src/credentials.ts` — add localhost IPC fetch (modify)
- `apps/founder-node/src/main.ts` — add localhost credentials server (modify, ~30 lines)

**Dependencies (build-time):** Node (.nvmrc), Python 3.11, jq, 7-Zip, Rustup, Git Bash, NSIS (`makensis`).

**Acceptance criteria:**
- `Founder-Stack-Installer.exe` installs both apps; both appear in Start Menu.
- Founder IDE shows "Founder IDE" in the title bar, taskbar, and Add/Remove Programs (not "VSCodium" / "VS Code").
- Founder IDE and VS Code can be installed side-by-side (no mutex/installer conflict).
- Chat works with zero manual configuration after Founder Node is paired.
- The bundled extension is present even with no internet (built-in, not fetched).

### Phase 3 — Agentic features + deep integration (10–14 days)

**Goal:** The chat can edit files, run commands, and self-correct from terminal output — approaching the Cursor/Windsurf agentic experience.

**Tasks:**
1. Implement `founder.editFile` tool via `vscode.lm.registerTool` — model emits edits, we apply with `workspace.applyEdit`, show a diff review view.
2. Implement `founder.runCommand` tool — `createTerminal` + `sendText`; capture output by writing to a temp file and reading it back (the extension-API workaround for limited terminal-read).
3. Implement `founder.readProject` tool — walks the workspace, returns file tree + selected file contents (with token budgeting).
4. Memory Engine integration — `buildSystemPrompt()` reads from a real Memory Engine endpoint (replace the Phase 1 static `.founder/project-memory.json` with the live store).
5. Execution-profile UI — surface the four aliases as named profiles in the Chat view (Turbo/Balanced/Architect/Autonomous) via a Quick Pick.
6. DDollar cost in the chat turn (not just status bar) — use a chat followup or a custom chat render strategy.
7. Open VSX curation — verify the extensions our founders need are available; document the missing ones (Remote-SSH etc.).
8. Custom Founder OS themes (dark + light) shipped as built-in themes.
9. Optional: in-tree chat panel (fork-level) for the terminal-output feedback loop that the extension can't fully do. Only if Phase 3 tool use proves insufficient.

**Files to create/modify:**
- `apps/founder-ide-extension/src/tools/edit-file.ts`, `run-command.ts`, `read-project.ts` (new)
- `apps/founder-ide-extension/src/memory/system-prompt.ts` (modify to use live Memory Engine)
- `apps/founder-ide-extension/src/profiles.ts` (new)
- `apps/founder-ide-extension/themes/founder-dark.json`, `founder-light.json` (new)
- `apps/api/src/memory/` — expose a `/api/memory/context` endpoint if not already present (verify)

**Dependencies:** `@types/vscode` (already), no new server deps.

**Acceptance criteria:**
- In chat, "add a JSDoc comment to every exported function in src/utils and run `npm run lint`" results in editable diffs + a terminal that runs lint + the model reading the lint output and fixing errors.
- Memory Engine context appears in the Flight Recorder prompt hash for each IDE message.
- Execution profiles are selectable and the chosen tier shows in the chat.
- DDollar cost is visible per turn in the chat UI.
- The two missing-marketplace extensions we care about are documented.

---

## 10. Risks and unknowns

| Risk | Severity | Mitigation |
|---|---|---|
| **Upstream sync burden** — every monthly VS Code release requires rebasing our `prepare_founder_ide.sh` + extension patches | High | Downstream VSCodium (inherits their sync). Keep our diff *additive* (a patch script that runs after theirs) rather than editing their files. Budget ~2–4 days/month for rebase. |
| **Build breaks when VS Code updates and we're behind** | Medium | Pin to VSCodium tags, not `master`. CI builds on every PR. The extension (Phase 1) is unaffected by VS Code internal changes — only the fork is. |
| **Disk space for build** — ~50 GB free needed | Low | Use CI runners (GitHub Actions `windows-2022`) for release builds; local dev only for the extension. |
| **Windows build time** — 45–90 min clean | Low | Incremental builds ~10–20 min. CI runs in background. Don't block local dev on full rebuilds. |
| **License gotcha bundling VSCodium + Founder Node** | Low | Both MIT-compatible. Preserve VSCodium's LICENSE.txt in our install dir. Don't bake Microsoft marketplace URLs in (ToS). Default to Open VSX. |
| **Extensions missing on Open VSX** — Remote-SSH/Containers/WSL, Pylance, Live Share | Medium | Document as known limitation. `pyright` covers most of Pylance. Remote-dev is a Phase 3+ concern; for a single-founder local-machine product this is acceptable. Power users can set the `VSCODE_GALLERY_*` env vars (their ToS risk). |
| **Terminal output → model feedback loop is limited in the extension API** | Medium | Phase 3 workaround: write command output to a temp file, read it back, feed to the model. If insufficient, the Phase 3 "in-tree chat panel" task in the fork gives native terminal-read. |
| **GUID collision with VSCodium/VS Code** | High if missed | Generate fresh GUIDs for `win32AppId`/`win32x64UserAppId` in `prepare_founder_ide.sh`. Test side-by-side install before any release. |
| **Founder Node + Founder IDE version skew** (token format changes) | Medium | The localhost credentials endpoint (Phase 2) returns a versioned payload. Extension supports N-1 versions. |
| **Open VSX extension availability drift** | Low | Periodic check script that queries Open VSX for our must-have list. |
| **VS Code chat API breaking change** | Low | The LM Chat Provider API is *stable* since 1.104. Proposed APIs (terminal-read) we avoid or gate behind version checks. |
| **Code signing** (SmartScreen warnings on unsigned installer) | Medium | Reuse the Founder Node signing setup (`apps/founder-node/build/sign.js`). Same cert, different appId. |

---

## 11. Effort estimate

| Phase | Scope | Estimated effort | Calendar |
|---|---|---|---|
| **Phase 1** | Chat extension, works in plain VSCodium, SSE streaming, status-bar cost | 3–5 dev-days | 1 week (incl. the server-side metadata line + testing) |
| **Phase 2** | Fork VSCodium, rebrand, Windows installer, bundle with Founder Node, CI | 10–14 dev-days | 2 weeks (the first clean Windows build + GUID/signing is the long pole) |
| **Phase 3** | Tool use (edit/run/read), Memory Engine, execution-profile UI, themes, Open VSX curation | 10–14 dev-days | 2 weeks |
| **Total to feature-complete Founder IDE** | | **23–33 dev-days** | **5–6 weeks** of focused work |

Phase 1 is shippable *independently* and immediately useful — a founder can use it with stock VSCodium today. That de-risks the whole project: we get real usage feedback on the Gateway-as-chat-provider before investing in the fork.

---

## 12. Open questions for the user

1. **Downstream of VSCodium, or direct downstream of `microsoft/vscode`?** Recommendation: VSCodium (inherits upstream sync). Confirm.
2. **One installer (Founder Stack) or two separate downloads (Founder IDE + Founder Node)?** Recommendation: one bundled installer for the public, but ship them separately too for power users. Confirm.
3. **Do we need Remote-SSH / Remote-Containers support?** If yes, Open VSX is a real gap and we need a plan (self-host a marketplace mirror, or accept the Microsoft-marketplace ToS risk). If no (single-machine founder), it's a non-issue.
4. **Memory Engine endpoint** — does `/api/memory/context` already exist, or do we build it in Phase 3? (Needs a quick code check before Phase 3.)
5. **The custom SSE metadata line (`founder_os_metadata: true`)** — acceptable to add a non-standard field to the OpenAI-compatible request body? It's opt-in and ignored by standard clients. Recommendation: yes.
6. **Code signing** — reuse the Founder Node cert for the Founder IDE appId, or a separate cert? Recommendation: reuse.
7. **Branding specifics** — final app name ("Founder IDE"? "Founder Code"?), icon assets, default theme colors. Needed before Phase 2 build.
8. **In-tree chat panel vs extension-only for Phase 3?** Recommendation: extension-only unless the terminal-output feedback loop proves insufficient. Defer the decision to end of Phase 3.

---

## 13. References

**VSCodium build**
- VSCodium repo: https://github.com/VSCodium/vscodium
- Build docs: https://github.com/VSCodium/vscodium/blob/master/docs/howto-build.md
- `dev/build.sh`: https://github.com/VSCodium/vscodium/blob/master/dev/build.sh
- `build/windows/package.sh`: https://github.com/VSCodium/vscodium/blob/bfaa0ebd/build/windows/package.sh
- `prepare_vscode.sh` (branding): https://github.com/VSCodium/vscodium/blob/8cc366bb76d6c0ddb64374f9530b42094646a660/prepare_vscode.sh
- Branding & binary naming (DeepWiki): https://deepwiki.com/VSCodium/vscodium/6.2-branding-and-binary-naming
- Windows CI workflow: https://github.com/VSCodium/vscodium/blob/master/.github/workflows/stable-windows.yml
- Extensions doc: https://github.com/VSCodium/vscodium/blob/a6a4322e/docs/extensions.md

**Cursor fork**
- How Cursor Actually Works: https://theaiengineer.substack.com/p/how-cursor-actually-works
- How Cursor Actually Works (howworks.ai): https://howworks.ai/blog/how-cursor-actually-works
- Cursor architecture case study: https://julien-riel.com/en/case-studies/cursor/
- Pragmatic Engineer — building Cursor: https://newsletter.pragmaticengineer.com/p/cursor
- Cursor chat storage: https://dasarpai.com/dsblog/cursor-chat-architecture-data-flow-storage/
- Priompt (open-sourced): https://github.com/anysphere/priompt

**Windsurf fork**
- Windsurf IDE review: https://pickuma.com/for-dev/windsurf-ide-review-ai-native-code-editor/
- Windsurf vs VS Code forks: https://www.lowcode.agency/blog/windsurf-vs-code-fork
- Windsurf AI guide: https://myengineeringpath.dev/tools/windsurf-ai/
- Windsurf Wave 7 (JetBrains + naming): https://devin.ai/blog/windsurf-wave-7
- Windsurf (pixlrun): https://pixlrun.com/ai/windsurf/

**Open VSX**
- Using Open VSX in VS Code (wiki): https://github.com/eclipse-openvsx/openvsx/wiki/Using-Open-VSX-in-VS-Code
- Marketplace switcher: https://github.com/AbdulOhab/VSCodium-marketplace-switcher
- Enable marketplace on forks (gist): https://gist.github.com/anxkhn/9ae7b2248999168b73f303dec5851460

**VS Code extension API for chat**
- Language Model Chat Provider API: https://code.visualstudio.com/api/extension-guides/ai/language-model-chat-provider
- Chat Participant API: https://code.visualstudio.com/api/extension-guides/ai/chat
- VS Code API reference (lm + chat namespaces): https://code.visualstudio.com/api/references/vscode-api
- Finalize LM API commit (1.104, 2025-08-27): https://github.com/microsoft/vscode/commit/a18d41e90a83fbe551fd7db4894a54cb7d436966
- Theia issue confirming stable in 1.104.x: https://github.com/eclipse-theia/theia/issues/16260

**Electron packaging**
- electron-builder NSIS docs: https://www.electron.build/docs/nsis/
- Building desktop apps with electron-builder: https://medium.com/@jamzi/building-desktop-applications-with-electron-electron-builder-47484193cbcc

**Our existing code (read for this report)**
- `apps/api/src/ai-proxy/ai-proxy.controller.ts` — OpenAI-compatible `/v1/chat/completions` with SSE
- `apps/api/src/ai-proxy/ai-proxy-runtime.service.ts` — Routing Engine v2, DDollar spend, Flight Recorder
- `apps/api/src/ai-proxy/dto/ai-proxy.dto.ts` — request/response shapes
- `apps/api/src/ai-proxy/ai-proxy.constants.ts` — model aliases, feature flags
- `packages/utils/src/ai-proxy.ts` — shared types, DDollar cost map
- `apps/founder-node/src/main.ts` — Founder Node Electron tray app
- `apps/founder-node/src/connect-ide.ts` — existing pattern for wiring proxy creds into an IDE
- `apps/founder-node/src/cursor-discovery.ts` — reads Cursor's `state.vscdb` (the pain a fork removes)
- `apps/founder-node/src/cursor-dispatch.ts` — clipboard + SendKeys paste (the pain a fork removes)
- `apps/founder-node/package.json` — electron-builder setup, appId, NSIS config
- `config/bot-architecture.lock.json` — read per workspace rule; not modified
