"""Read-only kernel identity for one reviewed handled reset incident."""
from pathlib import Path
import os

PID = 663
START_TICKS = 106
BOOT_ID = "58b6ba0f-d181-4414-ac20-2934b7f34851"
STARTED_AT = 1788604317.06
FAILURE_MTIME = 1788605101.5145597


def verify_handled_reset_kernel_continuity(*, reset_anchor, operation_mtime):
    """Never accept caller-selected proc roots or caller boolean continuity."""
    return _verify(Path("/proc"), reset_anchor, operation_mtime, os.sysconf("SC_CLK_TCK"))


def _verify(proc, reset_anchor, operation_mtime, ticks_per_second):
    def read(path, limit):
        with path.open("rb") as stream: data = stream.read(limit + 1)
        if len(data) > limit: raise ValueError("KERNEL_RECEIPT_OVERSIZED")
        return data.decode("ascii").strip()
    boot = read(proc / "sys/kernel/random/boot_id", 128)
    first = read(proc / str(PID) / "stat", 8192)
    # comm may contain spaces/parentheses: field3 begins after final ')'.
    def start(row):
        if int(row.split("(", 1)[0].strip()) != PID: raise ValueError("KERNEL_PID_MISMATCH")
        fields = row.rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X"}: raise ValueError("KERNEL_PROCESS_TERMINAL")
        return int(fields[19])
    tick = start(first)
    btime_rows = [r.split()[1] for r in read(proc / "stat", 1024 * 1024).splitlines() if r.startswith("btime ")]
    if len(btime_rows) != 1: raise ValueError("KERNEL_BOOT_TIME_INVALID")
    started = int(btime_rows[0]) + tick / ticks_per_second
    second = read(proc / str(PID) / "stat", 8192)
    if (boot != BOOT_ID or read(proc / "sys/kernel/random/boot_id", 128) != boot
            or tick != START_TICKS or start(second) != tick
            or abs(started - STARTED_AT) > .00001
            or abs(float(operation_mtime) - FAILURE_MTIME) > .00001
            or not started < float(reset_anchor) <= float(operation_mtime)):
        raise ValueError("KERNEL_CONTINUITY_MISMATCH")
    return {"pid": PID, "start_ticks": tick, "boot_id": boot,
        "started_at": started, "operation_mtime": float(operation_mtime),
        "reset_anchor": float(reset_anchor), "continuity": "KERNEL_RECHECKED_SAME_INCIDENT_PROCESS"}
