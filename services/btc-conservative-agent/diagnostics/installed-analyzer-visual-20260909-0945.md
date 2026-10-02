# Installed analyzer visual observation — 2026-09-09 09:45 AEST

Read-only inspection of http://127.0.0.1:9001/ through in-app browser. Existing process listener: PID 12244. No restart or publication performed.

- Overview navigation rendered successfully; desktop screenshot inspected at approximately 1264 x 714 viewport.
- Header says analyzer revision UNKNOWN, epoch UNBOUND, latest analysis FAILED and no report published.
- Three strategy tiers render explicit UNAVAILABLE with source/epoch parity and report-generation blockers, not a current winner.
- Expanded freshness receipts report ALL-DATA includes pre-wipe history, unavailable dataset/mirror revision and epoch, and failed/unavailable canonical sync receipt.
- Therefore the installed page is not a QA pass for current-source strategy delivery. Restore verified current mirror and publication before qualification; do not infer raw source data is unusable or repeat the completed reset.
- Desktop Overview and freshness disclosure only checked here. All other journeys and mobile remain pending; this is not full visual QA.

Browser extension connection timed out first; in-app browser inspection succeeded. No evidence of process crash was inferred from that connection timeout.
