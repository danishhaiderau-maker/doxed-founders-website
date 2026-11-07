# event_replay_v1

Fixture-only synthetic CPU lifecycle reference. It replays manufactured
full-lifecycle traces through one quote/trigger/fill pricer. It is not a
research run, not an R57 permutation sweep, not a profitability result, and
not a live or paper-trading path.

Live is OFF. `realistic_v1` is not resolved here.

## Stage A dependency

Stage A is an approved **design** dependency only. The design card digest is
`ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122`.

No Stage A implementation pin exists in this package. Normalized records are
explicit stubs:

`FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED`

Actual interface compatibility is **BLOCKED**. This package does not edit or
redefine producer schemas.

## What the reference does

- One pricer serves every fixture strategy and lifecycle action.
- Entry fills are strictly later than the signal-bar close, after the
  explicit synthetic arrival delay, inside a visible book interval.
- Marketable orders walk opposite-side depth for executable quantity.
- Passive orders fill at the resting limit only after traded-through or
  queue-consumption evidence. Touch alone does not fill. Passive fills are
  not repriced as taker walks.
- Missing or stale depth is invalid for fills. Nothing is interpolated or
  switched onto another venue.
- Cancels wait for their acknowledgement. In-flight partials still count.
  Duplicate acknowledgements are idempotent. Quantity is conserved.
- Composite exits share a frozen fixture tie order: hard stop, liquidation,
  thesis cut, ladder, breakeven, trail, giveback, target, time. That order is
  not live policy. A trigger does not fill at the trigger price.
- Liquidation uses the fixture identity only when maintenance terms are
  supplied. Unknown maintenance leaves the scenario invalid and the position
  unresolved. Size is never rounded up. Dust and rejected partials are
  recorded. Margin is not maximum loss.
- Missed, unfilled, and rejected signals stay in the denominator with zero
  realized contribution. Unresolved positions stay incomplete. Their realized
  result is null, not a fabricated closed zero.

## Fee and cost disposition

- `trading_fee` is the explicit account assumption `0` (Danish R7 account
  statement). It is not independently evidenced. A missing fee is an error.
  There is no implicit zero and no nonzero default.
- `fee_verification_evidence` is null / unverified. That does not override
  the account assumption. It is not a venue-wide claim.
- Measured spread, depth, and latency costs are `NULL/BLOCKED` until
  separately pinned observed data.
- Queue sizes and delays are `SYNTHETIC` only. They are not venue proof.
- Funding charges are excluded and not modelled. The amount is null, not
  zero. There is no collection gate.

Real policy values, dates, N, max hold, and cost threshold stay
`NULL/BLOCKED`.

## Bounds

The harness enforces, and stops on, these caps:

- 2 CPU workers
- 512 MiB RSS
- 120 seconds per command
- 600 seconds per suite
- 8 MiB receipts
- 2,048 manufactured events

Missing enforcement fails acceptance.

## Run

From the repository root, with the standard library only:

```bash
python research/event_replay_v1/tests/test_acceptance.py
python -m research.event_replay_v1.fixture_cli --fixture-only \
  --fixture research/event_replay_v1/fixtures/lifecycle.json \
  --receipt /tmp/event_replay_v1_receipt.json
```

`--real-input` is rejected. The CLI accepts only a fixture whose bytes match
`dependencies.lock.json`. The receipt records the authoritative base. The git
head is recorded outside the runtime, after the commit, because writing the
head into the tree would change the head.

## Unknowns that remain blocked

- Approved Stage A implementation pin and real adapter compatibility
- Measured spread, depth, and latency
- Venue fee verification
- Funding cashflows
- REALISTIC_V1 and live policy
- Historical venue-book proof for these manufactured depths, queues, and delays
- R57 actual permutation sweep
