# Conservative BTC Agent — Canonical Production Source

This directory is the only editable Conservative BTC bot source.

- `bot.py` owns the AI direction call, strategy decisions, structural limit,
  virtual chase, paper execution, signed relay events, exits, and bot dashboard.
- `btc_conservative_agent.py` is the Fly.io process entry point.
- `showcase_ui.py` adds the compatibility dashboard and data-sync endpoints.
- `research/` contains the out-of-process analyzer code and does not own trades.

## Runtime ownership

```text
Fly.io doxed-btc-bot
  └─ btc_conservative_agent.py
       └─ imports canonical bot.py exactly once
            ├─ public Fly dashboard/API
            ├─ signed exact-limit relay events
            └─ bounded data volume

Desktop
  ├─ :7002 read-only proxy to Fly
  ├─ canonical-research-data/ (verified Fly derivative)
  └─ :9001 external analyzer over that mirror
```

The desktop must not run `bot.py` or make a second AI call. Railway hosts the
platform API and isolated subscriber executor; it does not run another copy of
the strategy bot.

## Canonical research data store

Fly's persistent `bot_data:/app/data` volume is the durable authority. The only
desktop raw-evidence location is the ignored, repo-contained
`services/btc-conservative-agent/canonical-research-data/` folder. The Fly sync
worker writes that folder atomically and never uploads raw evidence from it;
the analyzer reads that same folder through `BTC_AGENT_DATA_DIR`.

Every completed sync appends a hash-chained
`canonical_dataset_manifest.jsonl`, atomically advances
`canonical_dataset_current.json`, and publishes
`canonical_dataset_parity.json`. Analyzer admission requires the current epoch,
source revision, and tile-registry signature to match the completed Fly sync
receipt. A mismatch fails closed before report generation.

Use `scripts/migrate_canonical_research_store.py` to copy a previously verified
mirror into the store. Migration verifies sync state, sizes, optional hashes,
revision parity, and epoch identity. It does not delete or modify the source.
The Windows wrapper uses the completed sync heartbeat:

```powershell
& .\scripts\migrate-fly-mirror-to-local.ps1 `
  -SourceDir "$env:LOCALAPPDATA\DoxxedCrypto\fly-data-mirror"
```

After its receipt and a local analyzer pass are verified, remove the obsolete
`DOXXED_FLY_MIRROR_DIR` override; future launchers reject any location other
than the repository's canonical store. Retain the old mirror until that
verification is complete.
Stale local rotations are moved under `archive/sync-retired/` with recoverable
receipts before their active paths are removed; cleanup paths cannot escape the
canonical store.

## Collector maturation scheduling

The provisional V2.2 journal is restored once at startup. Runtime maturation
uses the in-memory map. A cooldown-limited background reconciliation still
recovers any individual durable row missing from that map, but journal loading
and validation no longer run in the price/order worker or hold the collector
epoch lock for an entire scan. This prevents a full journal scan from blocking
the shared three-minute AI coordinator.

Mature/closed paths are processed before still-waiting paths. The worker uses a
small base batch and adapts toward a bounded terminal-backlog sweep, releasing
the collector lock between every record. Current controls are:

- `COLLECTOR_PROVISIONAL_REMERGE_INTERVAL_SEC`: background recovery cooldown;
  default 900 seconds, minimum 60 seconds.
- `COLLECTOR_MATURATION_BATCH_SIZE`: minimum records per one-minute poll;
  default 2, minimum 1.
- `COLLECTOR_MATURATION_MAX_BATCH_SIZE`: hard per-poll ceiling; default 25 and
  never below the configured base batch.
- `COLLECTOR_MATURATION_TARGET_SWEEP_MIN`: target minutes for one pass over the
  currently terminal-ready backlog; default 60, minimum 15.

`state.collector_maturation` publishes pending, terminal-ready, selected and
effective batch counts, oldest pending age, estimated ready-sweep minutes, and
last poll/journal-merge timestamps. Rollback is configuration-only: lower the
maximum/base batch while retaining oldest-first ordering and the dedicated
compressed-shadow lock. Do not restore per-minute full-journal rescans or share
compressed-shadow ownership with the collector epoch lock.

## Mature-ledger startup

Paper lifecycle restore reads the large V3 order-intent identity ledger only
when the atomic lifecycle snapshot actually contains pre-order awaiting
signals. Pending orders and open positions already contain their durable
identity and do not require that index. The overdue expected-order scan remains
mandatory and fail-closed, but its throttle timestamp is recorded after the
scan finishes so startup reconciliation cannot immediately trigger the same
full-ledger pass again.

## Safe deployment

Changes to this directory trigger `.github/workflows/fly-bot-deploy.yml`.
That workflow compiles the bot, runs the safety contracts, deploys the exact
Git revision to Fly.io, and proves the deployed revision is disarmed.

## Generated parity mirror

`../btc-signal-engine/engine.py` is a generated byte-for-byte mirror used for
parity checks and compatibility consumers. Never edit it independently and
never copy it back into this directory.

```bash
npm run mirror:signal-engine
npm run verify:signal-parity
```

External/local blunt replacement is locked by
`config/bot-architecture.lock.json`. Normal development edits this directory
directly.
