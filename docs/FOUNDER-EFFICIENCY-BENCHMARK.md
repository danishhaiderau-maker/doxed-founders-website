# Founder efficiency benchmark

This benchmark compares the deterministic prompt sent by Founder IDE with the
same request before context compaction. It covers five named coding workloads:
gateway streaming, provider settings, remote-edit safety, dependency impact,
and Windows installer diagnosis.

The baseline is `same-request-full-context`: the identical messages and tool
results before Founder removes stale coordination and bounds retained command
output. Token counts use the versioned local four-characters-per-token estimate.
The benchmark runs each fixture five times and requires byte-for-byte identical
results across runs.

| Fixture | Full-context baseline | Sent | Avoided | Estimated reduction |
|--|--:|--:|--:|--:|
| Gateway stream debug | 9,086 | 4,051 | 5,035 | 55.41% |
| Provider profile settings | 6,093 | 4,053 | 2,040 | 33.48% |
| Remote edit safety | 10,589 | 4,051 | 6,538 | 61.74% |
| Workspace impact review | 4,591 | 4,053 | 538 | 11.72% |
| Installer release check | 13,594 | 4,055 | 9,539 | 70.17% |

These percentages describe only these fixed context fixtures. They are not a
general product savings claim.

This is evidence of reduced context sent. It is not evidence that answers are
better, that provider latency is lower, or that a marketing percentage applies
to every repository. Live DeepSeek receipts separately show provider-reported
cache hits, misses, and output tokens. The API separately shows an estimated,
input-only USD comparison using `deepseek-usd-2026-07-22` pricing.

Run the benchmark with the Founder IDE extension test suite. The test fails if
any fixture does not send fewer tokens than its full-context baseline or if the
five repeated runs differ.
