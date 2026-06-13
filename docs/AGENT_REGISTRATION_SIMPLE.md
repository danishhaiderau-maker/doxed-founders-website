# Agent registration — simple path (no Phantom popups)

SAID’s website + Phantom in-browser is flaky. Use this instead.

## What is already done (no wallet needed)

| Item | Status |
|------|--------|
| Agent live on doxxedcrypto.digital | ✅ |
| AgentCard JSON (directories) | ✅ `/.well-known/agent-card.json` |
| ERC-8004 JSON (The Spawn) | ✅ `/.well-known/agent.json` |
| Signal API + fee treasury | ✅ Admin configured |
| GitHub / Neon / Railway / Vercel | ✅ Synced |

**You do not need SAID to operate the agent.** SAID is optional discovery.

---

## What only YOU can do (2 minutes — never give anyone your seed phrase)

**Do not send private keys, seed phrases, or `agent-wallet.json` to ChatGPT, Cursor, or any person.**

### Option A — SAID without Phantom (recommended)

On your PC, in the project folder:

```powershell
cd "C:\Users\user\Desktop\Final Bots\doxedcryptofounder"
npm install said-sdk @solana/web3.js
node scripts/said-register-simple.mjs
```

1. Script prints a **new Solana address**.
2. Send **~0.02 SOL** to that address from an exchange or another wallet.
3. Run the same command again → SAID registers automatically.
4. Optional badge: `node scripts/said-register-simple.mjs --verify`
5. In **Admin → Agent registrations**, paste that address as Solana treasury → Save.

The wallet file stays on your PC: `agent-wallet.json` (gitignored).

### Option B — Skip SAID entirely

1. Submit manually (no crypto):
   - [aiagentsdirectory.com](https://aiagentsdirectory.com/) — paste AgentCard URL + hub link
2. Later: The Spawn on **Base + MetaMask** (often easier than Solana SAID)

### Option C — Keep using Phantom (if it works)

Account → Security → Connect Solana → use treasury address in Admin.

---

## What the AI / platform controls (full tech ownership)

- Showcase bot, keys, pause/start (Admin Control)
- Signal API, billing, legal disclaimers
- Metadata hosting for all directories
- Hire fee 2,000 DDollar / 7 days for users
- Production deploys

---

## Disposable wallet FAQ

**“Can I give Cursor full control with a disposable wallet?”**

- ✅ Give the **public address** only (for treasury + SAID identity).
- ✅ Run **one script locally** on your machine.
- ❌ Never paste **private key / seed / agent-wallet.json** into chat.
- ❌ No one else can sign Solana transactions for you without that secret.

That is by design — it protects your funds and the agent.
