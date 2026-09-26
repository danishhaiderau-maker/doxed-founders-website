# Nucleus P0 — Founder Graph click → chat

Nucleus is the Founder Graph map. A click builds one context packet and starts a chat on the existing Founder OS gateway **Auto** tier (`founder-os-auto`). It does not add a provider registry and it does not rebuild the Void fork.

Related plan: [PR #119](https://github.com/danishhaiderau-maker/doxed-founders-website/pull/119) (`docs/FOUNDER-IDE-ASAP-PLAN.md` on that branch).

## Surfaces

Both surfaces share `NucleusContextPacket` (`version: 1`, `nodeId`, `label`, `type`, `detail`, `neighbors`, `excerpts`) from `packages/utils/src/nucleus-context.ts`. The extension keeps a byte-identical copy at `packages/founder-ide-extension/src/nucleus-context.ts` so the VSIX does not import `@dcf/utils`.

| Surface | Where | Graph read | Chat |
|---|---|---|---|
| Web | `/founder-ide/nucleus` | `GET /api/ide/nucleus` with the **session JWT** | `POST /api/v1/chat/phone-completions`, `model: founder-os-auto`, `stream: false` |
| Extension | Founder OS activity bar → **Nucleus**, or **Founder OS: Open Nucleus** | `GET /api/ide/nucleus` with the **Founder Node** header | Existing `@FounderOS` participant → `POST /api/v1/chat/completions` (Auto unless the active alias says otherwise) |

`GET /api/copilot/founder-graph` is unchanged and still accepts only a session JWT.

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

## Click behavior

1. The map lays nodes out left to right from the focus initiative.
2. Click highlights that node and opens the chat column (web) or `@FounderOS` chat (extension).
3. The visible text is the node type, label, id, detail, and 1-hop neighbors.
4. The system prompt gets a `<nucleus-context>` block. Labels are stripped of that closing tag, chat role headers, and markdown fences before insertion. HTML surfaces use React text nodes or `textContent`, plus `escapeNucleusHtml` for the IDE markdown fence.

`chainExcerpt` on the packet is the existing `formatFounderGraphForPrompt` string returned as `excerpt` by the graph API, capped and sanitized.

## Smoke

Automated:

```bash
npm test --workspace=@dcf/utils
npm test --workspace=@dcf/api -- src/ide-nucleus/ide-nucleus-auth.spec.ts
npm test --workspace=@dcf/web -- src/components/nucleus/nucleus-map.spec.tsx
npm run typecheck --workspace=founder-ide-extension
```

The utils test checks layout, click → `founder-os-auto` messages, prompt/HTML breakout, and that the extension copy matches. The web test renders the SVG and asserts a `<script>` label is escaped. The API test classifies the three Authorization forms above.

Manual, web:

1. Sign in and open `/founder-ide/nucleus`.
2. The map shows your Founder Graph (or an empty state). The header line includes `auth jwt`.
3. Click a node. It gains a white ring. The chat column shows **Node context** with that label.
4. Ask. The request model is `founder-os-auto`. The prompt block in the disclosure contains the node id inside a single `<nucleus-context>` wrapper.

Manual, extension (Founder IDE 0.9.1 or Cursor, no Void rebuild):

1. Pair Founder Node so `~/FounderVault/node-config.json` exists.
2. Run **Founder OS: Open Nucleus**.
3. Click a node. Chat opens on `@FounderOS` with the node label visible, and the next gateway call includes the packet in the system prompt.
