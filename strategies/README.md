# Strategy packages

YAML packages live here as `strategies/<id>.yaml` and are validated against
`schema/strategy-package.schema.json` in CI.

**Nothing in this directory is activated for paper or live execution by
presence alone.** Each package still needs its own R22 retirement/add work and
an RK PASS before activation. The paper-bot loader hard-refuses packages that
are not `paper_only: true` or that contain credential keys. Only
`evidence.status: FWD_PASS` may feed the R53(c) gate.

This PR ships the schema + loader + dashboard auto-render plumbing with an
empty package set on purpose.
