"""Read-only research-segment shipper throughput probe (run inside the Fly machine).

Reports CPU contention (loadavg, machine busy share, shipper scheduler run vs
wait time), the shipper's recent log, its status, and a full-priority dry-run
scan+plan timed against the live checkpoint with per-stream pending bytes.
It never writes the shipper state dir, the store or any runtime file.
"""
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

APP = Path("/app")
DATA = Path(os.getenv("BOT_DATA_DIR") or "/app/data")
STATE_DIR = Path(os.getenv("RESEARCH_SEGMENTS_STATE_DIR") or (
    DATA / "segment-shipper-v2" if (DATA / "segment-shipper-v2").is_dir() else DATA / "segment-shipper"))
SAMPLE_SECONDS = 5.0


def cpu_totals():
    fields = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = fields[3] + fields[4]
    return sum(fields), idle


def proc_stat(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    rest = raw[raw.rindex(")") + 2:].split()
    return {"utime": int(rest[11]), "stime": int(rest[12]), "nice": int(rest[16]),
            "policy": int(rest[38])}


def schedstat(pid):
    run_ns, wait_ns, slices = (int(x) for x in Path(f"/proc/{pid}/schedstat").read_text().split())
    return run_ns, wait_ns, slices


def find_pids():
    shipper, bot = [], []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "research_segment_shipper.py" in cmd:
            shipper.append(int(entry.name))
        elif cmd.strip().endswith("bot.py") or " bot.py" in cmd:
            bot.append(int(entry.name))
    return shipper, bot


def contention():
    shipper, bot = find_pids()
    tick = os.sysconf("SC_CLK_TCK")
    before_cpu = cpu_totals()
    before = {pid: (proc_stat(pid), schedstat(pid)) for pid in shipper + bot}
    time.sleep(SAMPLE_SECONDS)
    after_cpu = cpu_totals()
    total = after_cpu[0] - before_cpu[0]
    out = {"loadavg": Path("/proc/loadavg").read_text().split()[:3],
           "machine_busy_pct": round(100.0 * (total - (after_cpu[1] - before_cpu[1])) / max(1, total), 1),
           "processes": []}
    for pid, (stat0, sched0) in before.items():
        try:
            stat1, sched1 = proc_stat(pid), schedstat(pid)
        except OSError:
            continue
        run = (sched1[0] - sched0[0]) / 1e9
        wait = (sched1[1] - sched0[1]) / 1e9
        out["processes"].append({
            "pid": pid, "role": "shipper" if pid in shipper else "bot",
            "nice": stat1["nice"], "policy": stat1["policy"],
            "cpu_pct": round(100.0 * ((stat1["utime"] + stat1["stime"]) - (stat0["utime"] + stat0["stime"]))
                             / tick / SAMPLE_SECONDS, 1),
            "sched_run_s": round(run, 3), "sched_runqueue_wait_s": round(wait, 3),
        })
    return out


def dry_run_plan():
    sys.path.insert(0, str(APP))
    import research_segment_shipper as rs

    state = json.loads((STATE_DIR / "state.json").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    blob = json.dumps(state)
    json.loads(blob)
    state_json_seconds = time.perf_counter() - t0
    with tempfile.TemporaryDirectory() as scratch:
        env = os.environ
        shipper = rs.SegmentShipper(
            store=None, volume_root=DATA, runtime_root=DATA / "runtime", state_dir=Path(scratch),
            rules=rs.load_selection_rules(), prefix=state["prefix"], sink=rs.sink_from_env(),
            max_segment_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_SEGMENT_BYTES") or 8 * 1024 * 1024),
            max_member_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_MEMBER_BYTES") or 64 * 1024 * 1024),
            large_snapshot_bytes=int(env.get("RESEARCH_SEGMENTS_LARGE_SNAPSHOT_BYTES") or 1024 * 1024),
            large_snapshot_interval=float(env.get("RESEARCH_SEGMENTS_LARGE_SNAPSHOT_INTERVAL_SECONDS") or 3600),
        )
        t0 = time.perf_counter()
        universe = shipper.scan()
        scan_seconds = time.perf_counter() - t0
        t0 = time.perf_counter()
        ops = shipper.plan(state, universe)
        plan_seconds = time.perf_counter() - t0
    by_stream, by_kind, by_top = Counter(), Counter(), Counter()
    for op in ops:
        pending = int(op.get("pending_bytes", op["bytes"]))
        by_stream[op["stream"]] += pending
        by_kind[op["kind"]] += pending
        by_top[op["stream"].split("/")[0] if "/" in op["stream"] else "<root>"] += pending
    return {
        "state_bytes": len(blob), "state_files": len(state.get("files", {})),
        "state_json_roundtrip_s": round(state_json_seconds, 3),
        "universe_files": len(universe), "scan_s": round(scan_seconds, 3),
        "plan_s": round(plan_seconds, 3), "ops": len(ops),
        "pending_bytes": sum(by_stream.values()),
        "pending_by_kind": dict(by_kind.most_common()),
        "pending_by_top": dict(by_top.most_common(15)),
        "pending_top_streams": by_stream.most_common(40),
        "throttled": sorted(shipper.throttled)[:20],
    }


def main():
    report = {"at": time.time(), "state_dir": str(STATE_DIR)}
    try:
        report["contention"] = contention()
    except Exception as exc:  # probe must keep going
        report["contention_error"] = f"{type(exc).__name__}: {exc}"
    try:
        report["status"] = json.loads((STATE_DIR / "status.json").read_text(encoding="utf-8"))
    except Exception as exc:
        report["status_error"] = f"{type(exc).__name__}: {exc}"
    log = DATA / "segment-shipper.log"
    try:
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 16384))
            report["log_tail"] = handle.read().decode(errors="replace").splitlines()[-60:]
    except Exception as exc:
        report["log_error"] = f"{type(exc).__name__}: {exc}"
    try:
        report["dry_run"] = dry_run_plan()
    except Exception as exc:
        report["dry_run_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(report, sort_keys=True, default=str))


main()
