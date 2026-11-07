# Stage A causal grammar v1

Synthetic fixture package for engineering clear `20261107-1945-STAGE-A`.
It normalizes manufactured envelopes and compiles one explicit finite fixture.
It does not read real inputs.

Producer spec references, not implementation pins:

- auditor CoverageReport spec ref `3272c6425b59`
- collector spec refs `1439354678ef` and `7bee784c7e13`

Compatibility is **BLOCKED**. Stubs are **FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED**.
This package does not import auditor, collector, or dashboard bytes.

Temporal real enumeration is **NOT IMPLEMENTED / NOT TESTED / outside posted N**.
`research_n`, dates, maximum hold, and cost threshold are null.
There is no profitability evidence, no accepted finding, and the mission is not done.

The fixture grammar is exactly 13 synthetic strategies:

- singles `S:A` `S:B` `S:C`
- unordered pairs `U:A+B` `U:A+C` `U:B+C`
- unordered triple `U:A+B+C`
- ordered pairs `O:A>B` `O:B>A` `O:A>C` `O:C>A` `O:B>C` `O:C>B`

Ordered-pair lag, expiry, reset, ties, consumption, and retrigger values are fixture choices, not approved real policies. Commutative members are sorted. Ordered pairs keep direction.

Caps, enforced rather than forecast: 2 CPU workers, 512MiB peak RSS, 120 seconds per command, 600 seconds for the suite, 8MiB receipts, 2048 manufactured events, 256 fixture definitions. Exhaustion raises `INCOMPLETE` and does not truncate.

Authorized base pin: `40087a65f2157f8c6701ea3670adc5c29a28c87d`.

Run from the repository root, writing the receipt outside the repository:

```bash
python -m research.causal_grammar_v1.fixture_cli --fixture-only \
  --spec research/causal_grammar_v1/fixtures/tiny-spec.json \
  --events research/causal_grammar_v1/fixtures/committed-events.json \
  --receipt /tmp/causal-grammar-receipt.json \
  --pin-base 40087a65f2157f8c6701ea3670adc5c29a28c87d \
  --pin-head <40-lowercase-hex> \
  --workers 1
```

```bash
python research/causal_grammar_v1/tests/test_acceptance.py
```

Dependencies are the Python 3.12 standard library recorded in `dependencies.lock.json`. No third-party packages, network, environment, or vault access.
