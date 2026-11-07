"""Fixture-only entry.

A direct invocation is the parent watchdog: it installs its own 256MiB
virtual ceiling, spawns one child, and is the only receipt writer. The child
is `--cli-child` with an empty environment and no filesystem writes. The
suite calls `run_inprocess` instead of nesting another process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

VAS_BYTES = 268435456
COMMAND_SECONDS = 120
RECEIPT_CAP = 8 * 1024 * 1024
AGGREGATE_CAP = 16 * 1024 * 1024
PACKAGE = Path(__file__).resolve().parent
LOCK_PATH = PACKAGE / "dependencies.lock.json"
FIXTURE_KEY = "research/event_replay_v1/fixtures/lifecycle.json"


def _fail(code: str) -> int:
    print(code, file=sys.stderr)
    return 3


def _install_vas() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (VAS_BYTES, VAS_BYTES))


def _parse_child(argv: list[str]):
    if "--real-input" in argv or "--live" in argv or "--network" in argv:
        raise RuntimeError("REAL_INPUT_MODE_REJECTED")
    if "--fixture-only" not in argv:
        raise RuntimeError("FIXTURE_ONLY_REQUIRED")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--cli-child", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    return parser.parse_args(argv)


def _execute_parsed(args) -> dict:
    """Replay after arguments exist. The child installs containment before this import."""

    from .engine import replay
    from .guards import DEFAULT_LIMITS, CapStop, require_vas_ceiling

    require_vas_ceiling()
    raw = Path(args.fixture).read_bytes()
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    pinned = lock["files"][FIXTURE_KEY]
    if len(raw) > int(pinned["bytes"]) + 1 or hashlib.sha256(raw).hexdigest() != pinned["sha256"]:
        raise RuntimeError("FIXTURE_PIN_MISMATCH")
    fixture = json.loads(raw.decode("utf-8"))
    try:
        result = replay(fixture, workers=args.workers, limits=DEFAULT_LIMITS)
    except CapStop as exc:
        return {"status": "CAP_STOP", "code": str(exc)}
    return {
        "status": "OK",
        "compatibility": "BLOCKED",
        "authoritative_base": lock["authoritative_base"],
        "live": "OFF",
        "result": result,
    }


def run_inprocess(argv: list[str]) -> dict:
    """Child logic with no filesystem write and no further process spawn."""

    return _execute_parsed(_parse_child(argv))


def parent_main(argv: list[str]) -> int:
    sys.dont_write_bytecode = True
    _install_vas()
    from .guards import (
        MAX_AGGREGATE_BYTES,
        MAX_RECEIPT_BYTES,
        CapStop,
        OutputLedger,
        ProcessGate,
        ChunkPump,
        FramePump,
        emit_receipt,
        install_entry_containment,
    )

    if "--real-input" in argv or "--live" in argv:
        return _fail("REAL_INPUT_MODE_REJECTED")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--head", default=None)
    parser.add_argument("--base", default="40087a65f2157f8c6701ea3670adc5c29a28c87d")
    parser.add_argument("--clean-tree", default=None)
    args = parser.parse_args(argv)
    receipt = Path(args.receipt)
    allowed_root = Path("/opt/cursor/artifacts").resolve()
    if not str(receipt.resolve()).startswith(str(allowed_root) + "/"):
        return _fail("PROHIBITED_REAL_ROOT")
    if not receipt.parent.is_dir():
        return _fail("PROHIBITED_REAL_ROOT")
    install_entry_containment((str(receipt.resolve()),))
    gate = ProcessGate()
    gate.acquire()
    ledger = OutputLedger(aggregate_cap=MAX_AGGREGATE_BYTES, receipt_cap=MAX_RECEIPT_BYTES)
    started = time.monotonic()
    repo = str(PACKAGE.parents[1])
    bootstrap = (
        "import sys\n"
        "sys.dont_write_bytecode = True\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from research.event_replay_v1.fixture_cli import main\n"
        "raise SystemExit(main(sys.argv[2:]))\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", bootstrap, repo, "--cli-child", "--fixture-only", "--fixture", args.fixture],
        cwd=repo,
        env={},
        preexec_fn=_install_vas,
        start_new_session=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.stdin is not None:
        proc.stdin.close()
    deadline = COMMAND_SECONDS
    overshoot = 0.0
    stopped = False
    out_pump = FramePump(proc.stdout.fileno(), ledger) if proc.stdout is not None else None
    err_pump = ChunkPump(proc.stderr.fileno(), ledger) if proc.stderr is not None else None
    try:
        while True:
            if proc.poll() is not None and (out_pump is None or out_pump.eof) and (err_pump is None or err_pump.eof):
                break
            if time.monotonic() - started > deadline and not stopped:
                kill_at = time.monotonic()
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                stopped = True
                overshoot = time.monotonic() - kill_at
            if out_pump is not None and not out_pump.eof:
                out_pump.pull()
            if err_pump is not None and not err_pump.eof:
                err_pump.pull()
            if stopped and (out_pump is None or out_pump.eof) and (err_pump is None or err_pump.eof):
                break
            if (out_pump is None or not out_pump.progress) and (err_pump is None or not err_pump.progress):
                time.sleep(0.01)
        stdout = out_pump.frames if out_pump is not None else []
        stderr = err_pump.value() if err_pump is not None else b""
    except CapStop as exc:
        gate.release()
        print(f"CAP_STOP:{exc.code}", file=sys.stderr)
        return 2
    finally:
        gate.release()
    if stopped:
        print("CAP_STOP:command", file=sys.stderr)
        return 2
    if not stdout:
        print("CLI_NO_FRAME", file=sys.stderr)
        return 3
    payload = json.loads(stdout[-1].decode("utf-8"))
    body = {
        "kind": "event_replay_v1.synthetic_receipt",
        "compatibility": "BLOCKED",
        "authoritative_base": args.base,
        "head": args.head,
        "head_source": "EXTERNAL_ARGV_NOT_SELF_HASH",
        "clean_tree": args.clean_tree,
        "command": "fixture_cli --fixture-only",
        "interpreter": sys.executable,
        "runtime": sys.version.split()[0],
        "external_lock_sha256": hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest(),
        "wall": {"elapsed": time.monotonic() - started, "overshoot": overshoot, "mechanism": "parent_monotonic_killpg"},
        "aggregate_bytes": ledger.aggregate_used,
        "stderr_bytes": len(stderr),
        "child": payload,
    }
    try:
        with receipt.open("wb") as handle:
            writer = emit_receipt(ledger, handle, body, sort_keys=True)
        receipt_bytes = writer.ledger.receipt_used
    except CapStop as exc:
        print(f"CAP_STOP:{exc.code}", file=sys.stderr)
        return 2
    print(json.dumps({"status": payload.get("status", "OK"), "compatibility": "BLOCKED", "receipt_bytes": receipt_bytes}))
    return 0 if payload.get("status") == "OK" else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--cli-child" in argv:
        sys.dont_write_bytecode = True
        from .guards import GuardError, install_entry_containment, install_env_trap

        _install_vas()
        try:
            # Parse before the env trap. argparse reads locale data during startup.
            parsed = _parse_child(argv)
            install_entry_containment(())
            install_env_trap()
            payload = _execute_parsed(parsed)
        except (RuntimeError, GuardError) as exc:
            payload = {"status": "ERROR", "code": str(exc)}
        data = json.dumps(payload).encode("utf-8")
        sys.stdout.buffer.write(len(data).to_bytes(4, "big") + data)
        sys.stdout.buffer.flush()
        return 0 if payload.get("status") == "OK" else 2
    if "--fixture-only" not in argv:
        return _fail("FIXTURE_ONLY_REQUIRED")
    return parent_main(argv)


if __name__ == "__main__":
    sys.exit(main())
