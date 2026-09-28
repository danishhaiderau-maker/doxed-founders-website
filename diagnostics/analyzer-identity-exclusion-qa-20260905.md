# Runtime revision incident exclusion: source QA

The guarded deployment incident is retained in its original OPEN receipt and a
separate CLOSED observation receipt. No raw ledger was rewritten. The closure
pin is `ad20a36beea2f326c44287ab8a1753b81f9d4958b83299609eb08f9ee9a0813a`.

The local analyzer now accepts an explicit path/hash input and applies linked
episode/opportunity exclusions to baseline replay, conservative evidence, genome,
dynamic training/holdout, legacy optimizer eligibility and qualified exit replay.
Unresolved timing remains UNKNOWN; unrelated unassociated legacy rows are counted
but do not invent associations with every clean future episode. Descriptive
statistics remain available. Filtering a sealed cohort requires resealing.
Publication checks bind the receipt digest before/after staging and preserve the
disabled-input fingerprint behavior.

Root independently ran the combined incident, baseline, evaluator, publication,
discovery, dynamic, provenance, policy-cycle, genome and exit-grid suite:
**205 passed in 12.09 seconds**. The first root invocation failed collection
because the inherited BTC_AGENT_DATA_DIR selected a different store; removing
that variable in the test process used the test suite's canonical defaults.
No test failure was relabeled as a pass. Diff checks passed (Git emitted normal
LF-to-CRLF conversion notices).

Activation is NOT claimed. Required next step is a new atomic publication with:

- BTC_ANALYZER_IDENTITY_INCIDENT_RECEIPT pointing to the CLOSED receipt.
- BTC_ANALYZER_IDENTITY_INCIDENT_SHA256 matching the digest above.
- A current verified mirror and matching report provenance.

Already published dashboard generations do not gain protection retroactively;
their freshness and serving behavior remain part of visual/runtime QA. This
repair does not make the strategy or Bitfinex ready.
