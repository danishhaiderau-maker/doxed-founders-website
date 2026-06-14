# Agent registry — automated only (CLI / API)

Manual web directories (AI Agents Directory, Pikagent, Agents.one, ListMyAgent, AI Agents Buzz) are **deprecated** — no CLI or API, not worth ops time.

## One command

```powershell
npm run register:agents-automated
```

Optional flags:
```powershell
npm run register:agents-automated -- --said
npm run register:agents-automated -- --openserv
```

## Active registries

| Registry | Type | Command |
|----------|------|---------|
| **AgentCard** | Live | Already at `/.well-known/agent-card.json` |
| **SAID** | CLI | `npm run register:said-simple` (~0.02 SOL) |
| **The Spawn / ERC-8004** | API + tx | `THESPAWN_API_KEY` → `npm run prepare:agent-registrations` |
| **8004scan** | Auto | After Spawn mint |
| **Fushu** | Manifest + API | `fushu.json` + register script |
| **OpenServ** | API | `OPENSERV_USER_API_KEY` → `npm run provision:openserv` |

## Vault keys (`../doxedcryptofounder-secrets/vault/.env`)

```
THESPAWN_API_KEY=
OPENSERV_USER_API_KEY=
X402_EVM_PAY_TO=          # admin Base treasury for x402 intent payments
```

## After each step

[Admin → Agent registrations](https://doxxedcrypto.digital/admin/agent-registrations) → **Mark registered**

## Deprecated manual directories

Do not spend time on: aiagentsdirectory.com, pikagent.com, agents.one, listmyagent.com, aiagentsbuzz.com

Legacy copy-paste pack kept for reference only: `docs/AIAGENTSDIRECTORY_SUBMISSION.md`
