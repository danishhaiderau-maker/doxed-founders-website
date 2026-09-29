# Fly application starvation incident — 2026-08-31

Status: **OPEN — PRODUCTION RECOVERY NOT YET VERIFIED**

## Safety state

- Production revision observed by the last verified mirror: `a618301f6bdd`.
- Tested recovery revision at the time of this receipt: `079845fe8e149a02d7b896ce6cb847fd960fcd23`.
- Bitfinex live trading and the live relay were not enabled by this incident response.
- New entries failed closed after market readiness was lost (`WS_NOT_READY` and `REST_ENTRY_QUOTE_NOT_READY`).
- No pending paper order was cancelled or altered.
- No restart or deployment was attempted because current open/pending exposure could not be read authoritatively.

## Authoritative observations

Read-only workflow runs `33390193773` and `33391074685` observed:

- Fly machine `7844910f3024d8` remained `started` with `HostStatus: ok`.
- The machine start time remained `2026-08-30T21:42:39Z`; no intervening Fly machine restart was observed.
- The HTTP service check was critical because response headers timed out.
- Bounded SSH application probes timed out after 45 seconds.
- Public proxy requests reported that no healthy candidate could be selected.
- Recorded handler latency reached approximately:
  - `/health`: 30.231 seconds.
  - `/api/relay-state`: 101.971 seconds.
  - `/api/relay-execution-state`: 25.636 seconds.
- Handler admission later rejected requests with `class_cap_full`.
- The last available in-process watchdog exposure receipt, at application time
  `2026-08-31T11:29:27Z`, reported `positions=0 pending=0`.
- That exposure receipt is stale. Current exposure is therefore `UNKNOWN`, not
  deployment-authorizing flatness.
- The canonical mirror worker recorded 14 consecutive failures and entered a
  deterministic 1,800-second backoff. Its latest failed preflight was
  `2026-08-31T12:22:13.5407168Z`.
- The analyzer rejected the mirror with `MIRROR_SYNC_RECEIPT_FAILED` and did not
  replace the previous valid published generation.

## Classification

Strongest proven classification:

`APPLICATION_LEVEL_STARVATION_OR_CONTENTION_ON_HEALTHY_FLY_HOST`

The exact initiating code path is **not proven**. Synchronization pressure is a
plausible amplifier, but it is not proven to be the sole cause: the application
remained unavailable during the synchronization worker's quiet backoff window.

There is currently no authoritative evidence of:

- an OOM kill;
- a Fly host failure;
- a deployment restart;
- a Fly machine restart during the incident; or
- a specific handler, lock owner, or thread as the initiating cause.

Those causes must not be inferred from endpoint unavailability alone.

## Repairs prepared but not yet production-verified

The tested recovery stack includes bounded relay/state-lock waits, O(1) storage
projection, synchronization backoff and a resource-pressure circuit breaker,
bounded read-only Fly diagnostics, truthful analyzer/evidence freshness gates,
and structured application incident history. Revision
`079845fe8e149a02d7b896ce6cb847fd960fcd23` contains the complete current stack.

Local verification supporting the recovery stack:

- Combined integrity selection: `150 passed, 1 skipped`.
- Runtime incident/dashboard selection: `13 passed`.
- Bounded Fly diagnostic workflow completed rather than hanging.

These tests prove source behavior only. They do not prove production recovery.

## Guarded recovery condition

Before any restart or deployment, obtain a fresh authenticated authoritative
receipt proving both:

- pending paper orders = `0`; and
- open positions = `0`.

Then deploy the exact tested HEAD through the existing guarded Fly workflow.
After deployment, keep paper-only/live-disabled/disarmed state and verify:

1. exact deployed revision;
2. responsive health, state and relay endpoints;
3. stable WebSocket and REST entry quotes;
4. advancing AI, chase, expiry and collection cycles;
5. no unexplained restart or starvation recurrence;
6. acknowledged canonical mirror revision/epoch/config parity; and
7. a current atomic analyzer generation.

Until those checks pass, the incident remains open and Bitfinex readiness is
`NOT READY`.
