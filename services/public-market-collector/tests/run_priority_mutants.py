"""Run focused priority mutants in disposable copies, never the checkout.

Usage: python3 -B tests/run_priority_mutants.py /tmp/pmc-priority-mutants
The control must pass; every mutant must fail with an assertion, not an import
error or timeout. These are offline ctypes/plumbing checks, not Win32 execution.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO = PACKAGE_ROOT.parents[1]
WS = "public_market_collector/ws_public.py"
DAILY = "public_market_collector/daily.py"
MUTANTS = [
    ("get_current_process_argtypes", WS, "    kernel.GetCurrentProcess.argtypes = []\n", ""),
    ("get_current_process_restype", WS, "    kernel.GetCurrentProcess.restype = wintypes.HANDLE\n", ""),
    ("set_priority_class_argtypes", WS,
     "    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]\n", ""),
    ("set_priority_class_restype", WS, "    kernel.SetPriorityClass.restype = wintypes.BOOL\n", ""),
    ("use_last_error", WS, 'ctypes.WinDLL("kernel32", use_last_error=True)', 'ctypes.WinDLL("kernel32")'),
    ("numeric_error_reason", WS, 'return f"priority_denied:winerror={error}"', 'return "priority_denied"'),
    ("daily_reason_exposure", DAILY,
     '        except StartRefused as exc:\n            sys.stderr.write(f"REFUSED daily: {exc.reason}\\n")\n            return 78\n',
     '        except StartRefused:\n            return 78\n'),
]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("pass one log directory outside the checkout")
    logs = Path(sys.argv[1]).resolve()
    if logs == REPO or REPO in logs.parents:
        raise SystemExit("log directory must be outside the checkout")
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for label, relative, before, after in [("control", "", "", "")] + MUTANTS:
        with tempfile.TemporaryDirectory(prefix="pmc-priority-mutant-") as name:
            root = Path(name)
            shutil.copytree(PACKAGE_ROOT / "public_market_collector", root / "public_market_collector")
            (root / "tests").mkdir()
            shutil.copy2(PACKAGE_ROOT / "tests/test_windows_priority.py", root / "tests/test_windows_priority.py")
            if relative:
                path = root / relative
                source = path.read_text(encoding="utf-8")
                if source.count(before) != 1:
                    raise AssertionError(f"{label}: mutation target is not unique")
                path.write_text(source.replace(before, after), encoding="utf-8")
            start = time.monotonic()
            run = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
                 "-p", "test_windows_priority.py", "-v"],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=30, check=False,
            )
            elapsed = time.monotonic() - start
            log = logs / f"{label}.log"
            log.write_text(run.stdout, encoding="utf-8")
            # Require the complete eight-test suite. An error is not a kill.
            complete = "Ran 8 tests in " in run.stdout
            errors = "ERROR:" in run.stdout or "errors=" in run.stdout
            passed = complete and run.returncode == 0 and run.stdout.rstrip().endswith("OK")
            killed = complete and run.returncode == 1 and "FAILED (failures=" in run.stdout and not errors
            valid = passed if label == "control" else killed
            result = {"label": label, "status": ("PASS" if passed else "KILLED") if valid else "INVALID",
                      "returncode": run.returncode, "duration_s": round(elapsed, 6),
                      "log": str(log), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
            results.append(result)
            print(f"{label}: {result['status']} ({elapsed:.3f}s)", flush=True)
            if not valid:
                raise AssertionError(f"{label}: see {log}")
    summary = {"focused_test_count": 8, "mutants": len(MUTANTS), "killed": len(MUTANTS),
               "survived": 0, "invalid": 0, "results": results}
    path = logs / "summary.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"SUMMARY={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
