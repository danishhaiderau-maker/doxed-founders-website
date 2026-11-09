"""C3: bounded in-process relauncher, no subprocesses or scheduler installation.

Every relaunch waits >=6 seconds after the previous exit. Exit 0, 75 or 78 is
terminal. A checksum journal persists terminal exits across program invocations.
Torn journals and unpaired starts require operator review, never auto-restart.
All callers must hold the runtime's single-writer lock. Nothing clears a latch.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import StartRefused
from .framing import AppendJournal, read_journal

RELAUNCH_MIN_INTERVAL_S = 6.0
TERMINAL_EXIT_CODES = frozenset({0, 75, 78})


@dataclass(frozen=True)
class Decision:
    restart: bool
    wait_s: float
    reason: str


def decide(exit_code: int) -> Decision:
    code = int(exit_code)
    if code in TERMINAL_EXIT_CODES:
        return Decision(False, 0.0, f"terminal_exit_{code}")
    return Decision(True, RELAUNCH_MIN_INTERVAL_S, "restart_after_floor")


class LaunchJournal:
    def __init__(self, path: Path, guard_write=lambda: None):
        self.path = path
        self.guard_write = guard_write

    def startup_gate(self) -> float:
        rows, torn = read_journal(self.path)
        if torn:
            raise StartRefused("RELAUNCH_JOURNAL_TORN")
        if not rows:
            return 0.0
        last = rows[-1]
        if last.get("kind") != "exit" or type(last.get("exit_code")) is not int:
            raise StartRefused("PREVIOUS_RUN_UNCERTAIN")
        if last["exit_code"] in {75, 78}:
            raise StartRefused(f"PREVIOUS_TERMINAL_EXIT_{last['exit_code']}")
        # Always wait a full floor after acquiring the lock. No dependence on
        # wall-clock jumps, uptime, or a former process's monotonic epoch.
        return RELAUNCH_MIN_INTERVAL_S

    def append(self, kind: str, now_ns: int, *, exit_code=None) -> None:
        self.guard_write()
        journal = AppendJournal(self.path, deque(maxlen=8), exclusive=False)
        try:
            row = {"schema_v": "pmc_launch_v1", "kind": kind, "utc_ns": int(now_ns)}
            if exit_code is not None:
                row["exit_code"] = int(exit_code)
            journal.append_obj(row, label=kind)
        finally:
            journal.close()


def supervise(run_once, *, journal: LaunchJournal, clock_ns, sleep, stop=lambda: False, max_runs=None) -> int:
    """Run runtime callbacks, never a command. Unexpected failures return 1 from
    run_once and may restart after the floor; terminal stops never restart.
    ``sleep`` must wait the entire requested delay unless ``stop()`` is true.
    """
    wait = journal.startup_gate()
    if wait:
        sleep(wait)
    runs = 0
    while not stop() and (max_runs is None or runs < max_runs):
        journal.append("start", clock_ns())
        code = int(run_once())
        journal.append("exit", clock_ns(), exit_code=code)
        runs += 1
        policy = decide(code)
        if not policy.restart or (max_runs is not None and runs >= max_runs):
            return code
        sleep(policy.wait_s)
    return 0
