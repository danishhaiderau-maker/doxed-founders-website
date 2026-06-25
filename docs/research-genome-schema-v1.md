# Trading Genome Schema v1

Architecture freeze — schema version **1.0.0**. No field changes without incrementing `schema_version`.

## Principles

1. **Two systems** — bot records immutable facts; analyzer derives intelligence.
2. **Immutable IDs** — never rely on timestamps alone for joins.
3. **Event versioning** — every bus event carries `event_version` independent of schema version.
4. **Raw floats in bot** — bucketing belongs in the analyzer only.
5. **JSONL mirrors** — append-only audit trail alongside `research.db`.

## ID chain

```
environment_id → market_genome_id → decision_id → execution_id → trade_id → outcome_id
```

All IDs are UUID v4 strings, assigned once at creation, never mutated.

## Layers

| Layer | Recorder | Bot / Analyzer |
|-------|----------|----------------|
| 1 Environment | Environment Genome Recorder | Bot |
| 2 Market | Market Genome Recorder | Bot |
| 3 Decision | Decision Genome Recorder | Bot (APPROVE + REJECT) |
| 4 Execution | Execution Genome Recorder | Bot |
| 5 Lifecycle | Lifecycle Genome Recorder | Bot (event-driven) |
| 6 Trade | Trade Genome Recorder | Bot (on close) |
| 7 Outcome | Outcome Genome | Analyzer only |

Satellite genomes (Missed Opportunity, No Fill, Replay) are analyzer-derived.

## Event bus

Minimum events (each with `event_version`):

- `AI_SCAN_COMPLETE` v1
- `AI_APPROVED` v1
- `AI_REJECTED` v1
- `LIMIT_CREATED` v1
- `LIMIT_CHASED` v1
- `ORDER_FILLED` v1
- `ORDER_CANCELLED` v1
- `ORDER_EXPIRED` v1
- `POSITION_OPENED` v1
- `POSITION_UPDATED` v1
- `POSITION_CLOSED` v1
- `MFE_UPDATED` v1
- `MAE_UPDATED` v1
- `LADDER_STEP_ARMED` v1
- `LADDER_STEP_HIT` v1
- `STOP_UPDATED` v1
- `THESIS_CHANGED` v1
- `EXIT_TRIGGERED` v1

## Storage

- **Primary:** `services/btc-conservative-agent/research.db` (SQLite)
- **Mirrors:** `research/genome/*.jsonl` (append-only)
- **Legacy CSVs:** read-only during migration; analyzer regenerates reports from DB

## DNA Quality & confidence

Analyzer computes:

- `dna_quality` — 0–100 composite score
- `confidence_interval_95` — `{ low, high }` on EV estimates
- `research_confidence` — LOW | MODERATE | HIGH (sample-size gated)
- `cluster_assignment` — cluster label or `UNKNOWN` when similarity &lt; threshold

## Live execution tiles (frozen)

| Tile | Role |
|------|------|
| CONTINUOUS | Permanent benchmark |
| COMBO_604_SP4_CHASE_3PLUS | Research candidate |

All other lanes → legacy metadata only.

## Machine-readable schema

See `services/btc-conservative-agent/research/schema/genome_v1.json`.
