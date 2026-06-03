# Hybrid control plane

Founder OS is the **control plane**. Cursor, DeepSeek, and other tools are **workers**.

## Three legs

| Leg | Role | Typical worker |
|-----|------|----------------|
| **Ask** | Answers in Mission Control | DeepSeek, OpenAI, Ollama |
| **Code in repo** | Commits & PRs | Cursor Cloud, OpenHands |
| **Ship story** | Feed, X, community | Founder OS Autopilot |

## Entry modes

- **Cursor for code** — GitHub required; optional Cursor API. Lighter connect path.
- **Full Founder OS stack** — GitHub, Neon, Vercel, Railway, chat AI, builder, Autopilot.

## Autopilot (“Take full control”)

Runs in order:

1. GitHub commit sync  
2. Project memory → platform Postgres (Neon via Railway `DATABASE_URL`)  
3. Neon API verification (Stack token)  
4. Publish pending build updates (when Autopilot or full-control prompt)  
5. Vercel + Railway redeploy (when tokens + redeploy enabled)  
6. Builder agent dispatch (Cursor/OpenHands) for next task  

## API

- `GET /copilot/platform-sync` — status + control plane readiness  
- `POST /copilot/autopilot` — run full sync  

## Database

After pulling, apply schema:

```bash
npx prisma db push --schema=prisma/schema.prisma
```

New fields: `controlPlaneMode`, `autopilotEnabled`, `autopilotRedeployHosts`.
