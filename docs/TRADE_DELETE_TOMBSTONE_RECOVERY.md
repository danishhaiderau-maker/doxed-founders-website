
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
