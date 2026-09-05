# Bounded snapshot query projection repair

Authenticated Neon query history showed full PlatformSettings SELECT and
snapshot-upsert RETURNING fingerprints still accumulating calls. This proves
ongoing broad queries, not a billing-period attribution or realized savings.
Multiple callers can share the full SELECT fingerprint.

The snapshot service only needs the previous sequence before ingest; its upsert
return value is unused; its cache reader needs snapshot, sequence and timestamp.
The repair explicitly selects those fields and returns only id from the upsert.
No snapshot payload, authentication, sequence, timestamp or stale-owner check is
changed. No polling interval, relay behavior or database schema is changed.

Independent implementation regression: three projection tests failed before the
repair, then snapshot/bridge suites passed 28/28. Full API TypeScript no-emit
check passed. Root review reran actual TypeScript snapshot/bridge suites:

`node_modules/.bin/tsx.cmd --test apps/api/src/trading-agents/showcase-snapshot.service.spec.ts apps/api/src/trading-agents/bot-bridge.service.spec.ts`

28 passed, zero failed/skipped. Existing tests preserve authenticated canonical
ownership, stale/tampered rejection, bridge cache behavior and sequence handling.
New tests assert Prisma query arguments and preserve complete snapshot contents.

This is source evidence only. Deliver through an exact-source API-only workflow,
not an unbound API-plus-executor restart. Confirm deployed source and projected
SQL fingerprints, then compare equivalent Neon compute/transfer periods before
claiming monetary savings. Fly research transfer remains direct to the laptop.
