# Trade delete-by-id tombstone: states and manual recovery (HM #869 R2b/R2c)

`deleted_trade_ids.json` (bot cwd, `/app/data/runtime` on Fly) records the trade ids removed by
`POST /api/admin/trades/delete-by-id`. It is written (tmp + fsync + `os.replace` + directory fsync) **before** the
CSV rewrite, for the ids actually present, and settled by read-back afterwards (ids still in the CSV are removed).

| status | meaning | effect |
|---|---|---|
| `OK` | file valid or absent (no quarantine) | normal |
| `CORRUPT` | file unreadable / bad schema (quarantined to `deleted_trade_ids.json.corrupt.<UTC>.<pid>[.n]`), or a quarantine exists with no valid file | `[ALERT]`; system-health `fly.trade_delete_tombstone` RED; CSV_TRADES fallback replay held (rows stay pending); delete-by-id 409 `TOMBSTONE_CORRUPT`; ids salvaged from the quarantined bytes refused on write; every CSV_TRADES row written is `non_countable` with reason `TOMBSTONE_CORRUPT`. Trading keeps running. |
| `PERSIST_FAILED` | tombstone write failed before a delete | 500 `TOMBSTONE_PERSIST_FAILED`, no rows deleted; RED until the next good write |
| `DELETE_UNCERTAIN` | delete raised after the tombstone was written, or an invariant restore was not confirmed by read-back | 500 `DELETE_STATE_UNCERTAIN` / `RESTORE_UNCONFIRMED`; ids stay tombstoned (fail closed); RED (persisted in `deleted_trade_ids.json.uncertain`; survives restart until reconciled) |

Counters: `/health` and `/api/status` → `deleted_trade_resave_refused` (`tombstone_status`, `tombstone_quarantined_to`,
`tombstone_salvaged_ids`, `tombstone_corrupt_non_countable_writes`).

## Manual recovery from CORRUPT (operator; never delete the quarantined file)
1. Read the `.corrupt.*` file(s) and the `[ALERT] [TRADE-DELETE-BY-ID]` log lines. Rebuild the id list from the
   quarantined bytes plus the RK delete-list files (their `list_sha256` values) — the union of every id ever deleted.
2. Check none of those ids is still a row in `trades_3factor.csv` (if one is, it was not deleted; leave it out).
3. Write a valid file next to the quarantine, atomically (tmp then rename), `0644`:
   `{"schema": "deleted_trade_ids_v1", "updated_at": "<UTC ISO>", "ids": [...sorted...], "list_sha256s": [...]}`.
4. Move the `.corrupt.*` file(s) into a dated archive folder outside the runtime dir (keep them; do not delete).
   A valid main file already reopens on restart even if a quarantine remains; moving it just keeps the dir clean.
5. Restart the machine. Confirm `/health` `tombstone_status == "OK"` and `tombstoned_ids` == your count, system-health
   `fly.trade_delete_tombstone` GREEN, and the held CSV_TRADES fallback rows replay (deleted ids counted under
   `replay_count`, not written).
6. Rows written while CORRUPT keep `non_countable_reason` containing `TOMBSTONE_CORRUPT`; re-qualifying any of them
   is an RK decision, not part of this recovery.

## DELETE_UNCERTAIN recovery

Raised when the CSV write happened but its outcome could not be confirmed (restore unconfirmed, or an exception after the write). The ids are written, fsynced, to `deleted_trade_ids.json.uncertain` (`schema: delete_uncertain_v1`). While that file exists, every delete-by-id returns **409 DELETE_UNCERTAIN** and `fly.trade_delete_tombstone` is RED, including after a restart.

At boot `reverify_delete_uncertain` reads the trades CSV back against the marker ids:
- **None of the ids are in the CSV and all are tombstoned:** the delete landed. The marker is renamed to `.uncertain.resolved.<UTC>` (kept, never deleted) and the status returns to OK.
- **Any id is still in the CSV, or the marker is unreadable:** the status stays RED, and the error says how many ids are still present.

Manual steps when it stays RED:
1. Read the marker ids and the `tombstone_uncertain_ids` count in /system-health.
2. Compare them against the CSV and the newest `pre_delete` snapshot. Decide whether those rows should exist.
3. If the rows should stay deleted, remove them with a normal reviewed delete once the marker is cleared. If they should stay, untombstone those ids in `deleted_trade_ids.json` (as an atomic edit).
4. Rename the marker to `deleted_trade_ids.json.uncertain.resolved.<UTC>`. Do not delete it.
5. Restart. Then confirm that the status is OK and that `tombstone_uncertain_ids` is 0.
