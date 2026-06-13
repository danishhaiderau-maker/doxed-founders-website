# All agent directory listings — Conservative BTC Agent

**One command opens all free submit forms:**
```powershell
npm run submit:agent-directories -- --open
```

**Automated where possible:** Fushu API, SAID (`--said`), Spawn (if `THESPAWN_API_KEY` in vault).

---

## Universal copy-paste (every form)

| Field | Value |
|-------|--------|
| **Name** | Conservative BTC Agent |
| **Tagline** | Exchange-neutral BTC perp signal API — pay success fee on profit only, not on losses. |
| **Website / Product URL** | https://doxxedcrypto.digital/agent-hub/conservative-btc |
| **Demo URL** | https://doxxedcrypto.digital/agent-hub/conservative-btc |
| **Documentation** | https://doxxedcrypto.digital/docs/signal-api |
| **Logo / Icon** | https://doxxedcrypto.digital/icons/conservative-btc-agent.png |
| **Thumbnail / Cover** | https://doxxedcrypto.digital/icons/conservative-btc-agent-thumbnail.png |
| **AgentCard metadata** | https://doxxedcrypto.digital/.well-known/agent-card.json |
| **ERC-8004 JSON** | https://doxxedcrypto.digital/.well-known/agent.json |
| **API mandate** | https://doxed-founders-website-production.up.railway.app/api/trading-agents/conservative-btc/signals/mandate |
| **Company** | Doxxed Crypto Founder |
| **Email** | danish.haider.au@gmail.com |
| **Pricing** | Freemium — free API key; 10% success fee on profit only |
| **Categories** | Research, Data Analysis, Workflow, Finance |
| **Tags** | BTC, trading, signals, API, exchange-agnostic, AI agent |

### Description
```
Conservative BTC Agent publishes live BTC perpetual signal cycles from a transparent research pipeline. Subscribers execute on their own exchange using exchange-neutral percentage offsets. REST Signal Cycle API with mandatory stop-loss-at-fill. Success fee: 10% of profit on close only; $0 on loss. Not financial advice.
```

---

## Free directories (submit order)

| # | Platform | URL | Registry ID |
|---|----------|-----|-------------|
| 1 | **AI Agents Directory** | https://aiagentsdirectory.com/submit-agent | AGENTSCAN |
| 2 | **Pikagent** | https://www.pikagent.com/submit | PIKAGENT |
| 3 | **Fushu** | https://fushu.dev/register | FUSHU |
| 4 | **Agents.one** | https://agents.one/submit-agent/ | AGENTS_ONE |
| 5 | **ListMyAgent** | https://listmyagent.com/add-ai-agent-listing/ | LISTMYAGENT |
| 6 | **AI Agents Buzz** | https://aiagentsbuzz.com/submit/ | AIAGENTS_BUZZ |
| 7 | **OpenServ** | https://www.openserv.ai/ | SKILLS_SH |

All **$0** basic listings. Paid boosts optional only.

---

## On-chain / protocol (auto or low cost)

| Platform | Cost | How |
|----------|------|-----|
| **AgentCard** | Free | Already live |
| **SAID** | ~0.02 SOL | `npm run register:said-simple` |
| **The Spawn / ERC-8004** | Base gas | `THESPAWN_API_KEY` + sign tx |
| **8004scan / agentscan** | Free | Auto after Spawn mint |

---

## After each listing

1. **Admin → Agent registrations** → click **Mark {REGISTRY}**
2. AAD free tier: badge already on Agent Hub footer (`/agent-hub`)

---

## Files in repo

- `fushu.json` — Fushu manifest (repo root)
- `docs/AIAGENTSDIRECTORY_SUBMISSION.md` — AAD-specific detail
- `scripts/submit-agent-directories.mjs` — automation script
