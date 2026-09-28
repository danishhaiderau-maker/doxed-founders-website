# Declared shadow execution economics: source acceptance, not activation

The optional `declared_shadow_model_v1` uses an explicit checksum-pinned contract
bound to a generation. Fees follow actual simulated entry and partial/terminal
exit notionals. Funding is explicitly ZERO_SCENARIO or a declared constant
entry-notional rate over FIFO quantity-time. It is not measured venue funding.
Baseline timing is preserved; missing ATR, sizing, quantity constraints or timing
provenance stays UNKNOWN. No current defaults are relabeled historical evidence.

Root independently ran the model, shadow report, terminal, discovery manifest and
discovery cohort suites: **82 passed in 4.36 seconds**. An earlier command named a
nonexistent test file and ran no tests; it was corrected using the actual file
inventory. The agent's reported overlapping suite is not added to root coverage.

Regression includes 1,000 x 11 x 21,070 = 231,770,000 unsupported combinations:
exact arithmetic counts, at most100 diagnostic rows, no Cartesian context
allocation or terminal evaluation. Real evaluated results can stream to a caller
sink. Truncation remains explicitly incomplete and not profitability-supported
until a verified full-stream consumer is bound. This deliberately changes large
v1 report materialization; only its existing cost calculations are compatible.

Decimal reconciliation rejects genuine missing quantities down to1e-18 while
signed policy fractions reconstruct valid partials without binary-float artifacts.
Measured fees, funding and acknowledgement latency remain unavailable in scenario
results; no live or OOS qualification is promoted.

Remaining end-to-end work:

1. Project verified baseline execution context before hashing baseline receipts;
   do not manufacture alternate-fill ATR or overwrite original quantities.
2. Wire the explicit contract loader into normal atomic publication and bind its
   digest to provenance. There is no automatic default model activation.
3. Atomically publish the complete result stream and verify its discovery reader;
   a bounded display sample is not the entire strategy matrix.
4. Run against the current fully acknowledged mirror and visually QA assumptions,
   exact coverage and strategy tiers. None of these runtime results is claimed here.
