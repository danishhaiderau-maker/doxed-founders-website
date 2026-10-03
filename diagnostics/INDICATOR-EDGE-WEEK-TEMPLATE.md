# Indicator Edge - week {{week}} ({{period}})

Generated {{generated_at}} by `research/indicator_edge_weekly.py` from `indicator_edge_report.json`.
Observation only: nothing here changes a tile, an order, the relay or Bitfinex.

## In plain English

{{plain_english}}

## Pre-registration

| Item | Value |
|------|-------|
| Pre-registration id | `{{prereg_id}}` |
| Freeze line hash | `{{line_sha}}` |
| Frozen at | {{frozen_at}} |
| Feature set | `{{feature_set_version}}` (`{{feature_set_sha}}`) |
| Trials (FDR family) | {{trial_count}} |
| Scored days / eligible bars | {{scored_days}} / {{eligible_rows}} |
| Labels | {{label_counts}} |

## Indicator availability (latest bar)

{{availability}}

## Full ranking (best window per feature)

Net bp is the mean mid move in the signal direction minus a 2 bp round trip, entering 2 s after the decision;
"9 s" repeats it entering 9 s late. q is the Benjamini-Hochberg adjusted p over every feature x window trial.

{{ranking}}

## Correlation clusters (|rho| > 0.7)

{{clusters}}

## Combinations

{{combinations}}

## Daily summaries this week

{{daily}}
