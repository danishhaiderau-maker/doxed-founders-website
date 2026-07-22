# Founder V1 Release Contract

Updated: 2026-07-22

This contract turns the Founder V1 plan into measurable gates. A feature is
not complete while its runtime, evidence, or user journey remains unfinished.

## Usage unit

Founder-managed quota uses weighted token units (WTU):

```text
WTU = uncached input
    + (cached input x 0.25)
    + (visible output x 3)
    + (reasoning output x 3)
```

The first release policy is 200,000 WTU per seven-day Founder Free window.
Personal keys and local models record raw telemetry but consume zero managed
WTU. Provider USD cost is recorded separately. WTU is not an invoice and must
not be presented as a token-saving claim.

## Benchmark protocol

- Use 50 tasks minimum and 100 preferred, spanning questions, edits, tests,
  debugging, refactors, and multi-file work.
- Run every task five times in baseline and Founder modes.
- Pin repository commit, model and provider, temperature, max output, tool
  access, starting state, and acceptance tests.
- Record raw input, cached input, visible output, reasoning, WTU, provider USD
  cost, latency, task success, and test success.
- Publish medians and distributions. Do not advertise a savings percentage
  until the controlled result supports it.

## Proof receipt

Every completed local or remote task returns a versioned receipt with:

- changed file paths, before/after SHA-256, additions, and deletions;
- tests with exit status, duration, result summary, and whether the test was
  pre-existing, agent-written, or manual;
- command output hashes and bounded previews;
- raw token usage, WTU, billing source, and provider USD cost when available;
- explicit failure codes, stages, retryability, and cancellation/block state;
- a receipt hash and previous-receipt hash for tamper-evident chaining.

## Coordination model

Founder uses a hybrid local-first ledger:

- the local workspace is authoritative for an agent's own lease, file intent,
  decisions, and receipts, so work continues offline;
- the server mirrors compact lease and conflict metadata for cross-device and
  website awareness;
- the server never overwrites local work silently;
- conflicting file intents require coordination or a bounded ownership change;
- stale completions are rejected using task generation and lease fencing.

## Stage dependencies

- Context graph (Stage 2) and release/signing work may proceed independently.
- Efficiency benchmarks (Stage 3) require the Stage 1 measurement contract.
- Cost reservations (Stage 4) require Stage 1 WTU and provider cost telemetry.
- BYOK and managed routing (Stage 5) may overlap Stages 2-4 but cannot ship
  managed AI until reservations reconcile without overshoot.
- Coordination (Stage 6) may overlap provider work and is required by remote
  actions (Stage 9).
- UI (Stage 7) and Dragon (Stage 8) may begin earlier, but Dragon states and
  savings messages must consume real measurement/task events.
- Website alignment (Stage 10) may progress route by route, but cannot claim
  capabilities that have not passed their owning stage.
- Public release (Stage 11) requires every gate below.

## Release gates

Private beta requires unit, integration, installed-app, rollback, and cost
reservation reconciliation gates. An unsigned private beta is allowed only
with an explicit warning and restricted distribution.

Public release additionally requires:

- clean Windows VM install/uninstall evidence;
- 24 hours of soak evidence;
- verified Authenticode signing;
- at least 20 beta founders and 200 completed tasks;
- critical failure rate at or below 2 percent;
- week-two retention at or above 35 percent;
- zero unreconciled cost reservations.

Signing identity validation is an external gate. It blocks public release, not
implementation or private-beta QA.

## Invite containment

Beta invites are single redemption, bound to the first device, auditable, and
revocable. A leaked code must be individually disabled without rotating every
other founder's access.
