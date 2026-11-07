"""Pinned launcher for the synthetic coverage auditor.

Invocation, from the repository root:

    python -m research.coverage_audit.launch_audit \\
        --registry research/coverage_audit/source_registry.json \\
        --allowlist <explicit-allowlist.json> \\
        --allowlist-sha256 <sha256> \\
        --base-commit <40-hex> \\
        --clock-unix-ms <int> \\
        --seed <int> \\
        --resource-profile tiny-synthetic-v1

The process environment is not read. The only accepted resource profile is
tiny-synthetic-v1. Input bytes are supplied by the caller of main(); this
launcher does not discover files, open sockets, or write a report file.
"""

from __future__ import annotations

import sys

from research.coverage_audit.auditor import run_audit
from research.coverage_audit.cli import main as cli_main
from research.coverage_audit.guards import TINY_SYNTHETIC_PROFILE

PINNED_PROFILE_ID = "tiny-synthetic-v1"
PINNED_PYTHON = (3, 11)


def main(argv, *, version=None, **kwargs) -> tuple[int, dict]:
    current = sys.version_info if version is None else version
    if tuple(current[:2]) < PINNED_PYTHON:
        report = run_audit(
            registry_bytes=b"",
            allowlist={},
            allowlist_sha256="",
            sources={},
            base_commit="0" * 40,
            clock_unix_ms=0,
            seed=0,
            meter=kwargs.get("meter"),
        )
        report["reasons"] = sorted(set(report["reasons"]) | {"PYTHON_VERSION_UNSUPPORTED"})
        report["status"] = "INCOMPLETE_FAIL"
        return 2, report
    if TINY_SYNTHETIC_PROFILE["profile_id"] != PINNED_PROFILE_ID:
        raise RuntimeError("PIN_MISMATCH")
    return cli_main(argv, **kwargs)


if __name__ == "__main__":
    code, _report = main(sys.argv[1:])
    raise SystemExit(code)
