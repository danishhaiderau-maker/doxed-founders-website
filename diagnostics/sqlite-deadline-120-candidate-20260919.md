# SQLite snapshot deadline candidate — 2026-09-19

## Scope

Isolated detached worktree `C:\DoxxedCrypto\btc-v31-sqlite120-candidate` at
Fly baseline `f84fff81e2905fcf38d5f02f348bd8d9ee655816`.

## Evidence

- Retained transfer logs show large SQLite leases succeeding at client elapsed
  times of roughly 63–72 seconds and failing near the old 60-second boundary.
- Affected file: `v3/qualification_horizon_index.sqlite3` (local mirror
  114,741,248 bytes; read-only `PRAGMA integrity_check` = `ok`).
- Existing implementation already clamps the configured deadline to 15–120
  seconds and bounds worker CPU, memory, output size, concurrency, and process
  lifetime.

## Candidate change

- Default `DATA_SYNC_SQLITE_SNAPSHOT_DEADLINE_SECONDS` from 60 to 120 seconds.
- Invalid-value fallback from 60 to 120 seconds.
- Preserve explicit values and the existing 15–120 clamp.

## Local verification

- Focused sync/deadline/ACK tests: `16 passed, 139 deselected`.
- Full `test_fly_data_sync_contract.py`: `155 passed` in 66.03s.
- `py_compile` passed for `bot.py` and `engine.py`.
- Candidate hashes after the generated parity mirror:
  - `bot.py` / `engine.py`: `2253717177c2d1b6ebd948b9e487ec80c1ce4024989eba4629ad9070369031a9`
  - `signal-engine/manifest.json`: `a6769a1bb829c00d08579504ac2ece2f0c3c2498c0a1aa473f92cefdbbae1c74`
- `git diff --check` passed (only normal LF/CRLF warnings).

## Gate

This is a candidate only. No commit, push, deploy, transfer, cleanup,
analyzer publication, paper resume, or Bitfinex action was performed. The
dirty integration tree remains untouched. Deployment requires a separately
reviewed exact candidate and the existing guarded paused/paper-only/flat
acceptance checks.
