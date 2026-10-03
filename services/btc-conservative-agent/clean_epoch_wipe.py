"""clean-epoch-wipe: hard-delete pre-epoch untrusted data, plan-first, with a deletion receipt.

Dry run is the default. Nothing is deleted unless ``execute`` is given the
declared epoch id, the confirmation token and the sha256 of a plan produced in
the same boundary, and the epoch passed its 2 h certification.

``--pre-start`` (Danish: wipe while the bot is held down, before the final start)
plans everything existing now and executes only with
``--confirm DELETE-PRE-START:<plan sha256[:12]>`` while the bot reports the
paper maintenance hold (paused by DEPLOY_MAINTENANCE, disarmed, paper-only).

    # laptop (Danish runs these by hand)
    python clean_epoch_wipe.py plan --scope laptop --epoch ce-20261004-v31-clean
    python clean_epoch_wipe.py execute --scope laptop --epoch ce-20261004-v31-clean \
        --confirm DELETE-PRE-EPOCH:ce-20261004-v31-clean:<cert8> --expect-plan-sha256 <sha>

    # Fly guest (dispatch-only workflow modes clean-epoch-wipe-plan / -execute)
    python /app/clean_epoch_wipe.py plan --scope fly --data-root /app/data --epoch <id>

    # laptop preview of the Fly plan from the read-only shipper checkpoint
    python clean_epoch_wipe.py plan --scope fly --fly-files-json files.json --simulate-now

Hard KEEP (checked while planning and again for every file at execution, no
flag disables it): relay/Bitfinex evidence, restart-recovery state (V3 store,
SQLite, top-level runtime state, locks, shipper state, epoch manifest), the 1 s
market tape and other epoch-independent market data, secrets/config, code.
On Fly only sealed numbered rotations, quarantine copies, a fully ACKed archived
segment prefix and retired transfer state are candidates; live append heads are
never deleted. Anything not matched by a candidate class is retained.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import data_epoch

PLAN_SCHEMA = "clean_epoch_wipe_plan_v1"
RECEIPT_SCHEMA = "clean_epoch_wipe_receipt_v1"
CONFIRM_PREFIX = "DELETE-PRE-EPOCH"
PRE_START_PREFIX = "DELETE-PRE-START"
PRE_START_LABEL = "pre-start"
HELD_DOWN_URLS = {"laptop": "https://doxed-btc-bot.fly.dev/health", "fly": "http://127.0.0.1:7002/health"}
DIGEST_GRID_MIN_EPOCH_AGE_SEC = 6 * 3600
ROTATION_RE = re.compile(r"^(?P<base>.+\.(?:jsonl|csv|log))\.(?P<n>[1-9][0-9]*)(?:\.gz)?$")
DIGEST_GRID_BASES = frozenset({"chase_offset_touch_grid.jsonl", "order_multiverse_entry_grid.jsonl"})
TAPE_BASES = frozenset({"market_microstructure_1s.jsonl"})
TAPE_DATASETS = frozenset({"bitfinex_l1_tape_1s"})
MARKET_DATASETS = frozenset({"cross_venue_tape_1m", "market_context_1m", "liquidations"})
CODE_SUFFIXES = (".py", ".pyc", ".ps1", ".psm1", ".psd1", ".cmd", ".bat", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx",
                 ".toml", ".yml", ".yaml", ".md", ".ipynb", ".dockerfile")
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".sqlite3-wal", ".sqlite3-shm")
SECRET_TOKENS = ("secret", "credential", "vault", ".env", "token", "fly.toml", "apikey", "api_key", "private_key")
CONFIG_TOKENS = ("config", "pathway_lane_specs")
# Irreplaceable research that outlives an epoch: forward-tracker hash chains, pre-registrations, the shadow-exit
# backfill, hypothesis study output and signed registry/deploy/visual-QA receipts.
PRESERVED_RESEARCH_TOKENS = ("forward-tracker", "forward_tracker", "pre_registration", "pre-registration",
                             "shadow-exits", "hypothesis-tiles", "registry_receipt", "registry-receipt",
                             "deploy_receipt", "deploy-receipt", "visual-qa", "visual_qa")
RELAY_TOKENS = ("relay", "bitfinex", "live_copy", "exchange_", "platform-relay", "live-copy", "rearm")
# The bot's restart-recovery state lives on Fly; laptop copies of it are pre-epoch data. The laptop keeps only
# its own live chain state (locks, leases, ACKs, cursors, epoch manifest and receipts).
CHAIN_STATE_TOKENS = ("data_epoch", "clean-epoch", ".lock", ".lease", "prune-mode", "retention/", "acks/", "state.json",
                      "status.json", ".puller")
RECOVERY_TOKENS = CHAIN_STATE_TOKENS + (
    "recovery", "paper_lifecycle", "open_positions", "research_session", "research_reset_receipts", "crash_dump",
    "counters", "lane_pnl_ledger", "lane_lab_pnl_ledger", "runtime_pathway_integrity")

LAPTOP_ROOTS: dict[str, dict[str, Any]] = {
    # name: path, kind
    "pre_clean_epoch_staging": {"path": r"C:\DoxxedCrypto\pre-clean-epoch", "kind": "all"},
    "mirror_tree": {"path": r"C:\DoxxedCrypto\fly-mirror-segments\tree", "kind": "mirror"},
    "promotion_view": {"path": r"C:\DoxxedCrypto\segment-promotion-view", "kind": "mirror"},
    "analyzer_view": {"path": r"C:\DoxxedCrypto\segment-analyzer-view", "kind": "mirror"},
    "canonical_research_data": {"path": r"C:\DoxxedCrypto\v2c\services\btc-conservative-agent\canonical-research-data",
                                "kind": "mirror"},
    "segment_archive": {"path": r"C:\DoxxedCrypto\fly-segments", "kind": "all"},
    "historical_archive": {"path": r"C:\DoxxedCrypto\archive", "kind": "all"},
    "analyzer_exports": {"path": r"C:\DoxxedCrypto\analyzer-exports", "kind": "all"},
    "analysis_archive": {"path": r"C:\DoxxedCrypto\analysis-archive", "kind": "all"},
    "tier_a_compact": {"path": r"C:\DoxxedCrypto\bot-data-compact", "kind": "tier_a"},
    "perf_caches": {"path": r"C:\DoxxedCrypto\perf", "kind": "all"},
}
LAPTOP_RECEIPTS = r"C:\DoxxedCrypto\clean-epoch"
ANALYZER_CYCLE_STATUS = r"C:\DoxxedCrypto\laptop-chain\segment-analyzer-cycle.status.json"


# ------------------------------------------------------------------ hard KEEP guard

def keep_reason(relpath: str, *, scope: str) -> str | None:
    """Why ``relpath`` (POSIX, relative to its root) must never be deleted, else None."""
    rel = relpath.replace("\\", "/")
    lower = rel.lower()
    parts = lower.split("/")
    name = parts[-1]
    base = ROTATION_RE.sub(lambda m: m.group("base"), name)
    if ".git" in parts or lower.endswith(CODE_SUFFIXES) or name in ("dockerfile", "makefile"):
        return "CODE"
    if any(t in lower for t in SECRET_TOKENS):
        return "SECRETS"
    if base in TAPE_BASES or any(d in parts for d in TAPE_DATASETS):
        return "MARKET_TAPE_1S"
    if any(t in lower for t in RELAY_TOKENS):
        return "RELAY_BITFINEX_EVIDENCE"
    if any(t in lower for t in PRESERVED_RESEARCH_TOKENS):
        return "PRESERVED_RESEARCH"
    if base in data_epoch.EPOCH_INDEPENDENT_BASES or any(d in parts for d in MARKET_DATASETS):
        return "MARKET_DATA_EPOCH_INDEPENDENT"
    if any(t in lower for t in CONFIG_TOKENS):
        return "CONFIG"
    if scope == "laptop":
        return "LAPTOP_CHAIN_STATE" if any(t in lower for t in CHAIN_STATE_TOKENS) else None
    if lower.endswith(SQLITE_SUFFIXES):
        return "RESTART_STATE_SQLITE"
    if any(t in lower for t in RECOVERY_TOKENS):
        return "RESTART_RECOVERY_STATE"
    if scope == "fly":
        if lower.startswith(("v3/", "runtime/v3/")):
            return "RESTART_STATE_V3_STORE"
        if len(parts) == 1 and name.endswith(".json"):
            return "RESTART_STATE_TOP_LEVEL"
    return None


def _confirm_token(epoch_id: str, cert_sha8: str) -> str:
    return f"{CONFIRM_PREFIX}:{epoch_id}:{cert_sha8}"


# ------------------------------------------------------------------ file walking

def _walk(root: Path) -> Iterable[tuple[str, os.stat_result]]:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(directory, d)) and d != ".git"]
        for fname in filenames:
            path = os.path.join(directory, fname)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if os.path.islink(path):
                continue
            yield os.path.relpath(path, root).replace("\\", "/"), st


class Plan:
    def __init__(self, scope: str, epoch_id: str, epoch_start: float, simulated: bool) -> None:
        self.scope, self.epoch_id, self.epoch_start, self.simulated = scope, epoch_id, epoch_start, simulated
        self.candidates: list[dict] = []
        self.kept: dict[str, dict[str, int]] = {}
        self.kept_examples: dict[str, list[str]] = {}
        self.notes: list[str] = []
        self.by_root: dict[str, dict[str, int]] = {}

    def delete(self, root: str, abspath: str, st_size: int, st_mtime_ns: int, reason: str, rel: str) -> None:
        self.candidates.append({"root": root, "path": abspath, "rel": rel.replace("\\", "/"), "bytes": int(st_size),
                                "mtime_ns": int(st_mtime_ns), "reason": reason})
        r = self.by_root.setdefault(root, {"delete_files": 0, "delete_bytes": 0, "keep_files": 0, "keep_bytes": 0})
        r["delete_files"] += 1
        r["delete_bytes"] += int(st_size)

    def keep(self, root: str, relpath: str, size: int, reason: str) -> None:
        k = self.kept.setdefault(reason, {"files": 0, "bytes": 0})
        k["files"] += 1
        k["bytes"] += int(size)
        ex = self.kept_examples.setdefault(reason, [])
        if len(ex) < 8:
            ex.append(f"{root}:{relpath}")
        r = self.by_root.setdefault(root, {"delete_files": 0, "delete_bytes": 0, "keep_files": 0, "keep_bytes": 0})
        r["keep_files"] += 1
        r["keep_bytes"] += int(size)

    def doc(self) -> dict:
        self.candidates.sort(key=lambda c: c["path"])
        identity = json.dumps([[c["path"], c["bytes"], c["mtime_ns"]] for c in self.candidates],
                              separators=(",", ":")).encode()
        by_reason: dict[str, dict[str, int]] = {}
        for c in self.candidates:
            r = by_reason.setdefault(c["reason"], {"files": 0, "bytes": 0})
            r["files"] += 1
            r["bytes"] += c["bytes"]
        delete_bytes = sum(c["bytes"] for c in self.candidates)
        keep_bytes = sum(v["bytes"] for v in self.kept.values())
        return {"schema": PLAN_SCHEMA, "scope": self.scope, "epoch_id": self.epoch_id,
                "epoch_started_at_utc": data_epoch.utc_iso(self.epoch_start), "simulated": self.simulated,
                "planned_at_utc": data_epoch.utc_iso(time.time()),
                "total_delete_files": len(self.candidates), "total_delete_bytes": delete_bytes,
                "total_delete_gb": round(delete_bytes / 1e9, 3), "total_keep_bytes": keep_bytes,
                "total_keep_gb": round(keep_bytes / 1e9, 3), "delete_by_reason": by_reason, "by_root": self.by_root,
                "kept_by_reason": self.kept, "kept_examples": self.kept_examples, "notes": self.notes,
                "plan_sha256": hashlib.sha256(identity).hexdigest(), "candidates": self.candidates}


# ------------------------------------------------------------------ laptop planner

def plan_laptop(plan: Plan, roots: dict[str, dict[str, Any]] | None = None) -> None:
    roots = roots or LAPTOP_ROOTS
    epoch_day = time.strftime("%Y-%m-%d", time.gmtime(plan.epoch_start))
    start_ns = int(plan.epoch_start * 1e9)
    for name, spec in roots.items():
        root = Path(spec["path"])
        if "onedrive" in str(root).lower():
            raise SystemExit(f"REFUSED: {root} is under OneDrive")
        if not root.is_dir():
            plan.notes.append(f"{name}: {root} absent")
            continue
        for rel, st in _walk(root):
            reason = keep_reason(rel, scope="laptop")
            if reason:
                plan.keep(name, rel, st.st_size, reason)
                continue
            if st.st_mtime_ns >= start_ns:
                plan.keep(name, rel, st.st_size, "WRITTEN_AFTER_EPOCH_START")
                continue
            if spec["kind"] == "tier_a":
                m = re.search(r"/date=(\d{4}-\d{2}-\d{2})/", "/" + rel)
                if m and m.group(1) >= epoch_day:
                    plan.keep(name, rel, st.st_size, "TIER_A_EPOCH_DAY_PARTITION_MIXED")
                    continue
            plan.delete(name, str(root / rel), st.st_size, st.st_mtime_ns, f"PRE_EPOCH_{spec['kind'].upper()}", rel)


# ------------------------------------------------------------------ Fly planner

def _archived_prefix_candidates(data_root: Path, plan: Plan) -> None:
    """A superseded segment prefix whose final published seq the laptop ACKed."""
    store = data_root / "segment-store"
    current = (os.environ.get("RESEARCH_SEGMENTS_PREFIX") or "").strip()
    archives = {}
    for item in (os.environ.get("RESEARCH_SEGMENTS_ARCHIVE_PREFIXES") or "").split(","):
        if "=" in item:
            prefix, state_dir = item.split("=", 1)
            archives[prefix.strip()] = Path(state_dir.strip())
    if not store.is_dir():
        return
    for prefix_dir in sorted(p for p in store.iterdir() if p.is_dir()):
        prefix = prefix_dir.name
        if not current or prefix == current:
            plan.keep("fly_segment_store", prefix, 0, "CURRENT_SEGMENT_PREFIX")
            continue
        state_dir = archives.get(prefix)
        published = None
        if state_dir is not None:
            for name in ("status.json", "state.json"):
                try:
                    doc = json.loads((state_dir / name).read_text("utf-8"))
                    published = int(doc.get("shipped_seq") or doc.get("seq") or 0) or None
                    break
                except (OSError, ValueError, TypeError):
                    continue
        ack = prefix_dir / "acks" / "laptop" / f"{published or 0:012d}.json"
        if not published or not ack.is_file():
            plan.notes.append(f"segment prefix {prefix}: final seq {published} not ACKed by the laptop; retained")
            for rel, st in _walk(prefix_dir):
                plan.keep("fly_segment_store", f"{prefix}/{rel}", st.st_size, "ARCHIVED_PREFIX_NOT_FULLY_ACKED")
            continue
        # Whole archived prefix: acks/state names inside it are this prefix's, not the live recovery state.
        for rel, st in _walk(prefix_dir):
            plan.delete("fly_segment_store", str(prefix_dir / rel), st.st_size, st.st_mtime_ns,
                        f"ARCHIVED_SEGMENT_PREFIX_{prefix}_FULLY_ACKED", f"segment-store/{prefix}/{rel}")
        if state_dir is not None and state_dir.is_dir():
            for rel, st in _walk(state_dir):
                plan.delete("fly_shipper_state", str(state_dir / rel), st.st_size, st.st_mtime_ns,
                            f"ARCHIVED_SHIPPER_STATE_{prefix}", f"{state_dir.name}/{rel}")


def _fly_runtime_decision(rel: str, mtime_ns: int | None, plan: Plan, epoch_age: float) -> str | None:
    """Delete reason for one runtime-relative file, else None (caller records keep)."""
    name = rel.rsplit("/", 1)[-1]
    m = ROTATION_RE.match(name)
    if "quarantine" in rel.lower().split("/")[0]:
        return "PRE_EPOCH_QUARANTINE_COPY" if mtime_ns is None or mtime_ns < plan.epoch_start * 1e9 else None
    if not m:
        return None
    if mtime_ns is not None and mtime_ns >= plan.epoch_start * 1e9:
        return None
    if m.group("base") in DIGEST_GRID_BASES and epoch_age < DIGEST_GRID_MIN_EPOCH_AGE_SEC:
        return None
    return f"PRE_EPOCH_SEALED_ROTATION:{m.group('base')}"


def plan_fly(plan: Plan, data_root: Path, *, now: float) -> None:
    runtime = data_root / "runtime"
    epoch_age = now - plan.epoch_start
    roots = [("fly_runtime", runtime)]
    for link in ("research", "research_accumulator", "research_archive"):
        try:
            target = (runtime / link).resolve(strict=True)
            target.relative_to(data_root.resolve())
        except (OSError, ValueError):
            continue
        try:
            target.relative_to(runtime.resolve())
        except ValueError:
            if target.is_dir():
                roots.append((f"fly_{link}", target))
    for root_name, root in roots:
        if not root.is_dir():
            plan.notes.append(f"{root_name}: {root} absent")
            continue
        for rel, st in _walk(root):
            reason = keep_reason(rel, scope="fly")
            if reason:
                plan.keep(root_name, rel, st.st_size, reason)
                continue
            delete = _fly_runtime_decision(rel, st.st_mtime_ns, plan, epoch_age)
            if delete:
                plan.delete(root_name, str(root / rel), st.st_size, st.st_mtime_ns, delete, rel)
            elif ROTATION_RE.match(rel.rsplit("/", 1)[-1]):
                plan.keep(root_name, rel, st.st_size, "ROTATION_AFTER_EPOCH_OR_DIGEST_GUARD")
            else:
                plan.keep(root_name, rel, st.st_size, "LIVE_HEAD_OR_UNCLASSIFIED")
    _archived_prefix_candidates(data_root, plan)
    try:
        import research_fresh_start_wipe as fsw  # noqa: PLC0415 - same image, optional off-Fly
        for row in fsw.legacy_transfer_candidates(data_root, now):
            plan.delete("fly_legacy_transfer", row["path"], row["bytes"], row["mtime_ns"], "RETIRED_LEGACY_TRANSFER_STATE",
                        os.path.relpath(row["path"], data_root))
    except ImportError:
        pass


def plan_fly_from_checkpoint(plan: Plan, files_doc: dict, data_size: dict | None, *, now: float) -> None:
    """Laptop preview of the Fly plan from the shipper checkpoint (no mtimes: all pre-epoch at cutover)."""
    files = files_doc.get("files") or {}
    total = 0
    for rel, meta in sorted(files.items()):
        size = int(meta.get("size") or 0)
        total += size
        reason = keep_reason(rel, scope="fly")
        if reason:
            plan.keep("fly_runtime", rel, size, reason)
            continue
        delete = _fly_runtime_decision(rel, None, plan, max(now - plan.epoch_start, DIGEST_GRID_MIN_EPOCH_AGE_SEC))
        if delete:
            plan.candidates.append({"root": "fly_runtime", "path": "/app/data/runtime/" + rel, "rel": rel,
                                    "bytes": size, "mtime_ns": 0, "reason": delete})
            r = plan.by_root.setdefault("fly_runtime", {"delete_files": 0, "delete_bytes": 0, "keep_files": 0,
                                                        "keep_bytes": 0})
            r["delete_files"] += 1
            r["delete_bytes"] += size
        else:
            plan.keep("fly_runtime", rel, size, "LIVE_HEAD_OR_UNCLASSIFIED")
    plan.notes.append(f"checkpoint seq {files_doc.get('seq')} prefix {files_doc.get('prefix')}: "
                      f"{len(files)} runtime files, {total / 1e9:.2f} GB")
    if data_size:
        used = int(float(data_size.get("filesystem_used_mb") or 0) * 1024 * 1024)
        outside = max(used - total, 0)
        plan.notes.append(f"volume used {used / 1e9:.2f} GB; {outside / 1e9:.2f} GB outside the runtime checkpoint "
                          "(segment store v2 + shipper state + v1 archive); the archived v2 segment prefix becomes a "
                          "candidate only after the laptop ACKs its final seq - ESTIMATE, not in this plan's totals")
        plan.notes.append(f"volume_used_bytes={used} outside_runtime_bytes={outside}")


# ------------------------------------------------------------------ execution

def _analyzer_cycle_running(path: str | None = None) -> bool:
    try:
        doc = json.loads(Path(path or ANALYZER_CYCLE_STATUS).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if doc.get("finishedAt"):
        return False
    pid = int(doc.get("pid") or 0)
    if not pid:
        return False
    if os.name == "nt":
        import ctypes  # noqa: PLC0415
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def held_down_violations(health: dict) -> list[str]:
    """Why the bot is not in the paper maintenance hold a pre-start wipe requires (empty = held)."""
    expected = {"execution_paused": True, "pause_owner": "DEPLOY_MAINTENANCE", "live_armed": False,
                "bitfinex_live_enabled": False, "force_paper_mode": True}
    return [f"{k}={health.get(k)!r}" for k, v in expected.items() if health.get(k) != v]


def _fetch_health(url: str) -> dict:
    import urllib.request  # noqa: PLC0415
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def pre_start_token(plan_sha256: str) -> str:
    return f"{PRE_START_PREFIX}:{plan_sha256[:12]}"


def _load_certification(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def execute(plan_doc: dict, *, receipts_dir: Path, scope: str, cert_sha8: str,
            unlink: Callable[[str], None] = os.unlink) -> dict:
    started = time.time()
    deleted, freed, skipped = [], 0, []
    for row in plan_doc["candidates"]:
        path = row["path"]
        archived_prefix = row["reason"].startswith(("ARCHIVED_SEGMENT_PREFIX_", "ARCHIVED_SHIPPER_STATE_"))
        if not archived_prefix and keep_reason(row["rel"], scope=scope):
            skipped.append({"path": path, "why": "KEEP_GUARD_AT_EXECUTION"})
            continue
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            skipped.append({"path": path, "why": "ALREADY_ABSENT"})
            continue
        if (int(st.st_size), int(st.st_mtime_ns)) != (row["bytes"], row["mtime_ns"]):
            raise RuntimeError(f"{path} changed after planning; refusing (re-plan)")
        unlink(path)
        deleted.append(path)
        freed += row["bytes"]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = receipts_dir / plan_doc["epoch_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    listing = out_dir / f"deleted-{scope}-{stamp}.jsonl.gz"
    with gzip.open(listing, "wt", encoding="utf-8") as handle:
        for path in deleted:
            handle.write(json.dumps({"path": path}) + "\n")
    receipt = {"schema": RECEIPT_SCHEMA, "scope": scope, "epoch_id": plan_doc["epoch_id"],
               "certification_sha8": cert_sha8, "plan_sha256": plan_doc["plan_sha256"],
               "started_at_utc": data_epoch.utc_iso(started), "finished_at_utc": data_epoch.utc_iso(time.time()),
               "deleted_files": len(deleted), "freed_bytes": freed, "freed_gb": round(freed / 1e9, 3),
               "skipped": skipped[:200], "skipped_count": len(skipped), "by_root": plan_doc["by_root"],
               "kept_by_reason": plan_doc["kept_by_reason"], "deleted_listing": str(listing)}
    (out_dir / f"receipt-{scope}-{stamp}.json").write_text(json.dumps(receipt, indent=1, sort_keys=True), "utf-8")
    with open(receipts_dir / "receipts.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({k: v for k, v in receipt.items() if k not in ("skipped", "by_root", "kept_by_reason")},
                                sort_keys=True) + "\n")
    return receipt


# ------------------------------------------------------------------ CLI

def _summary(doc: dict, examples: int = 15) -> dict:
    out = {k: v for k, v in doc.items() if k != "candidates"}
    out["largest_candidates"] = sorted(doc["candidates"], key=lambda c: -c["bytes"])[:examples]
    return out


def build_plan(args, now: float) -> tuple[Plan, dict | None]:
    manifest = None
    if args.pre_start:
        epoch_id, start = PRE_START_LABEL, now
    elif args.simulate_now:
        epoch_id, start = args.epoch or "ce-00000000-dry-run", now
    else:
        if args.scope == "fly" and args.data_root:
            manifest = data_epoch.load_manifest(Path(args.data_root) / "runtime")
        elif args.manifest:
            manifest = data_epoch.load_manifest(args.manifest)
        if not manifest:
            raise SystemExit("NO_EPOCH_MANIFEST: declare the epoch first (or use --simulate-now for a preview)")
        if args.epoch and args.epoch != manifest["epoch_id"]:
            raise SystemExit(f"EPOCH_MISMATCH: manifest {manifest['epoch_id']} != --epoch {args.epoch}")
        epoch_id, start = manifest["epoch_id"], float(manifest["started_at_ts"])
    plan = Plan(args.scope, epoch_id, start, bool(args.simulate_now))
    if args.scope == "laptop":
        plan_laptop(plan)
    elif args.fly_files_json:
        files_doc = json.loads(Path(args.fly_files_json).read_text("utf-8"))
        ds = json.loads(Path(args.fly_data_size_json).read_text("utf-8")) if args.fly_data_size_json else None
        plan_fly_from_checkpoint(plan, files_doc, ds, now=now)
    else:
        plan_fly(plan, Path(args.data_root or "/app/data"), now=now)
    return plan, manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="clean-epoch-wipe", description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", default="plan", choices=("plan", "execute"))
    ap.add_argument("--scope", required=True, choices=("laptop", "fly"))
    ap.add_argument("--epoch", help="declared epoch id (required for execute)")
    ap.add_argument("--manifest", help="laptop: data_epoch.json (default: <mirror tree>/data_epoch.json)")
    ap.add_argument("--data-root", help="fly: volume root (/app/data)")
    ap.add_argument("--fly-files-json", help="preview the Fly plan from a /api/research-segments/<p>/files dump")
    ap.add_argument("--fly-data-size-json", help="with --fly-files-json: /api/data_size dump for volume totals")
    ap.add_argument("--simulate-now", action="store_true", help="preview: treat everything existing now as pre-epoch")
    ap.add_argument("--confirm", default="", help=f"{CONFIRM_PREFIX}:<epoch>:<certification sha8>")
    ap.add_argument("--expect-plan-sha256", default="")
    ap.add_argument("--certification", help="certification JSON (laptop default C:\\DoxxedCrypto\\clean-epoch\\certification.json)")
    ap.add_argument("--receipts-dir", help="where receipts go (laptop default C:\\DoxxedCrypto\\clean-epoch; fly <data-root>/runtime/clean-epoch-receipts)")
    ap.add_argument("--out", help="write the full plan JSON here")
    ap.add_argument("--pre-start", action="store_true",
                    help="plan/execute everything existing now while the bot is held down (no certification)")
    ap.add_argument("--health-url", help="pre-start hold check (default per scope)")
    args = ap.parse_args(argv)
    if args.pre_start and (args.simulate_now or args.fly_files_json):
        ap.error("--pre-start plans the live tree; it cannot be combined with previews")
    if args.scope == "laptop" and not args.manifest and not args.simulate_now and not args.pre_start:
        args.manifest = os.path.join(LAPTOP_ROOTS["mirror_tree"]["path"], data_epoch.MANIFEST_NAME)
    now = time.time()
    plan, manifest = build_plan(args, now)
    doc = plan.doc()
    if args.out:
        data_epoch.write_json_atomic(args.out, doc)
    if args.command == "plan":
        print(json.dumps({"dry_run": True, **_summary(doc)}, indent=1, sort_keys=True, default=str))
        return 0
    # ---- execute: every gate fails closed
    if args.pre_start:
        return _execute_pre_start(args, doc)
    if plan.simulated or not manifest:
        print(json.dumps({"error": "SIMULATED_PLAN_NOT_EXECUTABLE"}))
        return 2
    if args.scope == "fly" and args.fly_files_json:
        print(json.dumps({"error": "CHECKPOINT_PREVIEW_NOT_EXECUTABLE"}))
        return 2
    receipts = Path(args.receipts_dir or (LAPTOP_RECEIPTS if args.scope == "laptop"
                                          else os.path.join(args.data_root or "/app/data", "runtime",
                                                            "clean-epoch-receipts")))
    cert_sha8 = ""
    if args.scope == "laptop":
        cert_path = Path(args.certification or os.path.join(LAPTOP_RECEIPTS, "certification.json"))
        cert = _load_certification(cert_path)
        if not data_epoch.certified(cert, manifest["epoch_id"]):
            print(json.dumps({"error": "EPOCH_NOT_CERTIFIED", "certification": str(cert_path),
                              "status": (cert or {}).get("status")}))
            return 3
        cert_sha8 = hashlib.sha256(cert_path.read_bytes()).hexdigest()[:8]
        if _analyzer_cycle_running():
            print(json.dumps({"error": "ANALYZER_CYCLE_RUNNING", "hint": "run between analyzer cycles"}))
            return 4
    else:
        if now - float(manifest["started_at_ts"]) < data_epoch.CERTIFICATION_WINDOW_SEC:
            print(json.dumps({"error": "EPOCH_YOUNGER_THAN_CERTIFICATION_WINDOW"}))
            return 3
        parts = args.confirm.split(":")
        cert_sha8 = parts[2] if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{8}", parts[2]) else ""
    expected = _confirm_token(manifest["epoch_id"], cert_sha8) if cert_sha8 else None
    if not expected or args.confirm != expected:
        print(json.dumps({"error": "CONFIRMATION_REQUIRED", "expected_form": f"{CONFIRM_PREFIX}:<epoch>:<cert sha8>"}))
        return 5
    if args.expect_plan_sha256 != doc["plan_sha256"]:
        print(json.dumps({"error": "PLAN_SHA256_MISMATCH", "plan_sha256": doc["plan_sha256"]}))
        return 6
    receipt = execute(doc, receipts_dir=receipts, scope=args.scope, cert_sha8=cert_sha8)
    print(json.dumps({"executed": True, **receipt}, indent=1, sort_keys=True, default=str))
    return 0


def _execute_pre_start(args, doc: dict, fetch: Callable[[str], dict] | None = None) -> int:
    if args.confirm != pre_start_token(doc["plan_sha256"]):
        print(json.dumps({"error": "CONFIRMATION_REQUIRED", "expected_form": f"{PRE_START_PREFIX}:<plan sha256[:12]>"}))
        return 5
    if args.expect_plan_sha256 != doc["plan_sha256"]:
        print(json.dumps({"error": "PLAN_SHA256_MISMATCH", "plan_sha256": doc["plan_sha256"]}))
        return 6
    try:
        violations = held_down_violations((fetch or _fetch_health)(args.health_url or HELD_DOWN_URLS[args.scope]))
    except Exception as exc:  # noqa: BLE001 - any doubt fails closed
        violations = [f"health unavailable: {type(exc).__name__}"]
    if violations:
        print(json.dumps({"error": "BOT_NOT_HELD_DOWN", "violations": violations}))
        return 7
    if args.scope == "laptop" and _analyzer_cycle_running():
        print(json.dumps({"error": "ANALYZER_CYCLE_RUNNING", "hint": "stop the laptop chain first"}))
        return 4
    receipts = Path(args.receipts_dir or (LAPTOP_RECEIPTS if args.scope == "laptop"
                                          else os.path.join(args.data_root or "/app/data", "runtime",
                                                            "clean-epoch-receipts")))
    receipt = execute(doc, receipts_dir=receipts, scope=args.scope, cert_sha8=PRE_START_LABEL)
    print(json.dumps({"executed": True, **receipt}, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
