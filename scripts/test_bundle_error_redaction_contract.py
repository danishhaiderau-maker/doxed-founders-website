"""Direct contract check for root bundle-client terminal error redaction.

This intentionally avoids pytest because the desktop recovery environment ships
with a minimal Python runtime.  It exercises the executable ``main`` boundary,
not just the source text.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path


SCRIPT = Path(__file__).with_name("fly-sync-bundle-client.py")
SPEC = importlib.util.spec_from_file_location("root_bundle_client_redaction", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _run_with(error: Exception) -> dict:
    old_stdin, old_run = MODULE.sys.stdin, MODULE.run
    output = io.StringIO()
    try:
        MODULE.sys.stdin = io.TextIOWrapper(io.BytesIO(b"{}"), encoding="utf-8")

        def fail(*_args, **_kwargs):
            raise error

        MODULE.run = fail
        with contextlib.redirect_stdout(output):
            assert MODULE.main() == 1
    finally:
        MODULE.sys.stdin, MODULE.run = old_stdin, old_run
    lines = [line for line in output.getvalue().splitlines() if line]
    assert len(lines) == 1
    return json.loads(lines[0])


def main() -> None:
    hostile = "CUSTOMER_SECRET_SHOULD_NEVER_APPEAR_6E8D2E"
    generic = _run_with(ValueError(hostile))
    assert generic == {
        "schema": "fly_bundle_staging_receipt_v1",
        "status": "FAILED",
        "error": "BUNDLE_CLIENT_FAILED",
    }
    assert hostile not in json.dumps(generic)

    pressure = _run_with(MODULE.IndexPressureError({
        "generation_id": "a" * 64,
        "phase": "INDEX",
        "attempts": 2,
        "http_status": 503,
        "transport_error": None,
    }))
    assert pressure["error"] == "BUNDLE_INDEX_PRESSURE_CIRCUIT_OPEN"
    assert pressure["index_diagnostic"] == {
        "generation_id": "a" * 64,
        "phase": "INDEX",
        "attempts": 2,
        "http_status": 503,
        "transport_error": None,
    }
    print("ROOT_BUNDLE_ERROR_REDACTION_CONTRACT_OK")


if __name__ == "__main__":
    main()
