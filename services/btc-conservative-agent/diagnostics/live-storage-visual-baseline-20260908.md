# Live dashboard visual baseline

Observed through in-app browser on 2026-09-08, deployed revision 2db7a09c8574. This is NOT acceptance of candidate 3f57d7f.

- Data navigation lands on activity tables; table containers scroll horizontally rather than overflowing the page.
- Storage screenshot shows 419.4 MB inventoried runtime files, stale-revalidating label, 3996.78 MB capacity and 63.9% filesystem usage. The stale value dominates visually and has no generation age. Candidate 3f57d7f addresses this distinction; rendered candidate acceptance remains pending.
- Empty virtual-chase explanation is clipped inside the horizontally scrolling table. A short visible empty-state summary outside the table would improve readability.
- Live AX state reports PAPER ENTRIES: THREAD_CRASH and BITFINEX LIVE: BLOCKED - DISARMED; one paper position and no pending orders were displayed.
- Incident history only displays legacy September 5 dumps, not the September 8 position-open commit failure found in persistent logs. Do not interpret this table as a complete incident history.
- The session funnel displays FILLED=0 while lane totals report fills. Their scopes must be clarified or reconciled before dashboard accounting QA can pass.
- No trading, reset, wipe, close-position or configuration controls were activated. Full desktop/mobile navigation and candidate QA remain incomplete.
