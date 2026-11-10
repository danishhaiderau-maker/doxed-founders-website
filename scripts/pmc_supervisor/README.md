# DoxxedLaptopSupervisor — draft integration, Live OFF

Spec read in full: `SPEC-PMC-ANALYZER-SUPERVISOR-20261110.md`, SHA256
`6fd72a67cff0894df3007ddeb7eb541124191d73093efc8e5a15e3386d4d6444`.
Required base: `08c2b3762cc166bbc9dd91f45a13edb3a0cfd876`.
RK design PASS `bb1b36ebe306` conditions are incorporated; this is not a
runtime/installation/merge PASS. Do not install or run on the laptop from this PR.

## One owner, two roles, no computation

The sole future Task is **DoxxedLaptopSupervisor**, targeting the explicit
`scripts/pmc_analyzer_supervisor.py --config <fixed approved-config.json>` entrypoint
with the pinned absolute Python and `-B`. The historical filename does not imply
another PMC-only Task. **Chained-backtest and forward-paper roles must be added
later to THIS supervisor by reviewed amendment, never a second Task entry.**
Only PMC `daily --root ...\data\collector-r2` and the existing
`research_dashboard.py --standalone` saved-report viewer are allowed now.
No arbitrary command/config hook, trading engine, main UI, heavy analyzer, browser,
full-stack launcher, cloud probe, installer or task-registration code is included.
Importing the package or invoking without arguments starts nothing.

The proposed single Task settings (separate installation card required): startup
trigger, approved least-privilege user, noninteractive-capable, IgnoreNew,
ExecutionTimeLimit=0, no restart-on-failure, periodic trigger or network restart.
No existing process/Task is disabled, adopted or killed by name/port.
Existing owners must be separately reconciled and their receipt pinned before use.

## Frozen paths and provenance

Production rejects non-Windows, another config location and unrecognized keys.
All paths/argv are constants, not generic config commands. Python311 binary pin:
`5f7b89a612c9b8af1d6456cdfcd1dbe5ca630849e79aebced9bee9a6694952ec`.
No PATH/py/interpreter fallback. Config must pin the implementation bundle and a
complete first-party `.py` manifest for both services and this supervisor; the
producer's canonical data tree is excluded from the source manifest. Holding
read/share-read Win32 handles prevents source/executable/config replacement.
Every ancestor is checked for junction/reparse/symlink aliases; source/log/state
roots are not redirected to data roots. Windows Job-at-creation attributes remove
the create-then-assign ownership gap; membership, PID, creation time, executable,
argv/config hashes and priority are verified before resume. A one-process Job
limit prevents either role spawning a hidden third engine. Jobs kill owned
children on supervisor death/close; unrelated handles are never inherited.

Canonical viewer data is **the service-contained**
`C:\DoxxedCrypto\v2c\services\btc-conservative-agent\canonical-research-data`,
with reports in its `analyzer` subdirectory (verified against R47 and viewer source).
These exact nested data/report paths within the viewer service are intentional
exceptions to ancestor-overlap rejection; arbitrary aliases/nesting are rejected.
PMC stdout/stderr live only in `data\collector-r2-logs`; viewer logs only in
`v2c\logs`. Nothing is written by the supervisor to either data/report root.
The collector alone creates its own three permitted root metadata files and
UTC YYYY-MM-DD directories. A foreign root entry latches without cleanup.

## Restart and durability contract

* Initial start is not a restart. Only an actual OS exit **1**, without import,
  ownership, Fly or other terminal reason, can retry. Import/syntax/DLL failures
  latch even with exit 1. A failed spawn consumes its prior durable reservation.
* Backoff is 6,12,24,48,96,192,300 seconds capped, no jitter, monotonic within a
  run. Maximum three reservations per role/rolling 1800 seconds; fourth latches.
  Exponential index resets only after 1800 uninterrupted healthy owned seconds,
  not on invocation/reboot. Reservations persist and are never reset by time
  expiry of a latch or by a human clear.
* **Collector exit 0 ALWAYS latches `TERMINAL_EXIT_0`, across invocations.**
  This is the stricter outer rule, explicitly reconciled with S3: normal PMC
  completion is NOT permission for the outer supervisor to launch it again.
* 75/78/79, unknown, missing code, signed/unsigned NT termination and signals
  latch. Raw numeric/hex codes are retained; no synthetic rc=75 on termination.
* Crash at pending/running/exit/backoff, torn/checksum-bad/missing initialized
  state, clock rollback, identity drift or lost writes blocks starts. An unresolved
  prior intent becomes `UNCERTAIN_PREVIOUS_RUN`; there is no automatic takeover.
* The OS cross-session byte-range lock is acquired before journal reads. Lock
  file remains permanent. `supervisor-state.jsonl` is append-only, hash-chained,
  sequenced, fsynced, implementation/config-bound, capped at 32 MiB with 64 KiB
  reserved for STOP records. No temp/rename/truncate/rotate/delete operation.
  No implicit `missing file == first boot`: an approved installation card must
  pre-create/pin the genesis, config, one-byte permanent lock and empty log files.

PMC's existing internal logical-run retries and transport reconnects are NOT
bounded by this OS-process restart budget. Their journal counts are reported
separately. This PR does not alter those collector lifecycle rules.

## Human-only clear and manual/Task recovery

Never restart blindly, delete a journal, repair a root, or pass a `--force` flag.
Manual invocations, Task startup and reboot all honor the same durable latches.
No HTTP mutation/reset endpoint or auto-clear exists.

A separately authorized human recovery card must identify the role, latch ID,
previous state hash, remediation, new code/config/root pins and a named human
(`who`, `why`). An **offline human-held RSA key** signs that exact receipt
(RSASSA-PKCS1-v1_5/SHA256, 2048..4096 bits, exponent 65537). The public-key SHA
is pinned in config AND the original genesis; ordinary config drift cannot
replace it. This supervisor never reads a private key, signs or creates a clear.
The card installs `human-clear.json` and its digest in approved config, with the
pinned `human-clear-public-key.json` outside data roots. `recovery.py` defines the
exact receipt fields. Validation appends `HUMAN_CLEAR`, preserving who/why,
remediation, original signature and old latch evidence; replay verifies signatures
and cannot reuse a clear for a later latch. A new authorized invocation is a
distinct operation. PMC's own refusal/latch/vocabulary gates remain independent.
If no human public key was provisioned at genesis, this clear mechanism remains
unavailable; do not create a new genesis to evade it. Key rotation/archival needs
its own future approved migration, not this PR.

## Local health and disk (not a restart trigger)

Only `127.0.0.1:9040/` and `/health.json` (`/health` alias), GET/HEAD, exact Host.
No proxy, CORS, upload, query-target, cloud call, reset or LAN bind. Handler
threads are capped at eight with one-second socket bounds; they only copy cached
snapshots. Sampling runs separately every 10 seconds so slow HTTP/filesystem
sampling cannot starve actual exit monitoring. Sample age >30 seconds is UNKNOWN
(AMBER/HOLD), not a fresh green heartbeat. HTTP 200 with any latch is never GREEN.

PMC reads only current/previous UTC day plus bounded active segment paths and
incremental checksummed journals (256 KiB/read, 256 directory entries/tick).
Reports all four streams, receipt validity, received/durable progress, gaps,
checksum faults, invalid book, future-mtime >5s uncertainty and 90s staleness.
Quiet trades require advancing confirmed trades-heartbeat evidence; file mtime
is not coverage proof. No first verified snapshot => UNKNOWN. No gap fill.

Viewer probe is only `127.0.0.1:9001/api/health` (one-second timeout, 64 KiB cap,
no redirects/proxy); PID/roots/report-current/readiness are checked separately
from liveness. Startup grace is at most 120s. A bad probe or stale report never
kills/relaunches a live viewer. Any observed Fly-linked error/timeout persists
**FLY_SIDE_PATH_FAILURE / HOLD** even if rc=1. **No viewer UI change** is made.
Unchanged full-browser `/api/system-health` Fly side paths remain a separate HOLD;
local supervisor health is not proof of full-browser cloud independence.

Disk: 100 GiB R50 research reserve; 20 GiB new-collector OPS floor; collector's
own 512 MiB floor is unchanged. Unmeasurable free space blocks new starts.
Projections exist only from measured free-byte deltas (not a PMC-only forecast).
No research/backtest jobs are launched by this PR.

**Running disk-floor capture kill ships DISABLED**:
`disk_floor_terminate_enabled=false`, `disk_floor_card_sha256=null`.
Crossing 20 GiB still latches/HOLD and forbids another start, but preserves an
alive collector by default. Enabling graceful stop then owned termination after
10s requires a separately approved card/digest in config. It is not enabled by
this spec/design PASS. Supervisor shutdown/crash Job containment is a different
owned-process safety path; actual shutdown exit uncertainty remains recorded.

## Tests and acceptance limits

`python -B -m pytest scripts/pmc_supervisor_tests -q -p no:cacheprovider`

`.github/workflows/laptop-supervisor-tests.yml` adds Linux/Python311 and **real
Windows x64/Python311** jobs, including ctypes prototype-deletion assertions,
synthetic Job membership/exit/kill/handle/priority/junction tests, visible/hidden
startup, redirected UTF-8 and Arrow system-pool checks. Windows also runs the
existing injected keyless collector-transport and PR999 regression tests. No
production roots/live feed/order APIs are used. CI's disposable fixture roots are
not permission to create laptop fixture roots.

Deterministic tests exercise terminal exits, rolling budget/backoff/reset,
cross-invocation rc=0 and other latches, torn/checksum/missing state, power-loss
phase/failure injection, ownership/PID refusal, root/engine injection, scrub
canaries, HTTP/health/staleness/quiet trades/disk thresholds, bounded logs/journal,
prototype mutation inventory and signed human-clear replay.

**Still NOT TESTED / acceptance HOLD:** laptop installation or runtime;
different Windows logon sessions (CI verifies different processes, OS byte-lock
semantics are cross-session, but that is not an observed two-session test);
physical power loss or real disk-full; console-close/logoff OS-timeout behavior
and noninteractive Task Ctrl-Break delivery; real Windows startup-denial recovery
on the installed Python/dependency tree; 24h reliability, collector rate,
7-day continuity, live existing-browser behavior, R59 research/backtests/paper
compute. Visible/hidden CI verifies the selected STARTUPINFO modes, not a human
visual-QA receipt. No synthetic/CI PASS may be relabelled as laptop reliability.
The required separate physical/session/Task prototype card remains outstanding.

Keep this PR DRAFT. HM/RK source/prototype review, exact-head CI, merge,
installation, first-run and recovery gates are separate. Live stays OFF.
