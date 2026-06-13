# x402 integration — what Gemini got right vs what we already have

## Two different problems

| Problem | Who | Solution |
|---------|-----|----------|
| **Your API charges callers** (server) | Doxxed platform | Already built: `@x402/express` on `GET .../signals/intent` |
| **Subscriber agents pay your API** (client) | External bots / Cursor agents | Optional: Coinbase Agentkit or `@x402` client SDK |

Gemini’s Agentkit / Kite advice is for the **client side** — an orchestrator that hits HTTP 402 endpoints and signs payments hands-free. It is **not** for listing agents on web directories.

## What we already ship (server — no Agentkit needed)

- `apps/api/src/payments/x402-signal.setup.ts` — x402 paywall on signal intent
- AgentCard / agent.json advertise `x402Support` when admin EVM treasury is set
- Free path: subscribers with `X-Signal-Api-Key` skip x402

Enable in Railway (or Admin → Platform EVM treasury in DB):

```
X402_EVM_PAY_TO=0xYourAdminBaseAddress
X402_SIGNAL_ENABLED=true
X402_SIGNAL_INTENT_PRICE=$0.10
X402_SIGNAL_NETWORK=eip155:8453
X402_FACILITATOR_URL=https://x402.org/facilitator
```

Then:

```powershell
npm run sync:all
npm run verify:x402   # expect 402 on GET .../signals/intent
```

## Optional client stacks (for subscriber bots)

### Coinbase Agentkit (recommended if building a subscriber agent in Cursor)

- Handles wallet + 402 handshake on Base
- Good for: “Cursor agent polls Conservative BTC intent and pays $0.10 per call hands-free”
- Docs: https://docs.cdp.coinbase.com/agentkit

### Kite / SPACE (enterprise)

- Spend caps, concurrency guards on x402
- Good for: “Max 2 USDC/hour on signal API calls”

### Raw `@x402` fetch (minimal)

Same pattern as Gemini’s axios example — catch 402, sign, retry with `X-PAYMENT` header. Our intent endpoint follows the standard.

## What we do NOT need

- Agentkit for **directory registration** (SAID, Spawn, OpenServ use their own CLIs/APIs)
- Manual web directory forms
- A separate x402 project unless you want a **reference subscriber script** (future: `scripts/x402-signal-subscriber.mjs`)

## Commands (registry + x402 server)

```powershell
npm run register:agents-automated    # SAID / Spawn / Fushu / OpenServ
npm run sync:all                       # deploy x402 env to Railway
```
