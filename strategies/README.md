# Strategy packages

YAML packages live here as `strategies/<id>.yaml` and are validated against
`schema/strategy-package.schema.json` in CI.

**Nothing in this directory is activated for paper or live execution by
presence alone.** Each package still needs its own R22 and an RK PASS before
activation. The paper-bot loader hard-refuses packages that are not
`paper_only: true`, that contain credential keys, or (for `OOS_PASS` /
`FWD_PASS`) that lack 22m label `HOLDS` / have `leak_suspect` other than
`false`. Only `evidence.status: FWD_PASS` (with HOLDS + `leak_suspect: false`)
may feed the R53(c) gate.

**Numeric bars gate:** see `NUMERIC-BARS-FOLLOWUP.md`. While
`NUMERIC_BARS_FOLLOWUP_REQUIRED` is true, **any** `strategies/*.yaml` is
refused. This PR ships an empty package set on purpose.

**Laptop deps:** PyYAML + jsonschema (in `services/btc-conservative-agent/requirements.txt`).
Missing deps → dashboard shows **no package tiles** (soft fallback); install via
`pip install -r services/btc-conservative-agent/requirements.txt` before start.
