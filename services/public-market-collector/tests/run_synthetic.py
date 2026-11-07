"""Run the synthetic suite and bind the log outside the checkout.

Usage: python3 tests/run_synthetic.py /tmp/collector-988-corrective
The log directory must sit outside this repository. The harness does not
open a socket and does not read credentials.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_synthetic


class _Recording(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append((test.id(), "PASS"))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append((test.id(), "FAIL"))

    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append((test.id(), "ERROR"))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.rows.append((test.id(), "SKIP"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _loaded_pins() -> list[str]:
    rows = [f"python={platform.python_version()}", f"implementation={platform.python_implementation()}"]
    seen = set()
    harnesses = [Path(test_synthetic.__file__).resolve(), Path(__file__).resolve()]
    for path in harnesses:
        seen.add(str(path))
        rows.append(f"{_sha256(path)}  {path}")
    for name, module in sorted(sys.modules.items()):
        if not name.startswith("public_market_collector"):
            continue
        file_name = getattr(module, "__file__", None)
        if not file_name or file_name in seen:
            continue
        path = Path(file_name).resolve()
        if not path.is_file() or "__pycache__" in path.parts or str(path) in seen:
            continue
        seen.add(str(path))
        rows.append(f"{_sha256(path)}  {path}")
    return rows


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("REFUSED: pass one log directory outside the checkout", file=sys.stderr)
        return 2
    log_dir = Path(args[0]).resolve()
    if log_dir == REPO or REPO in log_dir.parents:
        print("REFUSED: log directory is inside the checkout", file=sys.stderr)
        return 2
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "synthetic-test.log"
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_synthetic)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"python={platform.python_version()}\n")
        handle.write(f"executable={sys.executable}\n")
        runner = unittest.TextTestRunner(verbosity=2, stream=handle, resultclass=_Recording)
        result = runner.run(suite)
        handle.write("TEST_IDS\n")
        for test_id, status in result.rows:
            handle.write(f"{status} {test_id}\n")
        handle.write("KILLED_MUTATIONS\n")
        for line in test_synthetic.KILLED_LINES:
            handle.write(line + "\n")
        handle.write("LOADED_PINS\n")
        for line in _loaded_pins():
            handle.write(line + "\n")
    pins_path = log_dir / "loaded-pins.txt"
    pins_path.write_text("\n".join(_loaded_pins()) + "\n", encoding="utf-8")
    killed_path = log_dir / "killed-mutations.txt"
    killed_path.write_text("\n".join(test_synthetic.KILLED_LINES) + "\n", encoding="utf-8")
    matrix_path = log_dir / "fault-matrix.json"
    matrix_path.write_text(
        json.dumps(test_synthetic.FAULT_MATRIX, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"LOG={log_path}")
    print(f"TESTS={len(result.rows)} FAILURES={len(result.failures)} ERRORS={len(result.errors)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
