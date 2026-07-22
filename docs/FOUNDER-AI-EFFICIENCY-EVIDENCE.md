# Founder AI Efficiency Evidence

## Status

Stage 3 implements deterministic prompt compaction and estimate plumbing. It does not establish a public savings claim. The IDE and API label every pre-provider number as `estimated`.

## Implemented controls

- Stable system prefix: the Founder identity and tool contract precede dynamic memory, project, and coordination blocks.
- Stable tool order: tool definitions are sorted by name before every request.
- Local project map: the IDE sends ranked file and symbol metadata instead of whole repository contents.
- Incremental invalidation: unchanged project-map entries retain their hashes and symbols.
- Memory de-duplication: when the IDE has already included authenticated Founder memory, the API does not inject a second copy.
- Bounded tool results: output above 16,000 characters keeps the beginning, end, and a SHA-256 reference to the full local output.
- Coordination de-duplication: only the latest live-agent coordination snapshot is sent.
- Route cache truth: `routeCacheLevel` identifies the routing-decision cache. It is not described as a provider token-cache hit.

## Estimator

The local preflight estimator uses `ceil(characters / 4)` plus a small per-message allowance. It is useful for comparing the same prompt before and after deterministic compaction. It is not a tokenizer and cannot replace provider-reported usage.

The server accepts an estimate only when:

- all counts are finite, non-negative, and bounded;
- `sentTokens <= baselineTokens`;
- `avoidedTokens = baselineTokens - sentTokens`;
- the measurement label is exactly `estimated`.

The server calculates the percentage again instead of trusting the client value.

## Synthetic preflight

The automated suite runs 50 representative tool-task fixtures five times with identical inputs. It verifies deterministic output, preservation of tool-output evidence, and lower estimated prompt volume for oversized fixtures. This is 250 deterministic scenario runs, not 250 paid model completions.

Command:

```powershell
npm.cmd test --workspace=founder-ide-extension
```

## Live benchmark gate

Before any website, sales page, or dragon message states a savings percentage:

1. Select 50 to 100 real coding tasks across at least five repositories.
2. Run each task five times in baseline and Founder modes.
3. Pin the same provider, model, temperature, tool permissions, and starting commit.
4. Use the same pre-existing acceptance tests for both modes.
5. Record provider-reported input, cached-input, reasoning, and output tokens; cache status; latency; retries; cost; task result; and test result.
6. Mark agent-written tests separately from pre-existing tests.
7. Exclude failed tasks from neither side; failure cost is part of the result.
8. Publish the median, distribution, and raw anonymized receipts rather than the best run.

## Claim threshold

- Below 20% verified savings: do not lead with efficiency. Lead with coordination, remote control, local privacy, and BYOK.
- 20% to 34.99%: efficiency may be a supporting claim.
- 35% or more: efficiency may become a headline only when task success and latency are no worse than baseline.
- A 50% to 80% claim requires the live benchmark to demonstrate that range. It cannot be inferred from synthetic fixtures.

Provider caches are probabilistic. Founder can maximize hit probability with stable prefixes, while the receipt must distinguish an actual provider-reported cache hit from route-cache reuse and local estimated compaction.
