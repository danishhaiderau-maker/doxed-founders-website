"""Read-only: which reset deletion targets change while the bot is held, and who holds them open.

Snapshots (size, mtime_ns) of every reset target candidate, sleeps, re-stats
the same paths, and reports changed files with deltas plus the bot processes
holding an open descriptor on each. Never writes, never opens targets for write.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")
from research_reset_inventory import plan_research_reset  # noqa: E402

RUNTIME = "/app/data/runtime"
SAMPLE_SEC = int(os.environ.get("PROBE_SAMPLE_SEC", "180"))
CANDIDATE = "EPOCH_RECOVERY_BOUNDARY_PROOF_REQUIRED"


PLAN_ERRORS = []


def candidates():
    rows = {}
    for scope in (None, "research", "research_accumulator", "research_archive"):
        if scope and not os.path.islink(os.path.join(RUNTIME, scope)):
            continue
        plan = plan_research_reset(RUNTIME, proof=None, allow_fly_runtime_aliases=True, scope_name=scope)
        if plan.get("errors"):
            PLAN_ERRORS.append({"scope": scope or "runtime", "errors": plan["errors"][:10]})
        for row in plan["retained"]:
            if row.get("category") and row.get("reason") in (CANDIDATE, "INCOMPLETE_INVENTORY_NO_TARGETS"):
                rows[row["absolute_path"]] = (row["category"], row["size_bytes"], row["mtime_ns"])
    return rows


def open_handles(paths):
    holders = {path: [] for path in paths}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target in holders:
                info = ""
                try:
                    info = open(f"/proc/{pid}/fdinfo/{fd}").read().split("flags:")[1].split()[0]
                except (OSError, IndexError):
                    pass
                holders[target].append({"pid": int(pid), "fd": int(fd), "flags_octal": info, "cmd": cmd[:120]})
    return holders


def threads():
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read()
        except OSError:
            continue
        if b"bot.py" not in cmd:
            continue
        names = {}
        for tid in os.listdir(f"/proc/{pid}/task"):
            try:
                name = open(f"/proc/{pid}/task/{tid}/comm").read().strip()
            except OSError:
                continue
            names[name] = names.get(name, 0) + 1
        out.append({"pid": int(pid), "thread_names": names})
    return out


start = time.time()
before = candidates()
time.sleep(SAMPLE_SEC)
changed = []
for path, (category, size, mtime) in before.items():
    try:
        st = os.stat(path)
    except FileNotFoundError:
        changed.append({"path": path, "category": category, "vanished": True})
        continue
    if (st.st_size, st.st_mtime_ns) != (size, mtime):
        changed.append({"path": path, "category": category, "size_before": size, "size_after": st.st_size,
                        "size_delta": st.st_size - size, "mtime_delta_s": round((st.st_mtime_ns - mtime) / 1e9, 3),
                        "mtime_after_unix": round(st.st_mtime_ns / 1e9, 3)})
holders = open_handles([row["path"] for row in changed])
for row in changed:
    row["open_by"] = holders.get(row["path"], [])
by_category = {}
for category, _, _ in before.values():
    by_category[category] = by_category.get(category, 0) + 1
print(json.dumps({"schema": "fly_reset_writer_probe_v1", "sample_sec": SAMPLE_SEC,
                  "scan_sec": round(time.time() - start - SAMPLE_SEC, 1), "candidates": len(before),
                  "candidates_by_category": by_category, "changed_count": len(changed),
                  "changed": sorted(changed, key=lambda r: r["path"]), "bot_threads": threads(),
                  "plan_errors": PLAN_ERRORS},
                 sort_keys=True))
