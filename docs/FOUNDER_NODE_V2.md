# Founder Node v2 — Step 4 of the Privacy Stack

> **Vector search on your machine. Cloud pushes goals. Agents run locally.**

After Steps 1–3 (Founder Vault, BYO AI, Phala TEE), Founder Node v2 adds **bidirectional sync**, a **local vector index**, and **on-device agents** — without uploading vault plaintext to Founder OS servers.

## What shipped

| Piece | Location |
|-------|----------|
| Local vector index | `~/FounderVault/vector-index.json` (TF-IDF chunks) |
| Bidirectional jobs | `FounderNodeSyncJob` — cloud → node pull queue |
| Node agents | `vault-index`, `goal-align`, `vault-summary` |
| API | `POST /founder-node/sync-jobs/*`, node poll `/sync-jobs/pending` |
| Auto goal push | Saving **Current goal focus** enqueues `PUSH_GOAL` when memory mode is `FOUNDER_NODE` |
| Builder UI | Settings → Builder → **Founder Node v2 (Step 4)** |

## Sync directions

```text
Founder OS (web)
       │  PUSH_GOAL / PUSH_TASK / VAULT_SEARCH / RUN_AGENT
       ▼
@dcf/api  ── FounderNodeSyncJob queue
       │
       ▼ (poll ~3s)
Founder Node tray app
       │
       ├── applyPushGoal → tasks.json + project-context.md
       ├── applyPushTask → tasks.json
       ├── searchVaultVectorIndex (local only)
       └── runLocalAgent (vault-index, goal-align, vault-summary)
       │
       └── POST /founder-node/sync (encrypted blob up) — unchanged from Step 1
```

## Setup

1. Complete Step 1 — pair Founder Node, memory mode **Founder Vault (Founder Node)**
2. Install Founder Node **v0.4.0+** from `/founder-node`
3. Settings → Builder → **Founder Node v2**
4. **Rebuild vector index** (or wait for hourly sync cycle)
5. Set **Current goal focus** — auto-pushes to vault when node is online
6. **Search vault** — semantic search runs on your desktop; only match snippets return to the browser

## Privacy

- **Vector index** never leaves your machine unless you run **Search vault** (top snippets only).
- **Private notes** are excluded from the index by default.
- **Bidirectional sync** writes goals/tasks to local files — not stored as plaintext on Neon.

## Roadmap (Step 5)

5. **Attestation dashboard** ✅ — see `docs/ATTESTATION_DASHBOARD.md`

## References

- Step 1: `docs/FOUNDER_VAULT.md`
- Step 2: `docs/BYO_AI.md`
- Step 3: `docs/PHALA_PRIVATE_AI.md`
