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


def run_inprocess(argv: list[str]) -> dict:
    """Child logic with no filesystem write and no further process spawn."""

    from .engine import replay
    from .guards import DEFAULT_LIMITS, CapStop, require_vas_ceiling

    if "--real-input" in argv or "--live" in argv or "--network" in argv:
        raise RuntimeError("REAL_INPUT_MODE_REJECTED")
    if "--fixture-only" not in argv:
        raise RuntimeError("FIXTURE_ONLY_REQUIRED")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--cli-child", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
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


def _read_bounded(stream, ledger) -> bytes:
    chunks = []
    while True:
        ledger.reserve_pipe(4096, "stderr_or_plain")
        chunk = stream.read(4096)
        if not chunk:
            ledger.refund(4096)
            break
        if len(chunk) < 4096:
            ledger.refund(4096 - len(chunk))
        chunks.append(chunk)
    return b"".join(chunks)


def _read_frames(stream, ledger) -> list[bytes]:
    frames = []
    while True:
        ledger.reserve_pipe(4, "frame_header")
        header = stream.read(4)
        if not header:
            ledger.refund(4)
            break
        if len(header) < 4:
            ledger.refund(4 - len(header))
            raise RuntimeError("CAP_STOP:aggregate")
        size = int.from_bytes(header, "big")
        if size > ledger.remaining_aggregate():
            raise RuntimeError("CAP_STOP:aggregate")
        ledger.reserve_pipe(size, "frame_body")
        body = stream.read(size)
        if len(body) < size:
            ledger.refund(size - len(body))
            raise RuntimeError("CAP_STOP:aggregate")
        frames.append(body)
    return frames


def parent_main(argv: list[str]) -> int:
    _install_vas()
    from .guards import MAX_AGGREGATE_BYTES, MAX_RECEIPT_BYTES, OutputLedger, ProcessGate

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
    gate = ProcessGate()
    gate.acquire()
    ledger = OutputLedger(aggregate_cap=MAX_AGGREGATE_BYTES, receipt_cap=MAX_RECEIPT_BYTES)
    started = time.monotonic()
    repo = str(PACKAGE.parents[1])
    bootstrap = (
        "import sys\n"
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = COMMAND_SECONDS
    overshoot = 0.0
    stopped = False
    try:
        while proc.poll() is None:
            if time.monotonic() - started > deadline:
                kill_at = time.monotonic()
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                stopped = True
                overshoot = time.monotonic() - kill_at
                break
            time.sleep(0.01)
        stdout = _read_frames(proc.stdout, ledger) if proc.stdout is not None else []
        stderr = _read_bounded(proc.stderr, ledger) if proc.stderr is not None else b""
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
    blob = json.dumps(body, sort_keys=True).encode("utf-8")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    with receipt.open("wb") as handle:
        while offset < len(blob):
            chunk = blob[offset:offset + 65536]
            ledger.reserve_receipt(len(chunk))
            handle.write(chunk)
            offset += len(chunk)
    print(json.dumps({"status": payload.get("status", "OK"), "compatibility": "BLOCKED", "receipt_bytes": len(blob)}))
    return 0 if payload.get("status") == "OK" else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--cli-child" in argv:
        from .guards import install_env_trap

        install_env_trap()
        _install_vas()
        try:
            payload = run_inprocess(argv)
        except RuntimeError as exc:
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
