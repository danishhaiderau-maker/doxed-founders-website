# Post-AI completion counter gap — 2026-09-05

## Scope and evidence identity

Read-only source diagnosis and local executable probe against production revision
`cb7745e827e179f598b891bcefa136d47f00c8ee`. No source, production, sync, or order
changes were made by this audit. The parent supplied the production observation:
06:14:10 UTC, PID 662, paper-only/unpaused, post-AI submitted/completed 26/26;
reversal worker completed 12 with one dead letter for
`reversal_study:scan-e2121c2fbd28`, reason `retries_exhausted`; last gap
`HOOK_TIMEOUT` at `2026-09-05T15:58:23+10:00`. This note does not independently
re-probe that runtime snapshot or certify that episode's stored outcomes.

## Findings at the exact committed revision

- `services/btc-conservative-agent/bot.py:3517` configures each post-AI worker
  with `max_retries=0` and `handler_timeout_sec=5.0`. A `retries_exhausted`
  record therefore need not imply any retry: attempt 1 can exhaust the budget.
- `bounded_evidence_worker.py:51` runs the handler in a daemon thread. A timeout
  does not kill that thread. While it remains alive, subsequent jobs are refused
  by the timeout path instead of spawning additional helpers.
- `bot.py:3483` increments global `completed` inside the handler. A late handler
  can increment it after the worker already dead-lettered the job. Worker-level
  completion (`bounded_evidence_worker.py:150`, `:160`, `:213`) only records a
  successful return through the worker's invocation path. These counters have
  different semantics; global submitted=completed is not evidence completeness.
- `bot.py:3483` also counts a reversal hook returning `False` as completed and
  labels it `REPLAY_LOCK_TIMEOUT`. `start_reversal_study_replay` at `bot.py:15096`
  can return `False` after an append failure/suppression, not just a replay-lock
  failure. It may return `True` without starting a study when collection is off
  or required signal/risk/price context is absent.
- `bot.py:3504` maps the timeout exception text to `HOOK_TIMEOUT`. The compact
  dead-letter's generic reason and this mapped gap reason can differ legitimately.
- `bot.py:48866` shows possible write-path waits: research/path locks,
  validation/rotation, filesystem append and fsync. Replay and final state-lock
  acquisition are additional possible waits. Timeout alone proves none of these
  was specifically responsible, and does not prove inventory contention.

All `bot.py` line numbers above refer to the pinned `cb7745e8` blob, not the
moving working tree. Worker references are under
`services/btc-conservative-agent/bounded_evidence_worker.py`.

## Executable local probe

Run from `C:\DoxxedCrypto\btc-v31-current` in PowerShell. The probe extracts
the exact committed handler and worker into memory, supplies isolated test
stubs, creates no files, and contacts no production endpoint. Its deliberately
short timeout tests control flow, not real production performance.

```powershell
@'
import ast, subprocess, threading, time, json
rev = 'cb7745e827e179f598b891bcefa136d47f00c8ee'
def blob(path):
    return subprocess.check_output(['git', 'show', rev + ':' + path]).decode('utf-8')
worker_ns = {}
exec(compile(blob('services/btc-conservative-agent/bounded_evidence_worker.py'),
             '<committed-worker>', 'exec'), worker_ns)
BoundedEvidenceWorker = worker_ns['BoundedEvidenceWorker']
source = blob('services/btc-conservative-agent/bot.py')
node = next(n for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef) and n.name == '_run_post_ai_evidence_hook')
ns = {'state_lock': threading.Lock(), '_post_ai_evidence_status': {'completed': 0},
      'scheduled_ai_cycle_state': {}, 'time': time}
gaps = []
ns['_record_post_ai_evidence_gap'] = lambda *args: gaps.append(args)
ns['start_reversal_study_replay'] = lambda *args: False
exec(compile(ast.Module(body=[node], type_ignores=[]), '<committed-hook>', 'exec'), ns)
ns['_run_post_ai_evidence_hook']({'key': 'test-false', 'payload': {'hook': 'reversal_study'}})
print(json.dumps({'probe': 'false_return_counts_complete',
                  'global_completed': ns['_post_ai_evidence_status']['completed'], 'gap': gaps}))
release = threading.Event()
ns['start_reversal_study_replay'] = lambda *args: release.wait(1)
worker = BoundedEvidenceWorker(ns['_run_post_ai_evidence_hook'],
                               max_retries=0, handler_timeout_sec=.02)
worker.submit('test-timeout', {'hook': 'reversal_study'})
deadline = time.monotonic() + 1
while not worker.snapshot()['dead_letters'] and time.monotonic() < deadline:
    time.sleep(.005)
before = worker.snapshot()
release.set()
deadline = time.monotonic() + 1
while ns['_post_ai_evidence_status']['completed'] < 2 and time.monotonic() < deadline:
    time.sleep(.005)
print(json.dumps({'probe': 'late_completion_after_deadletter',
                  'global_completed': ns['_post_ai_evidence_status']['completed'],
                  'worker_completed': worker.snapshot()['completed'],
                  'deadletter_reason': before['dead_letters'][0]['reason'],
                  'attempt': before['dead_letters'][0]['attempt'],
                  'alive_after': worker.snapshot()['timed_out_handler_alive']}))
worker.shutdown(drain_timeout=1)
'@ | python -
```

Observed output: the original probe imported the identical working-tree worker;
the command above was then rerun from this document with both modules pinned
explicitly and returned the same output, exit code 0:

```json
{"probe": "false_return_counts_complete", "global_completed": 1, "gap": [["reversal_study", "REPLAY_LOCK_TIMEOUT", "test-false"]]}
{"probe": "late_completion_after_deadletter", "global_completed": 2, "worker_completed": 0, "deadletter_reason": "retries_exhausted", "attempt": 1, "alive_after": false}
```

## Recovery limits and next receipts

The queue, dead letters, and payloads are memory-only. There is no automatic
durable retry in this worker. The parent's replacement deployment supersedes
the old `cb7745e8` runtime; process-memory payloads cannot be recovered by
assuming global counter equality meant successful persistence. This audit does
not independently certify the replacement's deployed SHA or completion.

For `scan-e2121c2fbd28`, obtain its exact gap log/detail and
`rev-scan-e2121c2fbd28` reversal start/outcome records and replay path. Late writes
may already exist; inspect before any replay attempt. Starting a replay now
would change its time origin and cannot recreate missing historical market
observations. Only preserved original context plus sufficiently complete market
paths could support an explicitly historical reconstruction. Missing evidence
remains UNKNOWN.

If another timeout occurs, preserve the timed-out helper's stack, replay-lock
owner/age and file-write timing alongside inventory activity. Distinguish lock,
filesystem, CPU/scheduling and deterministic exception paths from those receipts;
do not attribute causality from timing overlap alone. Do not increase timeouts,
retry blindly, or count these jobs as qualified evidence to clear a dashboard.

Disposition: retain this as an open evidence/telemetry defect for measured
correction after the current transfer critical path. The running guarded
deployment proceeds unchanged; this read-only audit adds no exemption or gate
relaxation.
