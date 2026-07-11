# Founder IDE — releases

## 0.9.0 (Void fork) — Phase 5

**Foundation pivot: VSCodium -> Void.** This release replaces the VSCodium-based
build with a fork of [Void](https://github.com/voideditor/void), giving Founder
IDE Cursor-level AI UX on day one: inline edit (Ctrl+K), autocomplete (FIM),
diff review, and a chat sidebar — all inherited from Void and rewired to talk
to the Founder OS Gateway.

### What changed

- **Editor foundation:** `voideditor/void` + `void-builder` instead of VSCodium.
  Void is a VS Code fork with a full AI-native UX already built.
- **AI integration:** the central chokepoint — Void's `sendLLMMessage` dispatch
  in `electron-main/llmMessage/sendLLMMessage.ts` — is rewired to call our
  Gateway (`/api/v1/chat/completions`, SSE) before falling through to Void's
  per-provider dispatch. When a Founder Node is paired
  (`~/FounderVault/node-config.json`), every AI request (chat, Ctrl+K,
  autocomplete, agent) flows through the Gateway and its Routing Engine.
- **Branding:** `prepare-founder-ide-void.sh` layers "Founder IDE" on top of
  Void the same way Void layers on top of VS Code — fresh GUIDs, Open VSX,
  our URLs, our icon.
- **Installer:** `Founder-Stack-Setup-0.9.0.exe` bundles the Void-based
  Founder IDE + Founder Node.

### Feature -> Gateway alias mapping

| Void feature | Gateway alias | Routes to |
|--|--|--|
| Autocomplete (FIM) | `founder-os-fast` | cheapest/fastest tier |
| Edit / Ctrl+K | `founder-os-code` | coding tier (GLM) |
| Chat (normal) | `founder-os-auto` | Routing Engine decides |
| Chat (agent) | `founder-os-reasoning` | deep reasoning (DeepSeek) |

### Build artifacts

- `Founder-IDE-Setup-x64.exe` — the Void-based Founder IDE installer
- `Founder-Stack-Setup-0.9.0.exe` — IDE + Founder Node bundle

### Build commands

```powershell
# Prep + compile (60-90 min clean):
bash packages/founder-ide/build/build-founder-ide-void.sh

# Compose the Founder Stack bundle:
$ISCC = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
& $ISCC /DFOUNDER_STACK_VERSION="0.9.0" `
  /DFOUNDER_IDE_SETUP="<path-to-Founder-IDE-Setup-x64.exe>" `
  /DFOUNDER_NODE_SETUP="apps\founder-node\release\Founder-Node-0.8.0-win-x64.exe" `
  "packages\founder-ide\installer\founder-stack.iss"
```

### Side-by-side install

Fresh GUIDs (`win32AppId` `{39F6C958-...}`, `win32x64UserAppId`
`{436F5001-...}`) mean Founder IDE 0.9.0 installs alongside VS Code, VSCodium,
Void, and the old VSCodium-based Founder IDE 0.8.0 without conflict.

## 0.8.0 (VSCodium fork)

Previous release — VSCodium-based. See git history.