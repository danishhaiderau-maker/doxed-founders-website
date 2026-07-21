# Safe Trading Genome derived-data rebuild

This procedure repairs contaminated Genome summaries without deleting or rewriting raw research evidence.
It must be run only during a short maintenance window with the relay disabled and both the bot and analyzer
writers stopped. Do not run it merely because a report looks stale.

## Data boundary

Raw, immutable evidence tables (never delete or rewrite):

- `research_events`
- `environment_genome`
- `market_genome`
- `decision_genome`
- `execution_genome`
- `lifecycle_genome`
- `trade_genome`

Rebuildable derived tables only:

- `genome_library`
- `genome_discovery_memory`
- `genome_evidence_ledger`

## Required sequence

1. Confirm the Bitfinex relay is OFF and no exchange order or position exists. Stop the analyzer and bot DB
   writers for the maintenance window. Record the source revision and UTC time.
2. Use SQLite's online backup API to create a timestamped, integrity-checked backup of `research.db`; do not
   use a filesystem copy of a live WAL database. Run `PRAGMA integrity_check` on the backup and require `ok`.
3. Record row counts for every raw table above. Also record the backup path and SHA-256 digest.
4. Create a separate staging database from that verified backup. In the staging database only, delete rows
   from the three rebuildable derived tables. Never drop or clear a raw table.
5. Run `run_genome_analyzer(db_path=<staging>, out_dir=<staging-artifacts>)` once as a warm-up so intentional
   run-delta fields converge. Then run it two more times against the unchanged staging database.
6. Require the last two passes to have identical row counts, observation totals, and normalized SHA-256 hashes
   of every derived payload (excluding only timestamps). Also require every raw-table count to equal step 3.
   Any difference is a failed rebuild: discard staging and leave production untouched.
7. Require `validate_genome_integrity(staging)` to finish successfully. Existing historical feature/linkage
   warnings are allowed and must remain visible; orphan, duplicate, or raw-count changes are not allowed.
8. With writers still stopped, open production using `BEGIN IMMEDIATE`, attach the verified staging database,
   replace rows in only the three derived tables from staging, and commit atomically. If any statement fails,
   roll back; do not partially publish.
9. Re-read raw counts and semantic derived hashes from production. Raw counts must still equal step 3 and the
   derived signature must equal verified staging. Create a fresh online backup of the published database and
   repeat the warm-up plus two identical analyzer passes there; do not mutate production for verification.
10. Restart the bot and analyzer, confirm dashboard/analyzer source revision parity, then watch the first new
    Genome scan for populated source fields and linked decision/position/close IDs. Keep the relay OFF until
    that verification passes.

## Rollback

If post-publish verification fails, stop writers, restore the timestamped SQLite backup with SQLite's backup
API, run `PRAGMA integrity_check`, verify all raw counts, and restart in research-only mode. Preserve the failed
staging database and artifacts for diagnosis; never overwrite the backup.

The publisher prints the backup path, SHA-256 digest, raw counts, semantic hashes, and post-publish verification
result. Preserve that receipt with the release evidence.
