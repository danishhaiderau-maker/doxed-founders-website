# Score-led paper experiment

`SCORE_LED_PAPER_RESEARCH_ENABLED=1` is an explicit startup configuration.
It defaults OFF. It changes admission for the existing five registry families,
not their entry offsets, quantities, chase, capacity, market checks or exits.
Enabled tile toggles remain required for paper orders. No extra sixth tile.

Finite numeric LONG/SHORT scores in 0..100 with a strict higher side choose
that side, even when the original AI says REJECT or NO_TRADE. Ties, missing or
malformed values and AI errors remain explicit no-order results. Inversion is
not supported for this treatment: fail closed rather than silently choose the
opposite side. The runtime requires force-paper and explicitly disarmed live
state, and registry relay-ineligibility, before admission and again at child
execution.

This is an admission experiment, not an AI approval or proven profitable
policy. Each child carries the complete unchanged `original_ai_snapshot`,
original raw verdict, and `admission_is_ai_approval=false`. Only the child
execution compatibility projection says APPROVE. Shared AI history and
benchmark filtering are unchanged. Decision ledger includes treatment and
original snapshot; paper signals retain the child AI output containing both.

The registry prefixes raw policy IDs with `SCORE_LED_PAPER_V1::`, yielding new
signatures, policy epochs, analyzer cohorts and manifest registry signature.
Historical outcomes are not relabeled. Existing hypothesis profitability
labels are replaced with UNTESTED_NEW_ADMISSION_TREATMENT. Mirror/analyzer
processes must use the same startup flag and verify registry identity. A
startup flag mismatch must never be resolved by relabeling historical rows.

Activation is an operational step, not performed by these source changes.
Before activation: generated-engine parity, matching analyzer configuration,
visual QA, safe guarded restart/deploy and fresh epoch/cohort identity checks.
After activation: inspect actual original REJECT + child SHORT 35>20 receipt,
paper limit intent and lifecycle, no live eligibility, matching analyzer data.
No orders guaranteed: downstream safety, toggles, capacity, depth and fill
constraints still apply. Source tests are not deployment or live-trade proof.
