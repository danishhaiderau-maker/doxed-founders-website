# Public collector: explicit daily runtime (PR988 follow-up)

**Build/review only. No capture, OPS, scheduler installation, laptop or live
trading authority is conferred by this code. Live OFF.** No arguments still
refuses (78). Existing `capture --output ... --allow-root ...` is unchanged.

After the separate HM/RK/merge and OPS/OS launch gates, the explicit entrypoint is:

```
python -B -m public_market_collector daily --root DEDICATED_EXISTING_DIRECTORY
```

Run from `services/public-market-collector` (or place that directory on Python's
module path). On Windows only, `daily` without `--root` selects
`C:\DoxxedCrypto\data\collector`. It must already be a dedicated directory outside
the checkout/protected trees. Non-Windows requires `--root`. No environment
variables or credentials are read. No HTTP server or scheduler is installed.
The only network transport is the existing host-checked, public TLS WebSocket.
No REST backfill, execution, bot or AI path is linked.

## Files and lifecycle

- `YYYY-MM-DD/{raw,norm}/{book,trades}/*.gz` and the existing `index.journal` /
  `health.journal`: PR988 append-only segments, journal checksums and startup-tail
  inspection. UTC **receive** day, not exchange timestamp. Existing files are
  never truncated, overwritten, renamed, deleted or silently repaired.
- `runtime.journal`: append-only checksum-framed `pmc_runtime_v1` day-start,
  heartbeat, coverage and day-end records. Heartbeat target 30 s, including idle
  reads/backoff; normal read/connect timeout is 5 s. No hard OS/DNS latency bound
  is claimed. Status and coverage are per run; not a sealed-day result.
- `launch.journal`: checksum-framed starts/exits; `runtime.lock`: one active
  runtime per root using OS advisory locking. All invocations must use this
  launcher/root; a different root/process is not globally paced by this lock.
- UTC rollover closes/flushes the old session and reconnects with a fresh
  snapshot. ConnectPacer is preserved across rollover. A frame read across the
  boundary is omitted rather than assigned to the wrong day; the rollover
  receipt explicitly discloses this gap. Book gaps cannot be backfilled.
- SIGINT/SIGTERM request a stop, final raw commit and final coverage record.
  Bounded transport timeout/backoff checks permit graceful shutdown; power loss
  or SIGKILL cannot be graceful. Existing startup inspection records uncertainty.

## Coverage is not proof of completeness

Records give distinct UTC receive minutes observed for confirmed trades-feed
messages (including heartbeat), valid-book messages and actual data separately.
An invalid-book heartbeat cannot count as valid-book coverage, and a heartbeat
cannot count as data. They include full-day unobserved-minute counts (including
pre-start/future minutes), checksum-fault count, gap count and durable counts.
Day-start records disclose the observation gap since the previous run/rollover.
A minute with one message is **not** proof of continuous coverage for that minute.
No 99% claim, Stage-F VALID judgment, sealing, fill model or scored forward day
is produced. F2 must assess intervals/coverage from raw records separately.

The legacy public subscription remains **P0 / len 250**, not the Stage-F draft's
len 25. Its freeze/binding is an open review item, not silently changed here.
The inherited per-day/session cap is 8 GiB; raw volume is unmeasured. All writers
honour the 512 MiB free-space floor. The Python `run_daily` API can raise, never
lower, that floor. No retention deletion, quota alert or full F12 supervisor is
provided.

## C3 relaunch policy

The in-process launcher waits **at least 6 s after each failed run's exit** before
another run. The network loop retains its own 3 s / 10-per-60 s connect pacing.
Exit **75** (disk/write stop) and **78** (refusal) never auto-restart. Exit 0 is a
graceful stop and also never auto-restarts. A later explicit program invocation
waits a full 6 s after acquiring the lock, even if the wall clock jumped.
Persisted 75/78, a torn launch/runtime journal or an unpaired start refuses before
any socket is opened. No acknowledgment/clear-latch flag is present. Operator
review/remediation requires the later OPS gates; do not delete journals to bypass
it. If a disk stop cannot journal its exit, the unpaired start fails closed.
A same-program unexpected non-I/O exception is type-only reported, records exit 1
and may retry after the floor. Disk/OS errors are terminal 75, not reconnects.

## Record vocabulary contract

The PR988 body remains the contract. This follow-up uses the existing
`gap.cause="maintenance"` at **both 20060 and 20061**, and on an otherwise valid
book-channel unsubscribe. Additive `maintenance_event` detail distinguishes
`info_20060`, `info_20061`, `book_unsubscribed` (or
`book_unsubscribed_after_20061`). It closes the book validity interval at receive
and normalize time. After 20061, old book channel IDs are quarantined: only a new
subscription acknowledgment plus a fresh applied snapshot restores validity.
A snapshot deadline fires even if the shadow book erroneously looks valid;
`gap.cause="disconnect", reconnect_reason="resubscribe_timeout"` records recovery.
Subscribe errors remain verbatim info; they do not clear the deadline.

This **extends maintenance semantics**: disclose the version when comparing or
pooling captures, and obtain RK vocabulary review. No new raw gap cause is
invented. Real transport EOF/close/liveness still uses `disconnect` as documented
in the corrected PR988 body. `pmc_runtime_v1` and `pmc_launch_v1` are separate
additive runtime schemas, not replacements for `pmc_raw_v1` / `pmc_health_v1`.

## Checks (synthetic only)

```
python -B -m unittest discover -s services/public-market-collector/tests -p 'test_*.py'
```

Run from repository root for this command. No full repository suite is needed.
Tests cover stale book/no snapshot/error/lost reply, >30 s heartbeat-only
backoff, terminal restart latches, locking, disk floor, UTC rollover, append-only
resume, graceful stop, forced disconnect, coverage and safety-scan plants.
Static scans reject credential/environment access, execution-client imports and
order/AI calls; synthetic runtime tests deny real socket calls. These are scoped
checks, not a universal security proof.

**Not tested:** venue maintenance/subscription-error timing, real forced drops,
24 h reliability/volume or 99% coverage, real Windows/Task Scheduler, Windows lock
behavior, real full-disk/power-loss scenarios, external supervisors and consumers.
Nothing is launched by this PR.

## PR997 vocabulary boundary (RK condition 1)

**Every newly written top-level record now carries
`record_vocab_v: "pmc_vocab_v2"`.** The field is added before gzip serialization
and before journal checksum computation, including raw headers/messages/gaps,
normalized records, index commit/close/startup/stop records, health records,
launch records and runtime day/heartbeat/coverage records. The existing structural
`schema_v` / `norm_schema_v` values remain unchanged; the common field is the
explicit semantic boundary rather than separately renumbering every family.
Raw and norm strict key lists include the field. Journal byte budgets include
the actual stamped/checksummed size.

**PR988-vocabulary captures are separate, CLOSED series.** A missing stamp means
`pmc_vocab_v1`; an explicit v1 stamp has the same meaning. Read them separately;
never relabel or append v2 records to those capture directories. New writers
refuse legacy/mixed/unknown journals on resume, and refuse uncommitted segments
without journal vocabulary proof. Use a new, separately approved root/series.
The reviewed reader mapping below is not a writer-resume override. Do not alter,
delete or rewrite historical files to bypass the boundary.

Reader/analysis code must use `vocabulary.pool_records(records)` before combining
series. The normalizer (`normalize_raw_records` and `canonical_norms`) enforces
that guard too. Homogeneous v1 or v2 inputs can be read separately; mixed versions
raise `VocabularyError`. Unknown/null/invalid explicit versions always refuse.
`decode_prefix` / `read_journal` are forensic byte/checksum primitives, not pooling
APIs or a declaration that two series are semantically compatible.

The only cross-boundary path is an explicitly supplied `ReviewedVocabMapping`
with a pinned HM review receipt SHA256 and an actual v1-to-v2 converter. The
converter must return v2 records; there is no `allow_mixed=True` or silent
identity fallback. Source objects are not mutated. Converted records retain the
source vocab and HM review reference, and stale journal checksums are removed.
Keep that mapping/receipt with analysis provenance. The API validates the supplied
review reference's shape; it cannot authenticate HM approval. **No production
mapping is shipped or approved by this PR** (tests use synthetic references).
Existing v1 meanings are preserved, including maintenance = 20060; any mapping's
semantic treatment needs HM review, not just a changed label. HM R2 must review
the version change at the new exact head; any push voids RK's earlier-head PASS.
