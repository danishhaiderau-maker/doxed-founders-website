# R2 backup rewrite (HM c) — not installed on the laptop by this change

* `r2_backup.py`: drop-in for the laptop script; adds `reconcile`, keep-manifest-pinned `restore-test` (HEAD first,
  misses counted separately), receipt v2 (`state_keys`, `r2_objects`, `missing`, `quarantined`, `quarantine_pending`).
* `r2_quarantine_plan.py`: DRY RUN ONLY. Lists the objects uploaded 13 Oct 16:00-17:00 UTC (14 Oct 03:xx AEDT) that are
  not pinned. No move/delete code exists; the move needs Danish's go and a separate reviewed change keyed to `plan_sha256`.
* Pinned set = `r2_keep_prefixes` of the KEEP manifest (`--keep-manifest keep.json` or `--keep-prefixes a/,b/`).
  NOTE: the #670 manifest has `R2_KEEP_PREFIXES = ()` ("nothing in R2 is kept"); with an empty pin `sync` refuses and
  `restore-test` fails (empty pool is not proof) unless `--allow-empty`. Add the archive prefix for repriced_trades.csv
  to the manifest before relying on R2.
* Fly side: `monitor_checks.archive_receipt` is RED on missing > 0 or a failed/empty restore test and AMBER while
  quarantine objects remain.
Order on the laptop (machine approval): `reconcile` (dry) -> `reconcile --apply` -> `sync` -> `restore-test --sample 100`.


## Review fixes (HM, #678)
* `quarantine_pending` is its own receipt field and is NOT part of `ok`; Fly shows AMBER while it is > 0 and a clean
  bucket is GREEN. `outside_keep > 0` is AMBER too. Missing / failed restore test / errors stay RED.
* New dedicated top-level R2 prefix `epoch-archive/` (ROOTS entry `epoch-archive` -> `C:\DoxxedCrypto\epoch-archive`),
  the only entry of `R2_KEEP_PREFIXES` in the keep manifest (see the **#670-based additive commit** on this PR:
  `keep_manifest.py` = #670 head d1518674 + one-line delta, `MANIFEST_SHA256` re-pinned in `test_keep_manifest.py`).
  Never under analysis-archive/, bot-data-* or fly-mirror-segments/.
* `quarantine_candidates` raises `QuarantinePlanError` if ANY candidate in the window is pinned; `build_plan` therefore
  fails instead of silently skipping it.
* `r2_quarantine_plan.py verify --plan ... --expect-plan-sha256 ...` (read-only) re-lists the bucket and re-verifies
  plan_sha256, size and last-modified per key, pinned keys and late arrivals. The (still unwritten) move step must run it
  immediately before moving.
* The receipt POST: the repo has no copy of the laptop job. Per WAITING-ON (#587/#588) the laptop `r2_backup.py`
  posts the receipt itself (task `run-r2-backup.ps1`, vault BOT_ADMIN_TOKEN). This rewrite now does the same:
  `post_receipt()` sends the whole v2 receipt (`RECEIPT_V2_FIELDS`) after `sync` / `restore-test`; the Fly route's
  whitelist is verified against that list by `test_archive_receipt_v2.py`. NOT verified: whether the CURRENT laptop script
  forwards v2 fields (it is the old script and is not in the repo; no laptop access). Installing this drop-in is what
  makes it forward them.

## Receipt POST hardening (HM #678 round 2)
* `post_receipt` is opt-in: `--post-receipt` or `R2_POST_RECEIPT=1`. Token = `R2_RECEIPT_TOKEN` only (header
  `X-R2-Receipt-Token`); the Fly admin token is never read. Refuses under `PYTEST_CURRENT_TEST` (real network), and only
  `https://doxed-btc-bot.fly.dev/api/monitor/archive/receipt` is accepted (not overridable).
* Receipts carry `kind` (`sync` | `restore_test`) and `host`.
* Fly route (`monitor_archive_receipt_post`): auth is `R2_RECEIPT_TOKEN` (a Fly secret that must be SET before the
  laptop can post; unset -> 503; the admin and monitor tokens get 401). Validates schema `r2_backup_receipt_v2`, host,
  `keep_manifest_sha256` == Fly's pin (sha256 of `{"r2_keep_prefixes": sorted(keep_manifest.R2_KEEP_PREFIXES)}`),
  `state_keys >= r2_objects > 0`, `0 < restore_test.checked <= min(1000, r2_objects)`, `finished_ts` within 6 h and newer
  than the stored one. Every POST (accepted or rejected) is appended to `archive_receipts.jsonl`. A `restore_test`
  receipt only replaces the stored `restore_test` (+ `restore_test_finished_ts`); counts, `quarantine_pending`,
  `outside_keep` and the sync receipt's AMBER state are untouched.
* DEPLOY NOTE: legacy-shaped receipts (no schema) are now rejected with 422, so the old laptop poster stops until this
  script is installed and `R2_RECEIPT_TOKEN` is set on both sides. The monitor then goes stale-AMBER (36 h), not GREEN.

## Round 3 (HM #678 B1 + follow-ups)
* Plausibility rules (`r2_objects > 0`, `0 < restore_test.checked <= min(1000, r2_objects)`, `state_keys >= r2_objects`)
  apply ONLY to `ok:true` receipts. An `ok:false` receipt (empty `epoch-archive/`, total bucket loss) is accepted with
  `r2_objects >= 0` / `checked >= 0` if schema, host, pin, `finished_ts` (<= 6 h old, newer than stored) and types are
  valid, and shows RED (`R2 holds 0 objects...`, `restore test failed...`, `backup job reported ok=false`). `ok` must be a bool.
* A receipt older than 72 h is RED ("no sync receipt for 2+ nights"); 36-72 h stays AMBER.
* 401 and 503 attempts are logged to `archive_receipts.jsonl` (status, remote ip, whether a token/admin header was
  present; never the token). The file rotates to `archive_receipts.jsonl.1` past 2 MiB (one generation).
* The generic "archive errors=0" reason is gone: it prints only when `errors > 0`; a bare `ok:false` says
  "backup job reported ok=false".
* `keep_manifest.py` / `test_keep_manifest.py` commit `bfc1f4f6` ([#670-BASED, ADDITIVE]) stays: #670 is not merged. Drop
  it, and apply `/workspace/grokbot/audit/678-keep-manifest-delta-on-670-d1518674.patch`'s one-line delta on top of #670,
  only after #670 merges.
