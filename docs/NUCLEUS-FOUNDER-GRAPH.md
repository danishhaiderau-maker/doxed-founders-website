# Nucleus P0 — Founder Graph click → chat

Nucleus is a live map of live work. Closed pull requests, finished agent runs, deploys, updates, decisions, vault docs, and commits that do not touch an open pull request drop off the map, and edges that touched them die. A stored graph older than 60 seconds is rebuilt on `GET /api/ide/nucleus`, so a newly opened pull request can appear and a merged one disappears.

A click builds one context packet (`version: 2`) with a **delivery address** — workspace path, symbol, line range, and intent — and starts a chat on the existing Founder OS gateway **Auto** tier (`founder-os-auto`). The address is in the system prompt (`<nucleus-delivery>`) and is enforced by the extension read/edit/run tools. It is not a free-text pin. It does not add a provider registry and it does not rebuild the Void fork.

Related plan: [PR #119](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/119) (`docs/FOUNDER-IDE-ASAP-PLAN.md` on that branch).

## Surfaces

Both surfaces share `NucleusContextPacket` (`version: 2`, `nodeId`, `label`, `type`, `detail`, `neighbors`, `excerpts`, `delivery`) from `packages/utils/src/nucleus-context.ts`. The extension keeps a byte-identical copy at `packages/founder-ide-extension/src/nucleus-context.ts` so the VSIX does not import `@dcf/utils`.

| Surface | Where | Graph read | Chat |
|---|---|---|---|
| Web | `/founder-ide/nucleus` | `GET /api/ide/nucleus` with the **session JWT** | `POST /api/v1/chat/phone-completions`, `model: founder-os-auto`, `stream: false` |
| Extension | Founder OS activity bar → **Nucleus**, or **Founder OS: Open Nucleus** | `GET /api/ide/nucleus` with the **Founder Node** header | Existing `@FounderOS` participant → `POST /api/v1/chat/completions` (Auto unless the active alias says otherwise) |

`GET /api/copilot/founder-graph` is unchanged. It still accepts only a session JWT and still returns the historical chain. Nucleus reads `GET /api/ide/nucleus`, which returns the live projection as both `graph` and `liveGraph`.

## Which token works

`GET /api/ide/nucleus` is marked so the global JWT guard does not reject a node header, then `IdeNucleusAuthGuard` requires exactly one of:

| Authorization header | Who | Result |
|---|---|---|
| `Bearer <session JWT>` | Browser on `/founder-ide/nucleus` (NextAuth `accessToken`) | `auth: "jwt"` |
| `FounderNode {nodeId}:{nodeToken}` | Extension and Founder Node. This is what the extension sends. | `auth: "founder-node"` |
| `Bearer fos_{nodeId}:{nodeToken}` | OpenAI-compat node bearer | `auth: "founder-node"` |

Anything else is 401. The handler returns graph labels and edges for that user. It does not return the node token, API keys, or vault file bodies.

Chat tokens are separate:

- Browser chat uses the **same session JWT** on `/api/v1/chat/phone-completions` (already the JWT entry to the gateway).
- Extension chat uses the **Founder Node** credential on `/api/v1/chat/completions`, same as the rest of Founder OS Chat.

The selected packet lives in React state (web) or a module variable (extension). Neither path writes it, or any secret, to `localStorage`.

## Live map

`projectLiveNucleusGraph` keeps:

| Node | Stays when |
|---|---|
| initiative | It is the focus initiative |
| task | Always (a task is current work) |
| pr | `detail` is exactly `open` |
| commit | It shares an edge with a live open pull request |
| agent_run | `detail` is empty or not a terminal status (`completed`, `failed`, `cancelled`, `success`, …) |

Everything else is dropped. Edges survive only when both ends are still live. Running the projection again is a no-op.

On each Nucleus read, if `updatedAt` is missing or older than 60 seconds, the API calls `FounderGraphService.rebuildForUser` (GitHub commits, pull requests, active agent run) and falls back to the stored chain if that rebuild throws. The response is still the live projection of whichever graph it used.

## Delivery address

`delivery` is `{ path, symbol, range, intent }`.

Resolution order, with no repository walk:

1. Explicit `path`, `symbol`, `range`, and `intent` on the graph node.
2. A GitHub blob URL in `href` (`/blob/<ref>/<path>#Lstart-Lend`).
3. A workspace-relative path, `symbol:Name` or `#Name`, and `L79-L120` written in the label, detail, or href.
4. For an open pull request, the rebuild asks GitHub for that PR's changed files only (`GET /repos/{owner}/{repo}/pulls/{n}/files`, at most five open PRs). The file with the largest diff becomes `path`. The first hunk supplies `symbol` and `range` when the patch has them. A commit whose message names that PR number inherits the same address.

`path` is workspace-relative. Absolute paths and `..` are rejected. When no file anchor exists, `path` is null and `intent` says to edit nothing and not search the repository.

End to end:

1. Webview or `/founder-ide/nucleus` builds the packet from the live graph.
2. Web chat posts it as the system message to `POST /api/v1/chat/phone-completions` (`model: founder-os-auto`). The extension prepends the same `<nucleus-context>` and `<nucleus-delivery>` blocks in `@FounderOS` and the language-model provider before `POST /api/v1/chat/completions`.
3. While that packet is selected, `founder.editFile` accepts only the delivery path and refuses an `oldText` outside the line range. `founder.readWorkspace` returns only that file, sliced to the range, and does not walk the tree. `founder.runCommand` refuses the command so a shell search cannot bypass the pin.

## Click behavior

1. The map lays live nodes out left to right from the focus initiative.
2. Click highlights that node and opens the chat column (web) or `@FounderOS` chat (extension).
3. The visible text includes the node identity and the delivery path, symbol, range, and intent.
4. The system prompt gets `<nucleus-context>` and `<nucleus-delivery>`. Field values are stripped of those closing tags, chat role headers, and markdown fences before insertion. HTML surfaces use React text nodes or `textContent`, plus `escapeNucleusHtml` for the IDE markdown fence.

## Still not a full pinpoint edit

- The Founder OS language-model provider still reports `toolCalling: false`. The packet reaches the gateway prompt, and the three tools enforce the address when they are invoked. There is no agent loop that calls them on its own.
- Initiative nodes, tasks whose text names no file, and commits that are not tied to an open pull request resolve to `path: null`. The intent then forbids a search. The click does not guess an open editor file.
- An open pull request contributes one primary file, not every file in the diff.
- The web chat can send the address to the gateway. It cannot edit a local file. That write path is the extension tools.
- Day Desk OAuth is unchanged.

## Smoke

Automated:

```bash
npm test --workspace=@dcf/utils
npm test --workspace=@dcf/api -- src/ide-nucleus/ide-nucleus-auth.spec.ts
npm test --workspace=@dcf/web -- src/components/nucleus/nucleus-map.spec.tsx
npm run typecheck --workspace=founder-ide-extension
```

The utils test checks layout, the live projection, click → `founder-os-auto` messages, the delivery address and tool gate, a GitHub pull-request patch anchor, prompt/HTML breakout, and that the extension copy matches. The web test renders the SVG, asserts a `<script>` label is escaped, and asserts a single `<nucleus-delivery>` block. The API test classifies the three Authorization forms above.

Manual, web:

1. Sign in and open `/founder-ide/nucleus`.
2. The map shows your Founder Graph (or an empty state). The header line includes `auth jwt`.
3. Click a node. It gains a white ring. The chat column shows the delivery path, symbol, range, and intent.
4. Ask. The request model is `founder-os-auto`. The prompt contains the node id inside one `<nucleus-context>` block and the address inside one `<nucleus-delivery>` block.

Manual, extension (Founder IDE 0.9.1 or Cursor, no Void rebuild):

1. Pair Founder Node so `~/FounderVault/node-config.json` exists.
2. Run **Founder OS: Open Nucleus**.
3. Click a node. Chat opens on `@FounderOS` with the delivery address visible. The next gateway call includes `<nucleus-delivery>` in the system prompt. Edit and read stay on that path.
