# Founder — IDE Control Surface

Adds the Founder control center, account sign-in, embedded Node connection, and [Founder OS](https://doxxedcrypto.digital) AI Gateway to Founder IDE and compatible VS Code-based editors.

Works in **Cursor, VSCodium, stock VS Code, and Windsurf** — no editor fork required.

> Phase 1 of the Founder IDE roadmap. See `docs/FOUNDER-IDE-FORK-PLAN.md` for the full design.

## What it does

- Registers the `founder-os` vendor in VS Code's Chat view model picker.
- Adds a Founder Activity Bar hub with Local, Hybrid, and Founder Cloud modes.
- Registers Founder as an IDE account provider with the X device-code sign-in.
- Calls our OpenAI-compatible endpoint `/api/v1/chat/completions` with `stream: true`.
- Streams tokens in real time to the built-in Chat view via `progress.report(new LanguageModelTextPart(delta))`.
- Authenticates with Founder Node credentials (`Bearer fos_{nodeId}:{nodeToken}`).
- Exposes four model aliases that map to the gateway's execution profiles:

  | Alias | Profile | Tier |
  |--|--|--|
  | `founder-os-auto` | auto | Routing Engine decides |
  | `founder-os-code` | turbo | Coding (GLM 5.2) |
  | `founder-os-reasoning` | architect | Reasoning (DeepSeek) |
  | `founder-os-fast` | turbo | Quick Q&A (low DDollar cost) |

- Status bar item shows connection state, the active model, the last route's tier/provider/model, and per-message DDollar cost (when the gateway emits the optional `founderOs` SSE metadata line).

## Auth / credentials

The node credential is read from `~/FounderVault/node-config.json`, written by
the secure X device-code flow. It is not copied into ordinary IDE settings.
Non-secret API and node identifiers can still be overridden for development.

If no vault session is present, the Founder Activity Bar opens on first run and
offers the account sign-in.

## Build

```powershell
cd packages\founder-ide-extension
npm install
npm run typecheck    # tsc --noEmit
npm run package      # @vscode/vsce package -> founder-ide-extension-0.0.1.vsix
```

The `npm run package` step produces `founder-ide-extension-0.0.1.vsix`.

## Install

```powershell
# Cursor
cursor --install-extension founder-ide-extension-0.0.1.vsix

# VS Code / VSCodium
code --install-extension founder-ide-extension-0.0.1.vsix
codium --install-extension founder-ide-extension-0.0.1.vsix
```

Reload the window after installing (`Developer: Reload Window`).

## Use

1. Open Founder from the Activity Bar and choose Local, Hybrid, or Founder Cloud.
2. Select **Sign in** to connect this computer through the X device-code flow.
3. Open Founder Chat and choose a Founder model.
4. Type a message. Tokens stream from the Founder gateway.

## Architecture

```
VS Code Chat view
   │  vscode.lm.registerLanguageModelChatProvider('founder-os', provider)
   ▼
founder-ide-extension (this package)
   - founder-hub.ts      -> Founder Activity Bar control center
   - founder-authentication.ts -> IDE Accounts integration
   - credentials.ts      -> reads ~/FounderVault/node-config.json
   - models.ts           -> 4 aliases map to X-Execution-Profile headers
   - gateway-client.ts   -> POST /api/v1/chat/completions, SSE parser
   - chat-provider.ts    -> LanguageModelChatProvider impl, progress.report()
   - extension.ts        -> activation, status bar, commands
   │  fetch (HTTPS, Authorization: Bearer fos_{nodeId}:{nodeToken})
   ▼
Founder OS API — apps/api/src/ai-proxy/ai-proxy.controller.ts
   AiProxyController -> AiProxyRuntimeService
     decideRoute (Routing Engine v2) -> invoke (GLM / DeepSeek, streaming)
     afterRequest (DDollar spend, AiTokenUsageLog, Flight Recorder)
```

The gateway owns routing, DDollar metering, and Flight Recorder logging. This extension is just the model layer — it does not re-implement any of that.

## Commands

| Command | Title |
|--|--|
| `founderOs.openHub` | Founder: Open control center |
| `founderOs.signIn` | Founder: Sign in with X |
| `founderOs.signOut` | Founder: Sign out |
| `founderOs.manage` | Founder OS: Manage connection |
| `founderOs.pair` | Founder OS: Pair with Founder Node |
| `founderOs.selectModel` | Founder OS: Select model alias |
| `founderOs.openVaultConfig` | Founder OS: Open node-config.json |

## Test plan

1. **Install** the `.vsix` in Cursor: `cursor --install-extension founder-ide-extension-0.0.1.vsix`. Reload the window.
2. **Sign in** from the Founder Activity Bar and confirm `~/FounderVault/node-config.json` exists.
3. **Status bar**: should read `Founder OS: Connected`. If not, click it → "Pair with Founder Node".
4. **Open Chat** view. The model dropdown should list `Founder OS Auto / Code / Reasoning / Fast`.
5. **Send a message** with `Founder OS Auto` selected.
   - Expect: tokens stream in real time; the response is plain text from our gateway.
   - Verify server-side: a row appears in the Flight Recorder for the request; `/api/v1/usage` reflects the spend.
6. **Try each alias**: switch to `Founder OS Code` and send a coding question; switch to `Founder OS Reasoning` and ask for a deep analysis. Confirm the gateway's `decideRoute` logs the expected tier for each (turbo / architect).
7. **Cancellation**: while streaming, hit the cancel button on the chat message. The fetch should abort (status bar returns to `Connected`).
8. **Unpaired state**: rename `~/FounderVault/node-config.json` temporarily and reload. Status bar should show `Not Paired` and the model picker should not list Founder OS models (or list them but error gracefully on use). Restore the file and reload to recover.
9. **Credential override**: set `founderOs.apiBaseUrl` in settings to a staging URL; reload; confirm requests go to staging (check Flight Recorder / network).
10. **Expired session**: use an expired test vault; expect a safe sign-in message with no raw response body or credential fragment.

## Notes / limitations

- The optional `founderOs` SSE metadata pre-line (tier / provider / DDollar cost) requires a small server-side addition to `ai-proxy.controller.ts` — currently the status bar shows `Connected` after each request; the per-message cost line activates automatically once the gateway emits the metadata line. (Design report §5.3 / §8.2.)
- Token counting is approximate (`length / 4`). The gateway does real token accounting server-side.
- File-edit / terminal tool use from chat is Phase 3 — see `docs/FOUNDER-IDE-FORK-PLAN.md` §9.
