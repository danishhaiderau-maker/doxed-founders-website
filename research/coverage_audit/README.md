# Synthetic coverage auditor

Timestamp and schema auditor for declared synthetic fixtures. A `COMPLETE`
result means every required check in that fixture was proven. It is not real
market coverage, not an accepted two-month window, and not a research finding.

Real venue cadence, publication delay, and book-delay facts are not invented.
The registry entry `unverified/placeholder/unspecified` stays `UNVERIFIED`.
Accepted real dates are null.

## Scope

These eighteen files are the whole package. Nothing outside
`research/coverage_audit/` is an input to this tool. The isolated lock does not
install packages and must not be copied into a repository-root requirements
file.

## Run the fixtures

From the repository root, with Python 3.11 or newer and no extra installs:

```
python -m unittest \
  research.coverage_audit.tests.test_synthetic_formats \
  research.coverage_audit.tests.test_cadence_semantics \
  research.coverage_audit.tests.test_redaction_and_guards \
  research.coverage_audit.tests.test_limits_and_integrity \
  research.coverage_audit.tests.test_synthetic_pipeline
```

Launcher shape, if a caller already holds bytes:

```
python -m research.coverage_audit.launch_audit \
  --registry research/coverage_audit/source_registry.json \
  --allowlist <explicit-allowlist.json> \
  --allowlist-sha256 <sha256-of-canonical-allowlist> \
  --base-commit <40-hex> \
  --clock-unix-ms <int> \
  --seed <int> \
  --resource-profile tiny-synthetic-v1
```

Arguments are explicit. The process environment is not read. The only CLI
profile id is `tiny-synthetic-v1`. Input paths inside an allowlist are rejected.
The auditor does not discover files, open sockets, write source bytes, or stage
temporary copies.

Exit codes: `0` COMPLETE, `3` UNKNOWN, `4` INCOMPLETE_FAIL, `2` when the
interpreter is older than 3.11.

## Stdlib startup effects

The Python interpreter may read environment variables and locale data during its own startup, before this package runs. This application does not read environment variables, does not enumerate their names, and does not use them as configuration or data. Importing the package loads stdlib modules `json`,
`hashlib`, `re`, `datetime`, `time`, `resource`, and, through the parser,
`zipfile` and `zlib`. `zipfile` imports `os`. Application code does not access
`os.environ`. `resource.getrusage` and `time.monotonic` are CPU and wall meters
only.

## Tiny synthetic resource profile

`tiny-synthetic-v1` is a test bound, not an estimate of a real audit:

| Bound | Value |
| --- | ---: |
| memory_bytes | 262144 |
| cpu_ms | 10000 |
| wall_ms | 10000 |
| output_bytes | 65536 |
| max_gaps | 16 |
| max_record_bytes | 2048 |
| max_decompressed_bytes | 65536 |
| max_input_bytes | 32768 |
| max_archive_members | 8 |
| max_duplicate_keys | 64 |
| max_rows | 128 |
| base_memory_bytes | 2048 |
| row_memory_bytes | 48 |
| dup_memory_bytes | 64 |
| day_memory_bytes | 32 |
| gap_memory_bytes | 128 |
| member_memory_bytes | 40 |

Crossing a bound returns `INCOMPLETE_FAIL` and a reason code. It does not
truncate a result into `COMPLETE`. Test-only profiles may set lower bounds, or
the same bounds at the exact edge, and cannot exceed the ceilings in
`guards.py`. The CLI cannot select those profiles.

Deterministic fixture receipts use an injected meter. A process meter checks
live CPU and wall time; those observed numbers vary by host and are not a
completeness claim.

## Behavior that is fixed

- ZIP members, including directories, are read in memory. Names with `..` or
  absolute paths are rejected and never extracted.
- Gzip members are read until the byte stream ends. A valid first member does
  not hide a corrupt or trailing remainder.
- CSV and headerless rows use the registry field list. Quoted commas, quotes,
  and newlines stay inside the field and are discarded when the field is not a
  timestamp or sequence.
- JSON and JSONL keep timestamp, sequence, type, and depth-count information.
  Other tokens are dropped.
- Sparse trades do not become outages. Candle gaps need a trade cross-check
  from the same run. Sequence holes are reported only for a per-instrument
  contiguous contract.
- Proven gaps carry UTC start, UTC end, rule, and numeric evidence. Anything
  unproven is `UNKNOWN`.
- Reports expose schema fingerprints, not prices, sizes, or raw rows.
- Allowlist and input hashes are checked before parsing and again after read.
  A mismatch fails closed.

## Limitations

No real files are opened by the fixture suite. Host, execution route, code
delivery, and aggregate report destination are a later card. Filename ranges
and timestamp bounds are not cadence proof. Depth band counts are not
executable book levels.
