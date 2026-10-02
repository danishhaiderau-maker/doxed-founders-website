"""Direct no-pytest contract for bounded bundle-transfer failure output."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CLIENT_PATH = ROOT / "fly-sync-bundle-client.py"
POWERSHELL_PATH = ROOT / "fly-sync-bundles.ps1"


def _load_client():
    spec = importlib.util.spec_from_file_location("bundle_redaction_client", CLIENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_client_failure_never_prints_exception_text() -> None:
    client = _load_client()
    secret = "SENSITIVE_TEST_VALUE_SHOULD_NOT_APPEAR"

    def fail(*_args, **_kwargs):
        raise RuntimeError(secret)

    client.run = fail
    original_stdin = sys.stdin
    stdout, stderr = io.StringIO(), io.StringIO()

    class FakeStdin:
        buffer = io.BytesIO(b"{}")

    try:
        sys.stdin = FakeStdin()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            assert client.main() == 1
    finally:
        sys.stdin = original_stdin

    receipt = json.loads(stdout.getvalue())
    assert receipt == {
        "schema": "fly_bundle_staging_receipt_v1",
        "status": "FAILED",
        "error": "BUNDLE_CLIENT_FAILED",
    }
    assert secret not in stdout.getvalue()
    assert secret not in stderr.getvalue()
    assert stderr.getvalue() == "BUNDLE_CLIENT_DIAGNOSTIC_REDACTED\n"


def test_powershell_wrapper_never_forwards_raw_exception_or_stderr() -> None:
    source = POWERSHELL_PATH.read_text(encoding="utf-8")
    assert "bundle_failure=' + $failureCode" in source
    assert "bundle_exception=' + $_.Exception.Message" not in source
    assert "bundle_stderr=' + $errText" not in source
    assert "$null = $stderr.Result" in source


if __name__ == "__main__":
    test_client_failure_never_prints_exception_text()
    test_powershell_wrapper_never_forwards_raw_exception_or_stderr()
    print("Bundle error redaction contracts passed")
