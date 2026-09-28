# Compressed Shadow and Missed Opportunity Proof Audit Contract

This experiment is research-only. Its release gate is closed unless every item
below is evidenced against one deployed revision and one collection epoch.

## Runtime isolation

- The only schedule is `0, 60, 120, 240, 420, 600` seconds with one terminal
  expiry at `780` seconds.
- Every state and receipt says `execution_class=SHADOW_ONLY`,
  `places_order=false`, and `relay_eligible=false`.
- Shadow state contains no quantity, exchange order ID, client order ID, relay
  intent, or callable exchange submission path.
- Before/after counts for paper orders, paper positions, exchange intents, live
  orders, and live positions must be identical when only the shadow poll runs.
- Existing active-paper chase timing, tile toggles, fill rules, exit policies,
  risk sizing, relay allowlists, and Continuous benchmark behavior must have an
  unchanged regression receipt.

## Lifecycle and identity

- Stage receipts occur exactly once for `0/60/120/240/420/600` and never use
  wall-clock polling count as schedule identity.
- Exactly one terminal receipt exists at or after 780 seconds. Repeated polls
  after terminal return no events and cannot append provisional rows.
- `trade_id`, shared AI-call ID, opportunity ID, episode ID, epoch ID, policy
  ID, and policy signature are non-empty and identical across every stage and
  terminal receipt.
- A restart/replay of the same identity deduplicates rather than creating a
  second terminal. Out-of-epoch and mixed-policy rows are rejected.

## Analyzer truth and provenance

- Input rows use `signed_compressed_shadow_schedule_v1`; unsigned, incomplete,
  mixed-identity, post-terminal provisional, or non-shadow rows are excluded
  with an explicit reason.
- `missed_opportunity_proof_report.json` uses
  `missed_opportunity_proof_v1`; `chase_policy_lab_report.json` uses
  `chase_policy_lab_v1`.
- Classification is exactly one of `PROVEN_MISSED_PROFIT`,
  `PROVEN_AVOIDED_LOSS`, `AMBIGUOUS`, or `INSUFFICIENT_EVIDENCE`.
- Both reports state `qualification_eligible=false`; shadow PnL never enters
  executed PnL, win rate, AI success/failure, policy qualification, or relay
  readiness.
- Empty evidence renders `SOURCE_EMPTY_OR_UNAVAILABLE`, not zero profit, zero
  loss, or a synthetic strategy leader.
- Each report carries source revision, analyzer revision, epoch, policy ID and
  signature, input file/hash or common capture-fence identity, accepted and
  excluded counts, and exclusion reasons.

## Dashboard and Download Everything

- Dashboard cards label the cohort Shadow/Counterfactual and show provenance,
  qualification ineligibility, sample size, exclusions, and evidence status.
- Download Everything includes the signed raw schedule plus both reports and
  declares each in its manifest with byte count and SHA-256.
- ZIP CRC, manifest membership, hashes, revision, epoch, policy signature, and
  common capture fence must validate offline. Missing raw evidence or either
  derived report makes the bundle incomplete for this experiment.
