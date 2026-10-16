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
