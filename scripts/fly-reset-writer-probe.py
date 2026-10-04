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


def processes():
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        if "python" in cmd:
            out.append({"pid": int(pid), "cmd": cmd[:160]})
    return out


def threads():
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read()
        except OSError:
            continue
        if b"btc_conservative_agent.py" not in cmd:
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


def sha256_file(path):
    import hashlib
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def reset_incident(paths):
    """Hash-bound facts the pre-deletion abort registry needs; reads only."""
    import hashlib
    receipts = os.path.join(RUNTIME, "research_reset_receipts")
    active = os.path.join(receipts, "ACTIVE_RESET.json")
    out = {"deleter_sha256": sha256_file("/app/research_exact_deletion.py"),
           "source_git_rev": os.getenv("SOURCE_GIT_REV"), "receipt_dirs": sorted(os.listdir(receipts))}
    if not os.path.exists(active):
        return {**out, "active_reset": None}
    pointer = json.load(open(active, encoding="utf-8"))
    directory = os.path.join(receipts, str(pointer.get("reset_id")))
    binding_path, operation_path = os.path.join(directory, "binding.json"), os.path.join(directory, "operation.json")
    binding = json.load(open(binding_path, encoding="utf-8"))
    operation = json.load(open(operation_path, encoding="utf-8"))
    proof, evidence = binding.get("proof") or {}, binding.get("boundary_evidence") or {}
    mismatch = operation.get("hash_mismatch") or (operation.get("failure_detail") or {}).get("hash_mismatch") or {}
    by_path_hash = {hashlib.sha256(p.encode("utf-8")).hexdigest(): p for p in paths}
    return {**out, "active_reset": {
        "reset_id": pointer.get("reset_id"),
        "sha256": {"active": sha256_file(active), "binding": sha256_file(binding_path),
                   "operation": sha256_file(operation_path)},
        "operation_mtime": os.stat(operation_path).st_mtime,
        "operation_keys": sorted(operation),
        "stage": operation.get("stage"), "failed_stage": operation.get("failed_stage"),
        "rejection_code": operation.get("rejection_code"), "hash_mismatch": mismatch,
        "hash_mismatch_path": by_path_hash.get(mismatch.get("target_path_sha256")),
        "later_stage_fields": {k: bool(operation.get(k)) for k in
                               ("deletion", "scope_deletions", "genome_reset_completed", "authority_retirements")},
        "reset_anchor": binding.get("reset_anchor"), "physical_scopes": binding.get("physical_scopes"),
        "new_epoch_id": proof.get("new_epoch_id"), "retired_epoch_id": proof.get("retired_epoch_id"),
        "deployed_revision": evidence.get("deployed_revision"),
        "directory_files": sorted(os.listdir(directory))}}


start = time.time()
before = candidates()
try:
    incident = reset_incident(list(before))
except (OSError, ValueError) as exc:
    incident = {"error": f"{type(exc).__name__}: {exc}"}
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
                  "plan_errors": PLAN_ERRORS, "reset_incident": incident,
                  "processes": processes()},
                 sort_keys=True))
