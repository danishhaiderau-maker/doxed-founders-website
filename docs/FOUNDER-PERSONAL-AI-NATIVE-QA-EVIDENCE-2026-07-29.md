# Founder Personal AI Native QA Evidence

Date: 2026-07-29

## Scope

This checkpoint proves that the installed Founder IDE can preserve and use a
named OpenAI-compatible Personal AI profile without consuming Founder-managed
quota or exposing its credential.

## Installed-app results

- Founder Settings exposes Account, AI, Local & Cloud, Connections, and
  Advanced sections.
- The AI section shows Founder-managed routes and the saved `GLM` Personal AI
  profile.
- The native composer route picker contains:
  - `founder-os-auto`
  - `founder-os-fast`
  - `founder-os-reasoning`
  - `founder-os-code`
  - `GLM`
- A native round trip from `GLM` to `founder-os-auto` and back to `GLM`
  preserved the original selection.
- A real GLM completion returned the exact requested nonce through the saved
  `GLM-5.2` model in 3,285 ms.
- The visible receipt was:
  `Founder route | Personal AI | GLM | GLM-5.2 | direct | outside managed quota | 3,219 ms`.
- Console errors: 0.
- Page errors: 0.
- No API key or protected header appeared in the UI, receipt, or evidence.

## Evidence

- Settings and route-picker run:
  `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-settings-qa-20260729-route-picker\`
- Open route-picker screenshot:
  `C:\Users\user\Desktop\Final Bots\artifacts\installed-founder-settings-qa-20260729-route-picker\installed-founder-route-picker-final.png`
- Real Personal AI completion:
  `C:\Users\user\Desktop\Final Bots\artifacts\installed-personal-ai-qa-20260729-harness-fixed\`
- Completion screenshot:
  `C:\Users\user\Desktop\Final Bots\artifacts\installed-personal-ai-qa-20260729-harness-fixed\installed-founder-chat-native-V1-PERSONAL-GLM-FIXED-20260729151142.png`

## Remaining boundary

The local Ollama route is still not claimed as live-tested. No Ollama service
or model is listening on this machine at port `11434`. Source and unit
contracts remain in place, but native Ollama completion needs a deliberately
installed local model before it can pass the same evidence gate.
