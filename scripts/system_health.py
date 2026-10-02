"""One aggregated, positive-progress health verdict for the whole BTC V3.1 stack.

Every check answers "is this subsystem making progress?", never "is a process
alive?". Each check carries status (GREEN/AMBER/RED/SKIP), the observed value,
its threshold, the last time it was GREEN, a likely-cause hint and a runbook
anchor in ``docs/SYSTEM_HEALTH_RUNBOOK.md``. The overall verdict is the worst
non-SKIP status.

Read-only by construction: it GETs public Fly/Railway endpoints, the
admin-authenticated Fly ``/api/state`` (read-only), the local analyzer API and
laptop files. It never pauses, resumes, arms, deploys, closes or prunes
anything. The only write towards Fly is the optional banner report
(``POST /api/system-health/report``), which stores a summary for the dashboard.

Usage:
  python scripts/system_health.py            # live evaluation, human summary
  python scripts/system_health.py --json     # same, full JSON
  python scripts/system_health.py --tick     # watcher tick: evaluate, alarm, publish
  python scripts/system_health.py --latest   # print the last published verdict

Exit codes: 0 GREEN, 1 AMBER, 2 RED.
"""
from __future__ import annotations

import argparse
import base64
import copy
import csv
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fly_platform_status as fly_platform_mod  # noqa: E402
import system_health_meta as meta_mod  # noqa: E402

SCHEMA = "system_health_v1"
ALARM_SCHEMA = "system_health_alarm_v1"
# Behaviour contracts this watcher build implements; the blindspot closure
# ledger only marks an item CLOSED-VERIFIED-LIVE when the live report lists it.
WATCHER_FEATURES = (
    "analyzer_reports", "analyzer_parity_strict", "pull_ack_no_none_green", "supervisor_process_check",
    "streams_analysed_freshness", "missing_data_not_green", "selfaware_engine", "cached_live_refresh",
    "fetch_failure_not_skip", "incident_stale_report",
    "parity_checker", "puller_lock", "chain_monitor_alerts", "incident_relay", "interim_status",
    "delivery_check", "fly_copy_lag", "wall_integrity", "adhoc_visibility", "check_dedupe", "amber_acks",
    "flapping", "pull_ack_run_telemetry", "epoch_parity_fields", "lifecycle_recent_red", "revision_master_ahead",
    "parity_timeout", "fly_platform_status",
)
GREEN, AMBER, RED, SKIP = "GREEN", "AMBER", "RED", "SKIP"
RANK = {SKIP: -1, GREEN: 0, AMBER: 1, RED: 2}
RUNBOOK = "docs/SYSTEM_HEALTH_RUNBOOK.md"

DEFAULT_STATE_DIR = r"C:\DoxxedCrypto\laptop-chain"
DEFAULT_ANALYZER_REPO = r"C:\DoxxedCrypto\v2c"
DEFAULT_MIRROR_TREE = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_SHADOW_ROOT = r"C:\DoxxedCrypto\fly-mirror-segments"
DEFAULT_EXPORTS = r"C:\DoxxedCrypto\analyzer-exports\latest"
DEFAULT_PROOF_DIR = r"C:\DoxxedCrypto\btc-v31-current\diagnostics"
DEFAULT_VAULT = r"C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\home-bot.env"
DEFAULT_RETENTION_DIR = r"C:\DoxxedCrypto\bot-data-retention"
DEFAULT_ARCHIVE_DIR = r"C:\DoxxedCrypto\analysis-archive"
FLY_URL = "https://doxed-btc-bot.fly.dev"
ANALYZER_URL = "http://127.0.0.1:9001"
SELFAWARE_URL = "http://127.0.0.1:9021"
SUPERVISOR_TASK = "DoxxedLaptopChainSupervisor"
SERVER_PORT = 9011
REPO = "danishhaiderau-maker/doxed-founders-website"

MIN = 60.0
HOUR = 3600.0

# Thresholds in seconds unless named otherwise. RED means "blocks trading,
# data custody or safety"; AMBER means "degraded, look soon".
THRESHOLDS: dict[str, float] = {
    "fly_unreachable_ticks": 2,
    "deploy_pause_max_sec": 45 * MIN,
    "ai_success_amber_sec": 6 * MIN,
    "ai_success_red_sec": 12 * MIN,
    "ai_consecutive_failures_red": 3,
    "ai_neutral_min_samples": 20,
    "ai_neutral_window_sec": 6 * HOUR,
    "orders_red_sec": 3 * HOUR,
    "tile_quiet_amber_sec": 6 * HOUR,
    "contradiction_red_window_sec": 2 * HOUR,
    "contradiction_amber_window_sec": 24 * HOUR,
    "ws_amber_sec": 60.0,
    "ws_red_sec": 120.0,
    # Idle gaps of up to ~14 min occur with a few MB of backlog even after #257,
    # so a plain gap is AMBER; an erroring shipper (PLAN_RACE every cycle) is RED
    # at 9 min and any backlog with no segment at 20 min.
    "shipper_stall_amber_sec": 9 * MIN,
    "shipper_stall_red_sec": 9 * MIN,
    "shipper_stall_backlog_red_sec": 20 * MIN,
    "shipper_status_red_sec": 10 * MIN,
    "unshipped_amber_bytes": 256 * 1024 * 1024,
    "pull_finished_red_sec": 15 * MIN,
    "pull_lag_seq_red": 30,
    "pull_lag_red_sec": 15 * MIN,
    "ack_lag_red_sec": 15 * MIN,
    "ack_lag_seq_red": 10,
    "analyzer_gen_amber_sec": 45 * MIN,
    "analyzer_gen_red_sec": 90 * MIN,
    "analyzer_api_down_red_sec": 20 * MIN,
    "cycle_amber_sec": 45 * MIN,
    "cycle_red_sec": 90 * MIN,
    "exports_amber_sec": 90 * MIN,
    "streams_stale_amber_sec": 15 * MIN,
    "registry_mismatch_red_sec": 30 * MIN,
    "relay_snapshot_amber_sec": 15 * MIN,
    "relay_heartbeat_amber_ms": 5 * MIN * 1000,
    "laptop_free_amber_bytes": 20 * 1024**3,
    "laptop_free_red_bytes": 5 * 1024**3,
    "fly_free_amber_bytes": 8 * 1024**3,
    "fly_free_red_bytes": 4 * 1024**3,
    "store_cap_amber_pct": 80.0,
    "store_cap_red_pct": 95.0,
    "retention_run_amber_sec": 3 * HOUR,
    "retention_run_red_sec": 12 * HOUR,
    "archive_snapshot_amber_sec": 3 * HOUR,
    "archive_snapshot_red_sec": 12 * HOUR,
    "proof_row_amber_sec": 45 * MIN,
    "supervisor_tick_red_sec": 15 * MIN,
    "watcher_stale_sec": 15 * MIN,
    "renotify_sec": 6 * HOUR,
    "tick_min_interval_sec": 240.0,
    "served_model_change_amber_sec": 6 * HOUR,
    "deepseek_balance_amber_usd": 5.0,
    "deepseek_balance_red_usd": 1.0,
    "deepseek_balance_cache_sec": 10 * MIN,
    "neon_usage_cache_sec": 15 * MIN,
    "neon_usage_error_cache_sec": 5 * MIN,
    "deepseek_balance_fly_max_age_sec": 30 * MIN,
    "analyzer_reports_red_sec": 45 * MIN,
    "analyzer_receipt_amber_sec": 30 * MIN,
    "analyzer_receipt_wall_amber_sec": 60 * MIN,
    "pull_applied_none_red_sec": 30 * MIN,
    "pull_exit_red_sec": 10 * MIN,
    "pull_loop_missing_red_sec": 10 * MIN,
    "source_down_amber_sec": 15 * MIN,
    "streams_content_lag_amber_sec": 60 * MIN,
    "selfaware_down_red_sec": 15 * MIN,
    "selfaware_stale_red_sec": 20 * MIN,
    "selfaware_job_late_factor": 3,
    "supervisor_probe_cache_sec": 60.0,
    "legacy_ack_stale_sec": HOUR,
}

# DeepSeek retired "deepseek-v4-flash" on 2026-10-01 and serves those requests
# as "deepseek-flash" (DeepSeek-V4.1-Flash). Used only when Fly does not yet
# report its configured model.
EXPECTED_DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"

# Checks that need N consecutive bad evaluations before an alarm opens (flap
# guard for single network blips). Default is 1.
SUSTAIN = {"fly.process": 2, "analyzer.api": 2, "railway.api": 2, "trading.orphans": 2, "fly.platform_status": 2}


# ---------------------------------------------------------------- utilities

def utcnow() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: Any) -> float | None:
    """Epoch seconds from an epoch number or an ISO-8601 string."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).strip().lstrip("\ufeff")
    try:
        number = float(text)
        return number if number > 0 else None
    except ValueError:
        pass
    text = re.sub(r"(\.\d{6})\d+", r"\1", text).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def read_json(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def fmt_age(sec: float | None) -> str:
    if sec is None:
        return "never"
    sec = max(0.0, float(sec))
    if sec < 120:
        return f"{sec:.0f}s"
    if sec < 2 * HOUR:
        return f"{sec / MIN:.0f}m"
    return f"{sec / HOUR:.1f}h"


def dig(obj: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(obj, Mapping):
            return default
        obj = obj.get(key)
    return default if obj is None else obj


def tail_csv(path: Path, max_bytes: int = 2_000_000) -> list[dict[str, str]]:
    """Rows from the last ``max_bytes`` of a CSV, keyed by the file's header."""
    try:
        with open(path, "rb") as handle:
            header = handle.readline().decode("utf-8-sig", "replace").strip("\r\n")
            size = handle.seek(0, os.SEEK_END)
            start = max(len(header) + 1, size - max_bytes)
            handle.seek(start)
            blob = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    if start > len(header) + 1:
        blob = blob.split("\n", 1)[1] if "\n" in blob else ""
    reader = csv.DictReader(io.StringIO(header + "\n" + blob))
    try:
        return [row for row in reader]
    except csv.Error:
        return []


def check(
    cid: str,
    subsystem: str,
    status: str,
    observed: Any,
    threshold: Any,
    hint: str = "",
    detail: str = "",
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "id": cid,
        "subsystem": subsystem,
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "hint": hint,
        "detail": detail,
        "runbook": f"{RUNBOOK}#{cid.replace('.', '-')}",
    }
    if fields:
        out["observed_fields"] = dict(fields)
    return out


_PARITY_OK = {"MATCH", "MATCHED", "OK", "TRUE", "GREEN", "PASS", "YES"}
_PARITY_UNKNOWN = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "PENDING"}


def parity_state(value: Any) -> bool | None:
    """True/False/None (unknown) for parity fields that may be bool, string ("MATCH") or a mapping."""
    if isinstance(value, Mapping):
        inner = value.get("match", value.get("ok", value.get("status")))
        return None if inner is None else parity_state(inner)
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in _PARITY_OK:
        return True
    if text in _PARITY_UNKNOWN:
        return None
    return False


def same_rev(a: Any, b: Any) -> bool | None:
    """Prefix compare of two git revisions (Fly reports 12 chars); None when either is unknown."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    if len(a) < 7 or len(b) < 7:
        return None
    return a.startswith(b) or b.startswith(a)


# ------------------------------------------------------------------ inputs

def load_vault(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def http_json(url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 20.0,
              method: str = "GET", body: Any = None) -> tuple[Any, str | None]:
    """(payload, error_code). Error codes are bounded; never echo upstream text."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", "User-Agent": "doxxed-system-health/1",
        **({"Content-Type": "application/json"} if data is not None else {}),
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return (json.loads(raw.decode("utf-8")) if raw else {}), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP_{exc.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        return None, "UNREACHABLE"
    except ValueError:
        return None, "BAD_JSON"


def _git(repo: str, *args: str, timeout: float = 20.0) -> str | None:
    try:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_registry(repo: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    """Active tile roster + signature from the canonical registry (cached per HEAD)."""
    head = _git(repo, "rev-parse", "HEAD")
    if head and cache.get("head") == head and cache.get("registry"):
        return cache["registry"]
    agent = Path(repo) / "services" / "btc-conservative-agent"
    code = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);import combo_pathway_config as c;"
        "print(json.dumps({'signature':c.active_tile_registry_signature(),'lanes':list(c.ACTIVE_TILE_ORDER)}))"
    )
    try:
        result = subprocess.run([sys.executable, "-c", code, str(agent)], capture_output=True,
                                text=True, timeout=90, check=False, cwd=str(agent))
        registry = json.loads(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        registry = None
    if registry:
        cache["head"], cache["registry"] = head, registry
    return registry


def collect_mirror(tree: Path, now: float) -> dict[str, Any]:
    """AI and order evidence from the laptop mirror (lags Fly by the shipper)."""
    out: dict[str, Any] = {"ai_success_ts": None, "ai_failure_ts": None, "ai_events": [],
                           "lane_orders": {}, "expired": {}, "filled": {}}
    for row in tail_csv(tree / "decisions_3factor.csv", 1_500_000):
        ts = parse_ts(row.get("ts"))
        if ts is None or now - ts > 24 * HOUR:
            continue
        if row.get("skip_stage") == "AI" and str(row.get("ai_error")).lower() != "true":
            out["ai_success_ts"] = max(out["ai_success_ts"] or 0.0, ts)
            out["ai_events"].append({"ts": ts, "ok": True, "direction": row.get("final_direction") or None})
        elif str(row.get("ai_error")).lower() == "true" or row.get("skip_stage") == "AI_ERROR":
            out["ai_failure_ts"] = max(out["ai_failure_ts"] or 0.0, ts)
    for row in tail_csv(tree / "ai_errors_3factor.csv", 300_000):
        ts = parse_ts(row.get("ts"))
        if ts is not None and now - ts <= 24 * HOUR:
            out["ai_failure_ts"] = max(out["ai_failure_ts"] or 0.0, ts)
            out["ai_events"].append({"ts": ts, "ok": False, "direction": None})
    for row in tail_csv(tree / "trades_3factor.csv", 3_000_000):
        tid, lane, ts = row.get("trade_id"), row.get("research_lane"), parse_ts(row.get("ts"))
        if not tid or ts is None:
            continue
        try:
            entry = ts - float(row.get("dur_min") or 0.0) * MIN
        except ValueError:
            entry = ts
        out["filled"][tid] = ts
        if lane:
            out["lane_orders"].setdefault(lane, []).append(entry)
    for row in tail_csv(tree / "expired_orders_3factor.csv", 1_000_000):
        tid, lane = row.get("trade_id"), row.get("research_lane")
        created = parse_ts(row.get("created_ts")) or parse_ts(row.get("time"))
        expired = parse_ts(row.get("expired_ts")) or parse_ts(row.get("time"))
        if tid and expired is not None:
            out["expired"][tid] = expired
        if lane and created is not None:
            out["lane_orders"].setdefault(lane, []).append(created)
    return out


def collect_exports(path: Path, now: float) -> dict[str, Any]:
    if not path.exists():
        return {"present": False}
    newest = None
    count = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                count += 1
                newest = max(newest or 0.0, entry.stat().st_mtime)
                if count > 5000:
                    break
    except OSError:
        pass
    manifest = None
    for name in ("manifest.json", "export-manifest.json", "latest.json"):
        manifest = read_json(path / name)
        if isinstance(manifest, dict):
            break
    generated = None
    if isinstance(manifest, dict):
        generated = parse_ts(manifest.get("generated_at") or manifest.get("generatedAt") or manifest.get("at"))
    client_status = None
    try:  # worker eed92197's analyzer_client, when present, is the authority.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import analyzer_client  # type: ignore  # noqa: PLC0415

        for name in ("exports_status", "export_status", "health"):
            func = getattr(analyzer_client, name, None)
            if callable(func):
                client_status = func()
                break
    except Exception:  # noqa: BLE001 - optional integration must never fail the tick
        client_status = None
    return {"present": True, "files": count, "newest_mtime": newest,
            "generated_at": generated or newest, "manifest": bool(manifest), "client_status": client_status}


def last_supervisor_tick(log_dir: Path) -> float | None:
    newest = None
    for log in sorted(log_dir.glob("laptop-chain-supervisor-*.log"))[-2:]:
        try:
            with open(log, "rb") as handle:
                handle.seek(max(0, handle.seek(0, os.SEEK_END) - 20_000))
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if " TICK " in line:
                ts = parse_ts(line.split(" ", 1)[0])
                if ts:
                    newest = max(newest or 0.0, ts)
    return newest


_PROBE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$t = Get-ScheduledTask -TaskName '__TASK__'
$i = if ($t) { $t | Get-ScheduledTaskInfo } else { $null }
$procs = @(Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%research-segment-pull-loop%' OR CommandLine LIKE '%laptop-chain-supervisor%' OR CommandLine LIKE '%laptop-ack-watcher%'")
function Pids([string]$pat) { ,@($procs | Where-Object { $_.CommandLine -like $pat } | ForEach-Object { [int]$_.ProcessId }) }
[pscustomobject]@{
  task_found = [bool]$t
  task_state = if ($t) { [string]$t.State } else { $null }
  last_run = if ($i -and $i.LastRunTime) { $i.LastRunTime.ToUniversalTime().ToString('o') } else { $null }
  last_result = if ($i) { [int64]$i.LastTaskResult } else { $null }
  pull_loop_pids = (Pids '*research-segment-pull-loop*')
  supervisor_pids = (Pids '*laptop-chain-supervisor*')
  ack_watcher_pids = (Pids '*laptop-ack-watcher*')
} | ConvertTo-Json -Compress
"""


def probe_supervisor(cache: dict[str, Any], now: float, *, ttl: float | None = None,
                     runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Scheduled-task state + live pull-loop/supervisor processes (cached; one PowerShell call)."""
    ttl = THRESHOLDS["supervisor_probe_cache_sec"] if ttl is None else ttl
    cached = cache.get("supervisor_probe")
    if isinstance(cached, Mapping) and now - float(cached.get("checked_at") or 0) < ttl:
        return dict(cached)
    out: dict[str, Any] = {"checked_at": now, "ok": False}
    if os.name != "nt" and runner is subprocess.run:
        out["error"] = "NOT_WINDOWS"
    else:
        # -EncodedCommand keeps the probe's own command line free of the patterns it searches for.
        encoded = base64.b64encode(_PROBE_SCRIPT.replace("__TASK__", SUPERVISOR_TASK).encode("utf-16-le")).decode()
        try:
            result = runner(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=45, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            payload = json.loads((result.stdout or "").strip().splitlines()[-1])
            for key in ("pull_loop_pids", "supervisor_pids", "ack_watcher_pids"):
                value = payload.get(key)
                payload[key] = [int(v) for v in (value if isinstance(value, list) else [value] if value else [])]
            out.update(payload, ok=True)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError, TypeError, AttributeError):
            out["error"] = "PROBE_FAILED"
    cache["supervisor_probe"] = out
    return dict(out)


def collect(opts: argparse.Namespace, state: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Gather every input. Each source fails independently into ``errors``."""
    now = utcnow() if now is None else now
    state_dir = Path(opts.state_dir)
    vault = load_vault(opts.vault)
    admin = os.environ.get("BOT_ADMIN_TOKEN") or vault.get("BOT_ADMIN_TOKEN")
    inputs: dict[str, Any] = {"now": now, "errors": {}}
    errors = inputs["errors"]

    def fetch(key: str, url: str, **kw: Any) -> None:
        payload, err = http_json(url, **kw)
        inputs[key] = payload
        if err:
            errors[key] = err

    fetch("fly_status", f"{opts.fly_url}/api/status", timeout=30)
    fetch("fly_health", f"{opts.fly_url}/health", timeout=30)
    if admin:
        fetch("fly_state", f"{opts.fly_url}/api/state", headers={"X-Bot-Admin-Token": admin}, timeout=60)
    else:
        inputs["fly_state"], errors["fly_state"] = None, "ADMIN_TOKEN_MISSING"
    fetch("analyzer_api", f"{opts.analyzer_url}/api/health", timeout=10)
    fetch("analyzer_status_api", f"{opts.analyzer_url}/api/status", timeout=15)
    fetch("analyzer_streams", f"{opts.analyzer_url}/api/streams/health", timeout=15)
    fetch("selfaware", f"{getattr(opts, 'selfaware_url', None) or SELFAWARE_URL}/api/selfaware/health", timeout=15)
    base = os.environ.get("PLATFORM_API_BASE_URL") or vault.get("PLATFORM_API_BASE_URL")
    if base:
        fetch("railway_health", f"{base.rstrip('/')}/health", timeout=20)
    else:
        inputs["railway_health"], errors["railway_health"] = None, "CONFIG_MISSING"

    shadow = Path(opts.shadow_root)
    files = {
        "pull_status": state_dir / "segment-pull.status.json",
        "head_snapshot": state_dir / "fly_segment_head_snapshot_v1.json",
        "relay_snapshot": state_dir / "relay_status_snapshot_v1.json",
        "runtime_snapshot": state_dir / "fly_runtime_snapshot_v1.json",
        "deploy_runs": state_dir / "fly_deploy_runs_snapshot_v1.json",
        "analyzer_status": state_dir / "analyzer-run.status.json",
        "cycle_status": state_dir / "segment-analyzer-cycle.status.json",
        "proof_active": state_dir / "unattended-proof" / "active.json",
        "puller_status": shadow / ".puller" / "status.json",
        # The puller rewrites status.json without seqs on a lock refusal; state.json keeps them.
        "puller_state": shadow / ".puller" / "state.json",
        "legacy_ack_watcher": state_dir / "laptop-ack-watcher.status.json",
    }
    for key, path in files.items():
        inputs[key] = read_json(path)
    inputs["legacy_ack_retired"] = (state_dir / "laptop-ack-watcher.RETIRED").exists()
    receipts = shadow / ".puller" / "ack-receipts.jsonl"
    try:
        with open(receipts, "rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - 4096))
            last = [l for l in handle.read().decode("utf-8", "replace").splitlines() if l.strip()][-1]
        inputs["ack_receipt"] = json.loads(last)
    except (OSError, ValueError, IndexError):
        inputs["ack_receipt"] = None
    retention_dir = Path(getattr(opts, "retention_dir", None) or DEFAULT_RETENTION_DIR)
    inputs["retention_last_run"] = read_json(retention_dir / "last-run.json")
    inputs["tier_a_health"] = (read_json(retention_dir / "status.json") or {}).get("tier_a")
    inputs["retention_exits"] = last_retention_exits(state_dir / "logs")
    reports = Path(opts.analyzer_repo) / ANALYZER_REPORTS_SUBDIR

    def analyzer_report(name: str) -> Any:
        return read_analyzer_report(reports, name)

    inputs["analyzer_receipt"] = analyzer_report("analyzer_generation_receipt.json")
    inputs["analyzer_integrity"] = analyzer_report("analyzer_integrity_report.json")
    inputs["analyzer_manifest_generated_at"] = (analyzer_report("report_manifest.json") or {}).get("generated_at")
    inputs["data_health_report"] = analyzer_report("data_health_report.json")
    inputs["ledger_reconciliation"] = analyzer_report("ledger_reconciliation.json")
    inputs["archive_last_snapshot"] = last_jsonl_row(
        Path(getattr(opts, "archive_dir", None) or DEFAULT_ARCHIVE_DIR) / "index.jsonl")
    inputs["proof_last_row"] = latest_proof_row(inputs.get("proof_active"), Path(opts.proof_dir))
    inputs["supervisor_tick_at"] = last_supervisor_tick(state_dir / "logs")

    inputs["mirror"] = collect_mirror(Path(opts.mirror_tree), now)
    inputs["mirror_trades"] = read_ledger_rows(Path(opts.mirror_tree) / "trades_3factor.csv")
    inputs["exports"] = collect_exports(Path(opts.exports), now)
    cache = state.setdefault("cache", {})
    inputs["supervisor_probe"] = probe_supervisor(cache, now)
    inputs["registry"] = collect_registry(opts.analyzer_repo, cache.setdefault("registry", {}))
    inputs["analyzer_head"] = _git(opts.analyzer_repo, "rev-parse", "HEAD")
    fly_rev = str(dig(inputs.get("fly_status"), "git_rev") or dig(inputs.get("fly_health"), "git_rev") or "")
    if fly_rev and inputs["analyzer_head"]:
        # Same rule as run-segment-analyzer-cycle.ps1: laptop-only merges on top of
        # the deployed revision are fine.
        inputs["analyzer_contains_fly"] = _git(opts.analyzer_repo, "merge-base", "--is-ancestor",
                                               fly_rev, "HEAD") is not None
    master = cache.get("master") or {}
    if now - float(master.get("at") or 0) > 10 * MIN:
        line = _git(opts.analyzer_repo, "ls-remote", "origin", "refs/heads/master", timeout=30)
        if line:
            master = {"at": now, "sha": line.split()[0]}
            cache["master"] = master
    inputs["master_sha"] = master.get("sha")
    try:
        usage = shutil.disk_usage(Path(opts.state_dir).anchor or "C:\\")
        inputs["laptop_disk"] = {"free": usage.free, "total": usage.total}
    except OSError:
        inputs["laptop_disk"] = None
    inputs["neon"] = collect_neon(cache, opts.state_dir, now)
    inputs["deepseek_balance"] = collect_deepseek_balance(vault, cache, now)
    inputs["fly_platform"] = fly_platform_mod.cached_snapshot(cache.setdefault("fly_platform", {}), now)
    return inputs


ANALYZER_REPORTS_SUBDIR = Path("services") / "btc-conservative-agent" / "canonical-research-data" / "analyzer" / "reports"


def read_analyzer_report(reports: Path, name: str) -> Any:
    # The generation (manifest, receipt, reconciliation) is written to the analyzer
    # dir; reports/ only mirrors part of it.
    found = read_json(reports.parent / name)
    return found if found is not None else read_json(reports / name)
_RETENTION_EXIT = re.compile(r"^(\S+) pid=\d+ RETENTION exit=(-?\d+)\s*(.*)$")
_AGENT_DIR = Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"


_LEDGER_FIELDS = ("trade_id", "research_lane", "epoch_id", "close_ts", "ts", "net_pnl_usd")


def read_ledger_rows(path: Path) -> list[dict[str, str]] | None:
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as handle:
            return [{k: row.get(k) for k in _LEDGER_FIELDS} for row in csv.DictReader(handle)]
    except OSError:
        return None


def _ledger_reconciliation_module():
    """The comparison logic ships with the analyzer, from this same checkout."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ledger_reconciliation", _AGENT_DIR / "ledger_reconciliation.py")
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, SyntaxError):
        return None


def last_retention_exits(log_dir: Path, limit: int = 3) -> list[dict[str, Any]]:
    """Newest-last RETENTION exit codes from the cycle logs (last-run.json only records successes)."""
    rows: list[dict[str, Any]] = []
    for log in sorted(log_dir.glob("segment-analyzer-cycle-*.log"))[-2:]:
        try:
            with open(log, "rb") as handle:
                handle.seek(max(0, handle.seek(0, os.SEEK_END) - 200_000))
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _RETENTION_EXIT.match(line.strip())
            if m:
                rows.append({"at": parse_ts(m.group(1)), "exit": int(m.group(2)), "detail": m.group(3)[:200]})
    return rows[-limit:]


def last_jsonl_row(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - 16384))
            last = [l for l in handle.read().decode("utf-8", "replace").splitlines() if l.strip()][-1]
        row = json.loads(last)
        return row if isinstance(row, dict) else None
    except (OSError, ValueError, IndexError):
        return None


def parse_deepseek_balance(payload: Any, err: str | None, now: float, source: str) -> dict[str, Any]:
    """Bounded summary of GET /user/balance; upstream text is never echoed."""
    out: dict[str, Any] = {"checked_at": now, "source": source}
    if err:
        return {**out, "error": err}
    infos = dig(payload, "balance_infos", default=[]) or []
    usd = next((i for i in infos if isinstance(i, Mapping) and str(i.get("currency")).upper() == "USD"), None)
    try:
        total = float(usd["total_balance"]) if usd else None
    except (KeyError, TypeError, ValueError):
        total = None
    if total is None:
        return {**out, "error": "NO_USD_BALANCE"}
    return {**out, "total_usd": total, "is_available": bool(dig(payload, "is_available", default=False))}


def collect_deepseek_balance(vault: Mapping[str, str], cache: dict[str, Any], now: float) -> dict[str, Any]:
    """Read-only balance GET, cached. The key goes only into the request header."""
    cached = cache.get("deepseek_balance")
    if isinstance(cached, Mapping) and now - float(cached.get("checked_at") or 0) < THRESHOLDS["deepseek_balance_cache_sec"]:
        return dict(cached)
    key = os.environ.get("DEEPSEEK_API_KEY") or vault.get("DEEPSEEK_API_KEY")
    if not key:
        return {"checked_at": now, "source": "laptop", "error": "KEY_MISSING"}
    payload, err = http_json(DEEPSEEK_BALANCE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    result = parse_deepseek_balance(payload, err, now, "laptop")
    cache["deepseek_balance"] = result
    return result


def neon_config(state_dir: str) -> dict[str, str]:
    """NEON_API_KEY / NEON_PROJECT_ID from the environment, else ``<state>/health/neon.env`` (never in git)."""
    file_values = load_vault(str(Path(state_dir) / "health" / "neon.env"))
    return {name: os.environ.get(name) or file_values.get(name) or ""
            for name in ("NEON_API_KEY", "NEON_PROJECT_ID", "NEON_ORG_ID", "NEON_EGRESS_BUDGET_BYTES_PER_HOUR")}


NEON_API = "https://console.neon.tech/api/v2"
# Usage-based plans (Launch/Scale) leave the project object's consumption counters at 0;
# only /consumption_history/v2 reports invoice-aligned usage.
NEON_METRICS = ("compute_unit_seconds", "root_branch_bytes_month", "child_branch_bytes_month",
                "instant_restore_bytes_month", "snapshot_storage_bytes_month",
                "public_network_transfer_bytes", "private_network_transfer_bytes", "extra_branches_month")
# Launch list prices (neon.com/docs/introduction/plans); extra branches are not priced here.
NEON_LAUNCH_RATES = {"cu_hour": 0.106, "storage_gb_month": 0.35, "instant_restore_gb_month": 0.20,
                     "snapshot_gb_month": 0.09, "egress_gb": 0.10, "egress_included_gb": 500.0}
NEON_ERROR_HINTS = {"HTTP_401": "API key rejected", "HTTP_403": "consumption API not available on this plan",
                    "HTTP_404": "org not accessible with this key", "HTTP_406": "time range rejected",
                    "HTTP_429": "rate limited"}


def _neon_buckets(payload: Any, project: str) -> Iterable[tuple[str, float, float, dict[str, float]]]:
    for proj in dig(payload, "projects", default=[]) or []:
        if not isinstance(proj, Mapping) or proj.get("project_id") != project:
            continue
        for period in proj.get("periods") or []:
            for bucket in (period.get("consumption") or []) if isinstance(period, Mapping) else []:
                start, end = parse_ts(bucket.get("timeframe_start")), parse_ts(bucket.get("timeframe_end"))
                if start is None or end is None:
                    continue
                values: dict[str, float] = {}
                for metric in bucket.get("metrics") or []:
                    try:
                        values[str(metric["metric_name"])] = float(metric.get("value") or 0)
                    except (KeyError, TypeError, ValueError, AttributeError):
                        continue
                yield str(period.get("period_plan") or ""), start, end, values


def summarize_neon_usage(daily: Any, hourly: Any, project: str, now: float) -> dict[str, Any]:
    """Month-to-date totals (completed days from ``daily`` + today from ``hourly``) and the last complete hour."""
    hour = datetime.fromtimestamp(now, timezone.utc).replace(minute=0, second=0, microsecond=0)
    month_start = hour.replace(day=1, hour=0).timestamp()
    day_start = hour.replace(hour=0).timestamp()
    totals = {name: 0.0 for name in NEON_METRICS}
    plans: set[str] = set()
    used = 0
    for source, keep in ((daily, lambda s: month_start <= s < day_start), (hourly, lambda s: s >= day_start)):
        for plan, start, _end, values in _neon_buckets(source, project):
            if keep(start):
                used += 1
                plans.add(plan)
                for name in NEON_METRICS:
                    totals[name] += values.get(name, 0.0)
    complete = sorted((start, values) for _p, start, end, values in _neon_buckets(hourly, project)
                      if end <= now and values)
    out: dict[str, Any] = {
        "month_start": iso(month_start), "buckets": used, "plan": ",".join(sorted(p for p in plans if p)) or None,
        "egress_bytes": totals["public_network_transfer_bytes"],
        "private_egress_bytes": totals["private_network_transfer_bytes"],
        "compute_cu_hours": totals["compute_unit_seconds"] / HOUR,
        "storage_gb_month": (totals["root_branch_bytes_month"] + totals["child_branch_bytes_month"]) / 1e9,
        "instant_restore_gb_month": totals["instant_restore_bytes_month"] / 1e9,
        "snapshot_gb_month": totals["snapshot_storage_bytes_month"] / 1e9,
        "extra_branches_month": totals["extra_branches_month"],
        "rate_bytes_per_hour": complete[-1][1].get("public_network_transfer_bytes", 0.0) if complete else None,
        "rate_hour": iso(complete[-1][0]) if complete else None,
    }
    if out["plan"] == "launch":
        r = NEON_LAUNCH_RATES
        out["est_cost_usd"] = round(
            out["compute_cu_hours"] * r["cu_hour"] + out["storage_gb_month"] * r["storage_gb_month"]
            + out["instant_restore_gb_month"] * r["instant_restore_gb_month"]
            + out["snapshot_gb_month"] * r["snapshot_gb_month"]
            + max(0.0, out["egress_bytes"] / 1e9 - r["egress_included_gb"]) * r["egress_gb"], 2)
    return out


def collect_neon(cache: dict[str, Any], state_dir: str = DEFAULT_STATE_DIR,
                 now: float | None = None) -> dict[str, Any] | None:
    """Neon usage from the consumption-history API, only when NEON_API_KEY + NEON_PROJECT_ID are configured.

    Cached for ``neon_usage_cache_sec`` (Neon refreshes consumption ~every 15 min). The key goes only
    into the request header and never into the returned summary.
    """
    cfg = neon_config(state_dir)
    key, project = cfg["NEON_API_KEY"], cfg["NEON_PROJECT_ID"]
    if not key or not project:
        return {"missing": [n for n in ("NEON_API_KEY", "NEON_PROJECT_ID") if not cfg[n]]} \
            if (key or project) else None
    now = time.time() if now is None else float(now)
    cached = cache.get("neon_usage")
    if isinstance(cached, Mapping) and cached.get("project") == project:
        ttl = THRESHOLDS["neon_usage_error_cache_sec" if cached.get("error") else "neon_usage_cache_sec"]
        if now - float(cached.get("checked_at") or 0) < ttl:
            return dict(cached)
    headers = {"Authorization": f"Bearer {key}"}
    base: dict[str, Any] = {"checked_at": now, "project": project}
    if cfg["NEON_EGRESS_BUDGET_BYTES_PER_HOUR"]:
        base["budget_bytes_per_hour"] = cfg["NEON_EGRESS_BUDGET_BYTES_PER_HOUR"]

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        cache["neon_usage"] = result
        return result

    org = cfg["NEON_ORG_ID"] or (cached.get("org_id") if isinstance(cached, Mapping)
                                 and cached.get("project") == project else None)
    if not org:
        payload, err = http_json(f"{NEON_API}/projects/{project}", headers=headers, timeout=20)
        org = dig(payload, "project", "org_id") if not err else None
        if not org:
            return finish({**base, "error": err or "NO_ORG_ID", "endpoint": "project"})
    base["org_id"] = org
    hour = datetime.fromtimestamp(now, timezone.utc).replace(minute=0, second=0, microsecond=0)
    month_start, day_start = hour.replace(day=1, hour=0), hour.replace(hour=0)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")

    def fetch(granularity: str, start: datetime, end: datetime) -> tuple[Any, str | None]:
        query = urllib.parse.urlencode({"project_ids": project, "org_id": org, "granularity": granularity,
                                        "from": fmt(start), "to": fmt(end), "metrics": ",".join(NEON_METRICS)})
        return http_json(f"{NEON_API}/consumption_history/v2/projects?{query}", headers=headers, timeout=20)

    hourly, err = fetch("hourly", min(day_start, hour - timedelta(hours=3)), hour + timedelta(hours=1))
    if err:
        return finish({**base, "error": err, "endpoint": "consumption_history"})
    daily = None
    if day_start > month_start:
        daily, err = fetch("daily", month_start, day_start)
        if err:
            return finish({**base, "error": err, "endpoint": "consumption_history"})
    return finish({**base, **summarize_neon_usage(daily, hourly, project, now)})


def latest_proof_row(active: Any, proof_dir: Path) -> dict[str, Any] | None:
    receipt = Path(active["receipt"]) if isinstance(active, dict) and active.get("receipt") else None
    if receipt is None or not receipt.exists():
        candidates = sorted(proof_dir.glob("unattended-proof-*.jsonl"))
        receipt = candidates[-1] if candidates else None
    if receipt is None:
        return None
    try:
        with open(receipt, "rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - 20_000))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "ROW":
            return row
    return None


# --------------------------------------------------------------- evaluation

def _ai_provider_health(status: Any, state: Any) -> dict[str, Any] | None:
    """Provider-truth block from the AI repair (c609373e) when Fly exposes it."""
    for source in (dig(status, "strategy_progress"), status, state, dig(state, "strategy_progress")):
        block = dig(source, "ai_provider_health")
        if isinstance(block, Mapping):
            return dict(block)
    return None


TRACKED_SOURCES = ("fly_status", "fly_health", "fly_state", "analyzer_api", "analyzer_status_api",
                   "analyzer_streams", "selfaware", "railway_health")
# A SKIP caused by a fetch failure (429, timeout, missing token) may hide a real failure;
# once its source has failed for ``source_down_amber_sec`` the check is AMBER instead.
SKIP_SOURCES: Mapping[str, tuple[str, ...]] = {
    "fly.revision": ("fly_status", "fly_health"),
    "ai.success": ("fly_status", "fly_health"),
    "ai.served_model": ("fly_status", "fly_health"),
    "trading.orders": ("fly_state", "fly_status", "fly_health"),
    "trading.orphans": ("fly_state",),
    "ws.ticks": ("fly_status", "fly_health"),
    "shipper.progress": ("fly_status", "fly_health"),
    "streams.coverage": ("fly_status",),
    "dashboards.parity": ("fly_status",),
}


def _fail_open_guard(checks: list[dict[str, Any]], errors: Mapping[str, Any], down: Mapping[str, float],
                     now: float, t: Mapping[str, float]) -> dict[str, Any]:
    """Turn fetch-caused SKIPs AMBER after a sustained outage and summarize every failing source."""
    limit = t["source_down_amber_sec"]
    long_down = {k: now - float(v) for k, v in down.items() if now - float(v) > limit}
    for c in checks:
        if c["status"] != SKIP:
            continue
        hit = [k for k in SKIP_SOURCES.get(c["id"], ()) if k in long_down]
        if hit:
            c["status"] = AMBER
            c["observed"] = (f"cannot fetch {', '.join(f'{k} ({errors.get(k)})' for k in hit)} for "
                             f"{fmt_age(max(long_down[k] for k in hit))}; {c['observed']}")
            c["hint"] = c["hint"] or "input unavailable too long to trust a SKIP (rate limit, token, outage)"
    parts = [f"{k}={errors.get(k)} for {fmt_age(now - float(v))}" for k, v in sorted(down.items())]
    st = AMBER if long_down else GREEN
    return check("watcher.sources", "watcher", st,
                 "; ".join(parts) or f"all {len(TRACKED_SOURCES)} sources answered",
                 f"no source failing for more than {fmt_age(limit)}",
                 "" if st == GREEN else "rate limited (HTTP_429), token missing, or endpoint down; dependent checks "
                                        "are blind", fields={"failing_sources": sorted(long_down)})


def evaluate(inputs: Mapping[str, Any], state: dict[str, Any], thresholds: Mapping[str, float] | None = None
             ) -> list[dict[str, Any]]:
    """Pure evaluation of every check. ``state`` carries progress memory."""
    t = {**THRESHOLDS, **(thresholds or {})}
    now = float(inputs["now"])
    errors = inputs.get("errors") or {}
    mem = state.setdefault("memory", {})
    status = inputs.get("fly_status") if isinstance(inputs.get("fly_status"), Mapping) else None
    health = inputs.get("fly_health") if isinstance(inputs.get("fly_health"), Mapping) else None
    fstate = inputs.get("fly_state") if isinstance(inputs.get("fly_state"), Mapping) else None
    progress = dig(status, "strategy_progress", default={})
    paused = bool(dig(status, "execution_paused", default=dig(health, "execution_paused", default=False)))
    pause_owner = dig(status, "pause_owner", default=dig(health, "pause_owner")) or ""
    checks: list[dict[str, Any]] = []
    add = checks.append
    source_down = mem.setdefault("source_down", {})
    for key in TRACKED_SOURCES:
        err = errors.get(key)
        if err and not (key == "railway_health" and err == "CONFIG_MISSING"):
            source_down.setdefault(key, now)
        else:
            source_down.pop(key, None)

    # ---------------- Fly
    reachable = status is not None and health is not None
    if reachable:
        mem["fly_unreachable_ticks"] = 0
        alive = bool(dig(status, "process_alive", default=True))
        ready = dig(status, "system_ready")
        st = GREEN if alive else RED
        add(check("fly.process", "fly", st, f"alive={alive} system_ready={ready} rev={dig(status, 'git_rev')}",
                  "process_alive and /api/status + /health answer",
                  "" if alive else "Fly process reports not alive"))
    else:
        mem["fly_unreachable_ticks"] = int(mem.get("fly_unreachable_ticks") or 0) + 1
        add(check("fly.process", "fly", RED, f"unreachable ({errors.get('fly_status') or errors.get('fly_health')})",
                  "/api/status and /health answer",
                  "Fly app down, restarting, or the laptop is offline"))

    if reachable:
        if paused:
            since = mem.setdefault("paused_since", now)
            age = now - float(since)
            deploy = str(pause_owner).upper() == "DEPLOY_MAINTENANCE"
            st = AMBER if deploy and age <= t["deploy_pause_max_sec"] else RED
            add(check("fly.paused", "fly", st, f"paused owner={pause_owner or 'none'} for {fmt_age(age)}",
                      f"unpaused; DEPLOY_MAINTENANCE <= {fmt_age(t['deploy_pause_max_sec'])}",
                      "guarded deploy boundary" if deploy else
                      "safety/admin pause (THREAD_CRASH, SAFETY, ADMIN_MANUAL) or a deploy that never resumed",
                      dig(status, "execution_reason", default="")))
        else:
            mem.pop("paused_since", None)
            add(check("fly.paused", "fly", GREEN, "paper running (execution_paused=false)", "unpaused"))

    fly_rev = str(dig(status, "git_rev") or dig(health, "git_rev") or "")
    master = str(inputs.get("master_sha") or "")
    runs = dig(inputs.get("deploy_runs"), "runs", default=[]) or []
    runs = sorted([r for r in runs if isinstance(r, Mapping)], key=lambda r: str(r.get("createdAt")), reverse=True)
    last_run = runs[0] if runs else {}
    failed = last_run.get("status") == "completed" and last_run.get("conclusion") not in (None, "success")
    if not fly_rev:
        add(check("fly.revision", "fly", SKIP if not reachable else AMBER, "unknown", "Fly git_rev reported"))
    else:
        same = bool(master) and master.startswith(fly_rev)
        obs = f"fly={fly_rev[:12]} master={master[:12] or '?'}{' (current)' if same else ' (master ahead; deploy queued)'}"
        if failed:
            obs += f"; last guarded deploy {last_run.get('databaseId')} concluded {last_run.get('conclusion')}"
        add(check("fly.revision", "fly", AMBER if failed else GREEN, obs,
                  "Fly runs a successfully deployed master revision",
                  "last guarded deploy failed; Fly still on the previous revision" if failed else ""))

    # ---------------- AI
    prov = _ai_provider_health(status, fstate)
    history = dig(fstate, "ai_history", default=[]) or []
    events = mem.setdefault("ai_events", [])
    seen = {e["ts"] for e in events}
    for entry in history:
        ts = parse_ts(entry.get("time") or entry.get("ts"))
        if ts is None or ts in seen:
            continue
        events.append({"ts": ts, "ok": not bool(entry.get("ai_error")),
                       "direction": entry.get("ai_direction_raw") or entry.get("final_direction")})
        seen.add(ts)
    mirror = inputs.get("mirror") or {}
    for entry in mirror.get("ai_events") or []:
        if entry["ts"] not in seen:
            events.append(dict(entry))
            seen.add(entry["ts"])
    events.sort(key=lambda e: e["ts"])
    del events[:-400]
    events[:] = [e for e in events if now - e["ts"] <= 24 * HOUR]
    success_ts = [e["ts"] for e in events if e["ok"]]
    candidates = [mem.get("ai_last_success_ts"), mirror.get("ai_success_ts"), max(success_ts) if success_ts else None,
                  parse_ts(dig(prov, "last_ai_success_ts") or dig(prov, "last_success_ts"))]
    last_success = max([c for c in candidates if c] or [0.0]) or None
    if last_success:
        mem["ai_last_success_ts"] = last_success
    trailing_failures = 0
    for e in reversed(events):
        if e["ok"]:
            break
        trailing_failures += 1
    if prov and prov.get("consecutive_failures") is not None:
        trailing_failures = max(trailing_failures, int(prov.get("consecutive_failures") or 0))
    startup_age = float(dig(progress, "process_startup_age_sec", default=1e9) or 1e9)
    ai_expected = reachable and not paused and startup_age > t["ai_success_red_sec"]
    age = (now - last_success) if last_success else None
    if not reachable:
        add(check("ai.success", "ai", SKIP, "Fly unreachable", f"success within {fmt_age(t['ai_success_red_sec'])}"))
    else:
        st = GREEN
        if age is None or age > t["ai_success_red_sec"]:
            st = RED if ai_expected else AMBER
        elif age > t["ai_success_amber_sec"]:
            st = AMBER
        attempt_age = dig(progress, "ai_age_sec")
        add(check("ai.success", "ai", st,
                  f"last SUCCESSFUL model response {fmt_age(age)} ago (last attempt {fmt_age(attempt_age)} ago)",
                  f"<= {fmt_age(t['ai_success_amber_sec'])} AMBER / {fmt_age(t['ai_success_red_sec'])} RED",
                  "" if st == GREEN else
                  "DeepSeek calls timing out/failing (attempts still advance ai_age); check egress, key, provider status",
                  f"provider_health={'present' if prov else 'not exposed yet (c609373e)'}"))
        st = RED if trailing_failures >= t["ai_consecutive_failures_red"] else AMBER if trailing_failures else GREEN
        add(check("ai.failures", "ai", st, f"{trailing_failures} consecutive failed AI calls",
                  f"< {int(t['ai_consecutive_failures_red'])}",
                  "" if st == GREEN else "provider errors (ReadTimeout/HTTP) on consecutive calls"))
    window = [e for e in events if e["ok"] and now - e["ts"] <= t["ai_neutral_window_sec"] and e.get("direction")]
    neutral = [e for e in window if str(e["direction"]).upper() in {"NO_TRADE", "NEUTRAL", "HOLD", "NONE"}]
    if len(window) < t["ai_neutral_min_samples"]:
        add(check("ai.decision_mix", "ai", SKIP,
                  f"{len(neutral)}/{len(window)} neutral in {fmt_age(t['ai_neutral_window_sec'])} "
                  f"(below {int(t['ai_neutral_min_samples'])} samples; not rated)",
                  f"not 100% neutral over >= {int(t['ai_neutral_min_samples'])} responses"))
    else:
        all_neutral = len(neutral) == len(window)
        add(check("ai.decision_mix", "ai", AMBER if all_neutral else GREEN,
                  f"{len(neutral)}/{len(window)} neutral in {fmt_age(t['ai_neutral_window_sec'])}",
                  "not 100% neutral",
                  "model returns only NO_TRADE: prompt/parse regression or dead inputs" if all_neutral else ""))

    echo = str(dig(prov, "last_model_echo") or "") or None
    fingerprint = dig(prov, "last_system_fingerprint")
    configured = str(dig(prov, "configured_model") or "") or EXPECTED_DEEPSEEK_MODEL
    previous = mem.get("ai_served_model")
    if echo and previous and echo != previous:
        mem["ai_served_model_changed_at"] = now
        mem["ai_served_model_previous"] = previous
    if echo:
        mem["ai_served_model"] = echo
    changed_at = mem.get("ai_served_model_changed_at")
    change_from = mem.get("ai_served_model_previous")
    for change in dig(prov, "served_model_changes", default=[]) or []:
        at = parse_ts(dig(change, "at"))
        if at and (changed_at is None or at > float(changed_at)):
            changed_at, change_from = at, dig(change, "from")
    recent_change = changed_at is not None and now - float(changed_at) <= t["served_model_change_amber_sec"]
    if not reachable or not echo:
        add(check("ai.served_model", "ai", SKIP, "Fly unreachable" if not reachable else "no served model echoed yet",
                  f"served model == configured ({configured})"))
    else:
        st = AMBER if echo != configured or recent_change else GREEN
        obs = f"served={echo} configured={configured} fingerprint={fingerprint or '?'}"
        if recent_change:
            obs += f"; changed {change_from}->{echo} at {iso(float(changed_at))}"
        add(check("ai.served_model", "ai", st, obs,
                  f"served == configured, no change within {fmt_age(t['served_model_change_amber_sec'])}",
                  "" if st == GREEN else
                  "DeepSeek is serving a different model than configured (silent alias/retirement); "
                  "analyzer splits cohorts by served model"))

    fly_balance = dig(prov, "deepseek_balance")
    balance = inputs.get("deepseek_balance") if isinstance(inputs.get("deepseek_balance"), Mapping) else None
    if (isinstance(fly_balance, Mapping) and fly_balance.get("total_usd") is not None
            and now - (parse_ts(fly_balance.get("checked_at")) or 0) <= t["deepseek_balance_fly_max_age_sec"]):
        balance = {**fly_balance, "source": "fly"}
    threshold = f">= ${t['deepseek_balance_amber_usd']:.2f} (AMBER < ${t['deepseek_balance_amber_usd']:.2f}, RED < ${t['deepseek_balance_red_usd']:.2f})"
    if not balance or balance.get("error") == "KEY_MISSING":
        add(check("deepseek.balance", "ai", SKIP, "balance not observed (no key on laptop, not exposed by Fly)", threshold))
    elif balance.get("total_usd") is None:
        add(check("deepseek.balance", "ai", AMBER, f"balance unknown ({balance.get('error')})", threshold,
                  "DeepSeek /user/balance unreachable or changed shape"))
    else:
        total = float(balance["total_usd"])
        available = balance.get("is_available") is not False
        st = (RED if total < t["deepseek_balance_red_usd"] or not available
              else AMBER if total < t["deepseek_balance_amber_usd"] else GREEN)
        add(check("deepseek.balance", "ai", st,
                  f"${total:.2f} USD is_available={available} (source={balance.get('source')})", threshold,
                  "" if st == GREEN else "top up DeepSeek; at $0 every AI call fails with HTTP 402"))

    # ---------------- Trading
    toggle_sources = [dig(fstate, "research_lane_enabled"),
                      dig(inputs.get("runtime_snapshot"), "research_lane_enabled")]
    toggle_maps = [m for m in toggle_sources if isinstance(m, Mapping)]
    toggles_known = bool(toggle_maps)
    toggles = next((m for m in toggle_maps if m), toggle_maps[0] if toggle_maps else {})
    on_lanes = sorted(k for k, v in toggles.items() if v is True)
    route_counts = dig(fstate, "tile_route_counts", default={}) or {}
    instance = str(dig(status, "bot_instance_id") or "")
    lane_mem = mem.setdefault("lanes", {})
    lane_evidence = {lane: list(ts) for lane, ts in (mirror.get("lane_orders") or {}).items()}
    for item in (dig(fstate, "trades", default=[]) or []):
        lane, ts = item.get("research_lane"), parse_ts(item.get("ts"))
        if lane and ts:
            lane_evidence.setdefault(lane, []).append(ts - float(item.get("dur_min") or 0) * MIN)
    for item in (dig(fstate, "positions", default=[]) or []) + (dig(fstate, "orders", default=[]) or []):
        lane = item.get("research_lane")
        ts = parse_ts(item.get("order_created_ts") or item.get("created_ts") or item.get("entry_ts"))
        if lane and ts:
            lane_evidence.setdefault(lane, []).append(ts)
    for lane in set(on_lanes) | set(route_counts):
        lm = lane_mem.setdefault(lane, {})
        counts = route_counts.get(lane) or {}
        total = sum(int(counts.get(k) or 0) for k in ("closed", "expired", "open", "pending"))
        if lm.get("instance") == instance and total > int(lm.get("total") or 0):
            lm["last_order_ts"] = now
        if lm.get("instance") != instance or total != lm.get("total"):
            lm["instance"], lm["total"] = instance, total
        ev = [x for x in lane_evidence.get(lane, []) if x and x <= now]
        if ev:
            lm["last_order_ts"] = max(float(lm.get("last_order_ts") or 0.0), max(ev))
        lm["orders_48h"] = len([x for x in lane_evidence.get(lane, []) if now - x <= 48 * HOUR])
    if not reachable or fstate is None:
        add(check("trading.orders", "trading", SKIP, f"no /api/state ({errors.get('fly_state')})",
                  f"an order within {fmt_age(t['orders_red_sec'])} while tiles ON"))
    elif not toggles_known:
        add(check("trading.orders", "trading", AMBER,
                  "toggle state unavailable (no research_lane_enabled in /api/state or the runtime snapshot)",
                  f"an order within {fmt_age(t['orders_red_sec'])} while tiles ON",
                  "cannot tell which tiles are ON; /api/state shape changed or the runtime snapshot is missing"))
    elif not on_lanes:
        add(check("trading.orders", "trading", GREEN, "all tiles OFF (no orders expected)",
                  f"an order within {fmt_age(t['orders_red_sec'])} while tiles ON"))
    else:
        quiet: dict[str, float | None] = {}
        for lane in on_lanes:
            last = lane_mem.get(lane, {}).get("last_order_ts")
            quiet[lane] = (now - float(last)) if last else None
        newest = min((q for q in quiet.values() if q is not None), default=None)
        all_quiet = newest is None or newest > t["orders_red_sec"]
        st = RED if (all_quiet and not paused) else GREEN
        per = ", ".join(f"{lane}:{fmt_age(q)}/{lane_mem.get(lane, {}).get('orders_48h', 0)}@48h"
                        for lane, q in quiet.items())
        quiet_tiles = [l for l, q in quiet.items() if q is None or q > t["tile_quiet_amber_sec"]]
        if st == GREEN and quiet_tiles:
            st = AMBER
        add(check("trading.orders", "trading", st,
                  f"newest order {fmt_age(newest)} ago across {len(on_lanes)} ON tiles; per tile last/48h: {per}",
                  f"any ON tile ordered within {fmt_age(t['orders_red_sec'])} (RED); each tile within "
                  f"{fmt_age(t['tile_quiet_amber_sec'])} (AMBER)",
                  "" if st == GREEN else
                  ("no tile is placing orders: AI failing, admission gates rejecting everything, or execution wedged"
                   if st == RED else f"quiet tiles {quiet_tiles}: regime gates may be legitimately closed; "
                                     "compare shared_ai_lane_counters reasons")))
    orphans = (dig(fstate, "orphan_order_ids", default=[]) or []) + (dig(fstate, "orphan_position_ids", default=[]) or [])
    if fstate is None:
        add(check("trading.orphans", "trading", SKIP, "no /api/state", "0 orphan orders/positions"))
    else:
        add(check("trading.orphans", "trading", RED if orphans else GREEN, f"{len(orphans)} orphan ids {orphans[:5]}",
                  "0 orphan orders/positions",
                  "" if not orphans else "order/position without a lifecycle owner; reconcile before any deploy"))
    expired = dict(mirror.get("expired") or {})
    filled = dict(mirror.get("filled") or {})
    for item in dig(fstate, "expired_orders", default=[]) or []:
        if item.get("trade_id"):
            expired[item["trade_id"]] = parse_ts(item.get("expired_ts") or item.get("time")) or now
    for item in (dig(fstate, "trades", default=[]) or []) + (dig(fstate, "positions", default=[]) or []):
        if item.get("trade_id"):
            filled[item["trade_id"]] = parse_ts(item.get("ts") or item.get("fill_ts")) or now
    both = {tid: max(expired[tid] or 0, filled[tid] or 0) for tid in set(expired) & set(filled)}
    recent_red = sorted(tid for tid, ts in both.items() if now - ts <= t["contradiction_red_window_sec"])
    recent_amber = sorted(tid for tid, ts in both.items() if now - ts <= t["contradiction_amber_window_sec"])
    unlinked = sum(int((v or {}).get("unlinked_lifecycle_rows") or 0) for v in route_counts.values())
    st = RED if recent_red else AMBER if (recent_amber or unlinked) else GREEN
    add(check("trading.lifecycle", "trading", st,
              f"{len(recent_amber)} expired+filled contradictions in 24h ({len(recent_red)} in "
              f"{fmt_age(t['contradiction_red_window_sec'])}) {recent_amber[:4]}; unlinked lifecycle rows={unlinked}",
              "0 trade ids both EXPIRED and FILLED",
              "" if st == GREEN else "fill-vs-TTL race (see #253/#255): fill thread latency lets TTL expire a filled order"))

    # ---------------- WebSocket / trade ticks
    if not reachable:
        add(check("ws.ticks", "ws", SKIP, "Fly unreachable", f"<= {t['ws_amber_sec']:.0f}s"))
    else:
        ws_age = dig(status, "ws_age")
        ws_age = float(ws_age) if isinstance(ws_age, (int, float)) else None
        progressing = dig(progress, "ws_progressing")
        if progressing is None:
            progressing = dig(inputs.get("runtime_snapshot"), "strategy_progress", "ws_progressing")
        st = GREEN
        if ws_age is None or ws_age > t["ws_red_sec"] or progressing is False:
            st = RED
        elif ws_age > t["ws_amber_sec"] or progressing is None:
            st = AMBER
        add(check("ws.ticks", "ws", st,
                  f"trade tick {fmt_age(ws_age)} old, ws_progressing="
                  f"{progressing if progressing is not None else 'not reported'}",
                  f"<= {t['ws_amber_sec']:.0f}s AMBER / {t['ws_red_sec']:.0f}s RED, ws_progressing reported true",
                  "" if st == GREEN else
                  ("Fly no longer reports strategy_progress.ws_progressing" if progressing is None and st == AMBER
                   and (ws_age or 0) <= t["ws_amber_sec"] else "Bitfinex WS disconnected/stalled; REST fallback only")))

    # ---------------- Shipper
    transfer = dig(health, "volume", "transfer", default=None) or (inputs.get("head_snapshot") if
                                                                   dig(inputs.get("head_snapshot"), "ok") else None)
    if not transfer:
        add(check("shipper.progress", "shipper", SKIP if not reachable else RED, "no transfer block",
                  "segments shipping"))
    else:
        shipped = transfer.get("shipped_seq")
        last_seg = parse_ts(transfer.get("last_segment_at"))
        unshipped = int(transfer.get("unshipped_bytes") or 0)
        err = transfer.get("last_error")
        if shipped is not None and shipped != mem.get("shipped_seq"):
            mem["shipped_seq"], mem["shipped_seq_changed_ts"] = shipped, now
        seg_age = (now - last_seg) if last_seg else None
        status_age = transfer.get("segment_status_age_sec")
        backlog = unshipped > 0 or bool(err)
        st = GREEN
        if seg_age is not None and err and seg_age > t["shipper_stall_red_sec"]:
            st = RED
        elif seg_age is not None and backlog and seg_age > t["shipper_stall_backlog_red_sec"]:
            st = RED
        elif isinstance(status_age, (int, float)) and status_age > t["shipper_status_red_sec"]:
            st = RED
        elif (seg_age is not None and backlog and seg_age > t["shipper_stall_amber_sec"]) or err or \
                unshipped > t["unshipped_amber_bytes"]:
            st = AMBER
        if not transfer.get("segments_enabled", True):
            st = RED
        add(check("shipper.progress", "shipper", st,
                  f"shipped_seq={shipped} last segment {fmt_age(seg_age)} ago, unshipped={unshipped / 1e6:.1f}MB, "
                  f"last_error={err or 'none'}, status age={fmt_age(status_age)}",
                  f"new segment within {fmt_age(t['shipper_stall_red_sec'])} while last_error, within "
                  f"{fmt_age(t['shipper_stall_backlog_red_sec'])} while backlog (RED)",
                  "" if st == GREEN else
                  ("PLAN_RACE/hot stream aborting every cycle or shipper thread dead; ACK will freeze"
                   if st == RED else "backlog building or transient PLAN_RACE")))

    # ---------------- Laptop pull / ACK
    pull = inputs.get("pull_status") or {}
    puller = inputs.get("puller_status") or {}
    receipt = inputs.get("ack_receipt") or {}
    published = dig(transfer, "shipped_seq") if transfer else None
    pstate = inputs.get("puller_state") if isinstance(inputs.get("puller_state"), Mapping) else {}
    applied, applied_src = None, "none"
    for src, value in (("puller status", puller.get("applied_seq")), ("pull status", pull.get("appliedSeq")),
                       ("puller state.json", pstate.get("applied_seq"))):
        if value is not None:
            applied, applied_src = value, src
            break
    fly_acked = dig(transfer, "laptop_acked_seq") if transfer else None
    finished = parse_ts(pull.get("finishedAt"))
    if applied is not None and published is not None and int(applied) >= int(published):
        mem["pull_caught_up_ts"] = now
    if fly_acked is not None and applied is not None and int(fly_acked) >= int(applied):
        mem["ack_caught_up_ts"] = now
    # While Fly ships every few minutes and polls ACKs every ~5m, the trailing
    # counter can chase the leading one indefinitely without ever matching at a
    # tick. Lag is only a custody risk when the trailing counter stops advancing.
    if applied is not None and applied != mem.get("applied_seq"):
        mem["applied_seq"], mem["applied_changed_ts"] = applied, now
    if fly_acked is not None and fly_acked != mem.get("fly_acked_seq"):
        mem["fly_acked_seq"], mem["fly_acked_changed_ts"] = fly_acked, now
    pull_lag = (int(published) - int(applied)) if (published is not None and applied is not None) else None
    ack_lag = (int(applied) - int(fly_acked)) if (applied is not None and fly_acked is not None) else None
    behind_for = now - float(max(mem.setdefault("pull_caught_up_ts", now), mem.get("applied_changed_ts") or 0))
    ack_behind_for = now - float(max(mem.setdefault("ack_caught_up_ts", now), mem.get("fly_acked_changed_ts") or 0))
    finished_age = (now - finished) if finished else None
    reasons = []
    if finished_age is None or finished_age > t["pull_finished_red_sec"]:
        reasons.append(f"no finished pull for {fmt_age(finished_age)}")
    if pull_lag is not None and (pull_lag > t["pull_lag_seq_red"] or (pull_lag > 0 and behind_for > t["pull_lag_red_sec"])):
        reasons.append(f"applied behind published by {pull_lag} for {fmt_age(behind_for)}")
    if ack_lag is not None and ack_lag > t["ack_lag_seq_red"]:
        reasons.append(f"Fly laptop_acked behind applied by {ack_lag} segments")
    elif ack_lag and ack_lag > 0 and ack_behind_for > t["ack_lag_red_sec"]:
        reasons.append(f"Fly laptop_acked behind applied and not advancing for {fmt_age(ack_behind_for)}")
    ambers = []
    if applied is None:
        none_for = now - float(mem.setdefault("applied_none_since", now))
        (reasons if none_for > t["pull_applied_none_red_sec"] else ambers).append(
            f"applied seq unknown for {fmt_age(none_for)} (puller status, pull status and state.json all lack it)")
    else:
        mem.pop("applied_none_since", None)
    exit_code = pull.get("exitCode")
    iteration = pull.get("iteration")
    pull_fail = mem.get("pull_fail") or {}
    if isinstance(exit_code, int) and exit_code != 0:
        if not pull_fail:
            pull_fail = {"since": now, "first_iteration": iteration, "first_finished": pull.get("finishedAt")}
        pull_fail["iteration"], pull_fail["exit"] = iteration, exit_code
        mem["pull_fail"] = pull_fail
        failing_for = now - float(pull_fail["since"])
        try:
            consecutive = int(iteration) - int(pull_fail.get("first_iteration")) + 1
        except (TypeError, ValueError):
            consecutive = 1 if pull.get("finishedAt") == pull_fail.get("first_finished") else 2
        msg = f"segment pull exit={exit_code} for {consecutive} consecutive pulls / {fmt_age(failing_for)}"
        if consecutive > 1 and failing_for > t["pull_exit_red_sec"]:
            # A lock refusal while another puller (the analyzer cycle) keeps applying is degraded, not dead.
            lock_held = "lock" in str(pull.get("error") or "").lower()
            applying = mem.get("applied_changed_ts") and now - float(mem["applied_changed_ts"]) <= t["pull_lag_red_sec"]
            (ambers if lock_held and applying else reasons).append(
                msg + ("; another puller holds the lock and applied keeps advancing" if lock_held and applying else ""))
        else:
            ambers.append(msg)
    elif exit_code == 0:
        mem.pop("pull_fail", None)
    st = RED if reasons else AMBER if ambers else GREEN
    if st == GREEN and pull_lag:
        st = AMBER if behind_for > 5 * MIN else GREEN
    add(check("laptop.pull_ack", "laptop", st,
              f"published={published} applied={applied} ({applied_src}) fly_acked={fly_acked} last pull "
              f"{fmt_age(finished_age)} ago exit={exit_code} (last receipt through {receipt.get('through_seq')})",
              f"applied known and advancing or ==published within {fmt_age(t['pull_lag_red_sec'])}, pull exit 0 "
              f"(RED after >1 failing pull and {fmt_age(t['pull_exit_red_sec'])}), Fly ACK advancing within "
              f"{fmt_age(t['ack_lag_red_sec'])} and <= {t['ack_lag_seq_red']} behind applied",
              "; ".join(reasons + ambers) or ("" if st == GREEN else "pull catching up"),
              str(pull.get("error") or "")[:200],
              fields={"published": published, "applied": applied, "applied_source": applied_src,
                      "fly_acked": fly_acked, "pull_exit": exit_code,
                      **{k: puller.get(k) for k in ("last_attempt_result", "consecutive_failures", "run_seconds",
                                                    "max_run_seconds", "deadline_reached")}}))

    sup = inputs.get("supervisor_tick_at")
    sup_age = (now - sup) if sup else None
    probe = inputs.get("supervisor_probe") if isinstance(inputs.get("supervisor_probe"), Mapping) else None
    sup_threshold = (f"task enabled, last run <= {fmt_age(t['supervisor_tick_red_sec'])}, pull-loop process alive "
                     f"(RED after {fmt_age(t['pull_loop_missing_red_sec'])} missing)")
    if not probe or not probe.get("ok"):
        st = RED if sup_age is None or sup_age > t["supervisor_tick_red_sec"] else AMBER
        add(check("laptop.supervisor", "laptop", st,
                  f"process probe unavailable ({(probe or {}).get('error')}); last log TICK {fmt_age(sup_age)} ago",
                  sup_threshold, "cannot verify the scheduled task / pull loop; only the supervisor log is visible"
                  if st == AMBER else "scheduled task disabled, laptop asleep, or tick erroring"))
    else:
        last_run = parse_ts(probe.get("last_run"))
        run_age = (now - last_run) if last_run else None
        pull_pids = list(probe.get("pull_loop_pids") or [])
        red, amber = [], []
        if not probe.get("task_found"):
            red.append(f"scheduled task {SUPERVISOR_TASK} not found")
        elif str(probe.get("task_state")).lower() == "disabled":
            red.append("scheduled task disabled")
        if run_age is None or run_age > t["supervisor_tick_red_sec"]:
            red.append(f"task last ran {fmt_age(run_age)} ago")
        if pull_pids:
            mem.pop("pull_loop_missing_since", None)
        else:
            missing = now - float(mem.setdefault("pull_loop_missing_since", now))
            (red if missing > t["pull_loop_missing_red_sec"] else amber).append(
                f"no research-segment-pull-loop process for {fmt_age(missing)}")
        if probe.get("last_result") not in (None, 0, 267009, 267011):  # 0x41301 running, 0x41303 not yet run
            amber.append(f"task last result 0x{int(probe['last_result']):X}")
        st = RED if red else AMBER if amber else GREEN
        add(check("laptop.supervisor", "laptop", st,
                  f"task state={probe.get('task_state')} last run {fmt_age(run_age)} ago result={probe.get('last_result')}; "
                  f"pull loop pid(s) {pull_pids or 'none'}; log TICK {fmt_age(sup_age)} ago",
                  sup_threshold,
                  "; ".join(red + amber) or "",
                  fields={"task_state": probe.get("task_state"), "last_run_age_sec": run_age,
                          "pull_loop_pids": pull_pids, "supervisor_pids": probe.get("supervisor_pids"),
                          "probe_checked_at": iso(probe.get("checked_at"))}))

    legacy = inputs.get("legacy_ack_watcher") if isinstance(inputs.get("legacy_ack_watcher"), Mapping) else None
    ack_pids = list((probe or {}).get("ack_watcher_pids") or [])
    if inputs.get("legacy_ack_retired"):
        add(check("laptop.legacy_ack_watcher", "laptop", GREEN, "legacy ACK watcher explicitly retired (marker file)",
                  "retired, or running with a fresh poll"))
    elif legacy is None:
        add(check("laptop.legacy_ack_watcher", "laptop", GREEN, "no legacy ACK watcher state on disk",
                  "retired, or running with a fresh poll"))
    else:
        poll = parse_ts(legacy.get("lastPollAt"))
        poll_age = (now - poll) if poll else None
        frozen = poll_age is None or poll_age > t["legacy_ack_stale_sec"]
        st = AMBER if frozen else GREEN
        add(check("laptop.legacy_ack_watcher", "laptop", st,
                  f"laptop-ack-watcher.status.json state={legacy.get('state')} last poll {fmt_age(poll_age)} ago; "
                  f"process {ack_pids or 'not running'}",
                  "retired, or running with a fresh poll",
                  "" if st == GREEN else "orphan state from the retired data-sync ACK watcher (segments replaced it); "
                                         "archive the status file or create laptop-ack-watcher.RETIRED"))

    # ---------------- Analyzer
    an = inputs.get("analyzer_status") or {}
    cyc = inputs.get("cycle_status") or {}
    gen = parse_ts(an.get("lastCompletedGenerationAt") or an.get("lastSuccessAt"))
    gen_age = (now - gen) if gen else None
    st = RED if gen_age is None or gen_age > t["analyzer_gen_red_sec"] else AMBER if gen_age > t["analyzer_gen_amber_sec"] else GREEN
    add(check("analyzer.generation", "analyzer", st,
              f"last completed generation {fmt_age(gen_age)} ago (state={an.get('state')} exit={an.get('exitCode')})",
              f"<= {fmt_age(t['analyzer_gen_amber_sec'])} AMBER / {fmt_age(t['analyzer_gen_red_sec'])} RED",
              "" if st == GREEN else "analyzer crash/stall or cycle blocked on promotion/migration",
              str(an.get("detail") or "")[:200]))
    api = inputs.get("analyzer_api") if isinstance(inputs.get("analyzer_api"), Mapping) else None
    in_cycle = cyc.get("finishedAt") is None and cyc.get("startedAt") is not None
    if api is None:
        since = mem.setdefault("analyzer_api_down_since", now)
        down = now - float(since)
        st = AMBER if (in_cycle and down <= t["analyzer_api_down_red_sec"]) else RED
        add(check("analyzer.api", "analyzer", st, f":9001 unreachable for {fmt_age(down)} (cycle phase={cyc.get('phase')})",
                  f"answers; down <= {fmt_age(t['analyzer_api_down_red_sec'])} only while a cycle replaces it",
                  "dashboard replaced during the ANALYZER phase" if st == AMBER else "analyzer dashboard crashed"))
    else:
        mem.pop("analyzer_api_down_since", None)
        sapi = inputs.get("analyzer_status_api") if isinstance(inputs.get("analyzer_status_api"), Mapping) else None
        gf = api.get("generation_freshness") if isinstance(api.get("generation_freshness"), Mapping) else \
            dig(sapi, "generation_freshness", default={})
        src_parity = parity_state(api.get("source_revision_parity"))
        rev_parity = parity_state(gf.get("revision_parity"))
        gen_rev = gf.get("generation_revision")
        fly_src = dig(health, "source_git_rev") or fly_rev
        rev_match = same_rev(gen_rev, fly_src)
        fly_sync = dig(health, "analyzer_sync_id") or dig(status, "analyzer_sync_id")
        local_syncs = {k: v for k, v in (("upstream_sync_id", dig(sapi, "upstream_sync_id")),
                                          ("analyzer_sync_id", dig(sapi, "analyzer_sync_id")
                                           or api.get("runtime_analyzer_sync_id"))) if v}
        sync_id_match = None if not fly_sync or not local_syncs else all(v == fly_sync for v in local_syncs.values())
        receipt_age = gf.get("mirror_sync_receipt_age_seconds")
        receipt_age = float(receipt_age) if isinstance(receipt_age, (int, float)) else None
        receipt_ts = parse_ts(gf.get("mirror_sync_receipt_timestamp"))
        run_start = parse_ts(dig(sapi, "analysis_run", "started_at"))
        receipt_at_start = (run_start - receipt_ts) if (run_start and receipt_ts) else None
        problems = []
        if not api.get("ok"):
            problems.append("/api/health ok=false")
        if sapi is None:
            problems.append(f"/api/status unreadable ({errors.get('analyzer_status_api')})")
        elif sapi.get("ok") is not True:
            problems.append(f"/api/status ok={sapi.get('ok')} while /api/health ok={api.get('ok')}")
        if api.get("runtime_sync_match") is False:
            problems.append("runtime_sync_match=false")
        for label, value in (("source_revision_parity", src_parity), ("revision_parity", rev_parity)):
            if value is False:
                problems.append(f"{label}={api.get(label) if label == 'source_revision_parity' else gf.get(label)}")
            elif value is None:
                problems.append(f"{label} not reported")
        if rev_match is False:
            problems.append(f"generation rev {str(gen_rev)[:12]} != Fly {str(fly_src)[:12]}")
        elif rev_match is None:
            problems.append("generation/Fly revision unknown")
        if sync_id_match is False:
            problems.append(f"sync id {local_syncs} != Fly {fly_sync}")
        if receipt_at_start is not None and receipt_at_start > t["analyzer_receipt_amber_sec"]:
            problems.append(f"analysis started on a mirror receipt {fmt_age(receipt_at_start)} old")
        if receipt_age is not None and receipt_age > t["analyzer_receipt_wall_amber_sec"]:
            problems.append(f"mirror sync receipt {fmt_age(receipt_age)} old")
        add(check("analyzer.api", "analyzer", AMBER if problems else GREEN,
                  "; ".join(problems) or f"ok on /api/health and /api/status; generation rev {str(gen_rev)[:12]} == "
                                         f"Fly {str(fly_src)[:12]}; sync id {fly_sync}",
                  "/api/health and /api/status ok, parity MATCH, generation rev == Fly source_git_rev, sync ids == "
                  f"Fly, mirror receipt <= {fmt_age(t['analyzer_receipt_amber_sec'])} old at analysis start and <= "
                  f"{fmt_age(t['analyzer_receipt_wall_amber_sec'])} on the wall clock",
                  "" if not problems else "analyzer generation not current, stale upstream identity, or "
                                          "revision/epoch parity mismatch",
                  fields={"generation_rev": gen_rev, "fly_rev": fly_src, "sync_id_match": sync_id_match,
                          "fly_sync_id": fly_sync, "local_sync_ids": local_syncs, "receipt_age_sec": receipt_age,
                          "receipt_age_at_run_start_sec": receipt_at_start, "health_ok": api.get("ok"),
                          "status_ok": dig(sapi, "ok")}))

    sapi = inputs.get("analyzer_status_api") if isinstance(inputs.get("analyzer_status_api"), Mapping) else None
    reports_threshold = (f"required_reports_ok=true and ok=true (AMBER on first sight, RED after "
                         f"{fmt_age(t['analyzer_reports_red_sec'])} or a second generation)")
    if sapi is None:
        add(check("analyzer.reports", "analyzer", AMBER,
                  f":9001 /api/status unreadable ({errors.get('analyzer_status_api')}; cycle phase={cyc.get('phase')})",
                  reports_threshold, "required-report state unknown"))
    elif not isinstance(sapi.get("required_reports_ok"), bool):
        mem.pop("analyzer_reports_bad", None)
        add(check("analyzer.reports", "analyzer", AMBER, "/api/status has no required_reports_ok field",
                  reports_threshold, "analyzer dashboard predates required-report reporting or changed shape"))
    else:
        req = sapi.get("required_report_status") if isinstance(sapi.get("required_report_status"), Mapping) else {}
        failures = [str(x) for x in sapi.get("required_report_failures") or []] or \
            [name for name, v in req.items() if isinstance(v, Mapping) and v.get("available_in_generation") is False]
        bad = sapi["required_reports_ok"] is False or sapi.get("ok") is False
        generation = str(sapi.get("generated_at") or "")
        if bad:
            track = mem.setdefault("analyzer_reports_bad", {"since": now, "generations": []})
            if generation and generation not in track["generations"]:
                track["generations"] = (track["generations"] + [generation])[-5:]
            bad_for = now - float(track["since"])
            st = RED if bad_for > t["analyzer_reports_red_sec"] or len(track["generations"]) >= 2 else AMBER
            detail = ", ".join(
                f"{name} ({str(dig(req, name, 'generation_error') or 'unavailable')[:90]})" for name in failures[:4])
            other = [str(x) for x in (sapi.get("stale_reasons") or []) + (sapi.get("analyzer_sync_blockers") or [])]
            obs = (f"required_reports_ok={sapi['required_reports_ok']} ok={sapi.get('ok')} for {fmt_age(bad_for)} "
                   f"across {len(track['generations'])} generation(s); failing: {detail or 'none listed'}"
                   + (f"; other reasons: {', '.join(other[:4])}" if other else ""))
        else:
            mem.pop("analyzer_reports_bad", None)
            st, obs = GREEN, f"all {len(req)} required reports available in generation {generation or '?'}"
        add(check("analyzer.reports", "analyzer", st, obs, reports_threshold,
                  "" if st == GREEN else "required analyzer reports failing in the current generation; see "
                                         "required_report_status on :9001 /api/status and the analyzer-once log",
                  fields={"required_reports_ok": sapi["required_reports_ok"], "ok": sapi.get("ok"),
                          "failing_reports": failures, "generated_at": generation or None}))
    started = parse_ts(cyc.get("startedAt"))
    if in_cycle and started:
        dur = now - started
        st = RED if dur > t["cycle_red_sec"] else AMBER if dur > t["cycle_amber_sec"] else GREEN
        obs = f"cycle running {fmt_age(dur)} (phase={cyc.get('phase')})"
    else:
        fin = parse_ts(cyc.get("finishedAt"))
        dur = (fin - started) if (fin and started) else None
        code = cyc.get("exitCode")
        st = AMBER if code not in (None, 0) else GREEN
        obs = f"last cycle {fmt_age(dur)} exit={code}"
        if code not in (None, 0) and cyc.get("stopReason"):
            obs += f" ({cyc.get('stopReason')})"
    head = str(inputs.get("analyzer_head") or "")
    if fly_rev and head and not head.startswith(fly_rev):
        if inputs.get("analyzer_contains_fly"):
            obs += f"; analyzer rev {head[:12]} contains Fly {fly_rev[:12]}"
        else:
            st = max(st, AMBER, key=RANK.get)
            obs += f"; analyzer rev {head[:12]} does not contain Fly {fly_rev[:12]}"
    add(check("analyzer.cycle", "analyzer", st, obs,
              f"cycle <= {fmt_age(t['cycle_amber_sec'])}, exit 0, analyzer rev contains Fly rev",
              "" if st == GREEN else "slow promotion/migration, failed cycle, or v2c auto-ff not yet followed Fly"))

    # ---------------- Analyzer studies + integrity (a pass can exit 0 while studies throw)
    receipt = inputs.get("analyzer_receipt") if isinstance(inputs.get("analyzer_receipt"), Mapping) else None
    integrity = inputs.get("analyzer_integrity") if isinstance(inputs.get("analyzer_integrity"), Mapping) else {}
    integrity_status = str(integrity.get("report_status") or "MISSING").upper()
    manifest_at = parse_ts(inputs.get("analyzer_manifest_generated_at"))
    receipt_at = parse_ts((receipt or {}).get("generated_at"))
    if receipt is None or (manifest_at and receipt_at and receipt_at < manifest_at - 30 * MIN):
        st = RED if integrity_status in ("INVALID", "UNCHECKED") else AMBER
        add(check("analyzer.studies", "analyzer", st,
                  f"no generation receipt for the current generation; integrity {integrity_status}",
                  "generation receipt GREEN: every required study OK and integrity VALID",
                  "analyzer revision predates the generation receipt, or the receipt step failed"))
    else:
        st = str(receipt.get("level") or AMBER).upper()
        st = st if st in (GREEN, AMBER, RED) else AMBER
        if integrity_status != "VALID":
            st = RED
        reasons = list(receipt.get("reasons") or [])
        add(check("analyzer.studies", "analyzer", st,
                  "; ".join(reasons) or f"all {len(receipt.get('studies') or [])} studies OK; integrity VALID",
                  "every required study OK, integrity VALID, protection replay not truncated, inputs not BLOCKED",
                  "" if st == GREEN else "see analyzer_generation_receipt.json and the analyzer-once log",
                  ", ".join(receipt.get("failed_required_studies") or [])[:200]))

    dh = inputs.get("data_health_report") if isinstance(inputs.get("data_health_report"), Mapping) else None
    if dh is None:
        add(check("analyzer.data_health", "analyzer", SKIP, "data_health_report.json not found", "streams OK"))
    elif not dh.get("stream_status_basis"):
        add(check("analyzer.data_health", "analyzer", SKIP,
                  "report predates mirror-relative verdicts (#293); wall-clock STALE verdicts ignored",
                  "streams judged against the mirror head"))
    else:
        bad = [f"{s.get('stream')}={s.get('status')}" for s in dh.get("streams") or []
               if isinstance(s, Mapping) and str(s.get("status")) not in ("OK", "WARMUP")]
        mirror = str(dh.get("mirror_status") or "MISSING")
        st = AMBER if bad or mirror != "OK" else GREEN
        add(check("analyzer.data_health", "analyzer", st,
                  f"mirror {mirror} ({fmt_age(dh.get('mirror_staleness_sec'))} behind wall clock); "
                  f"streams {dh.get('status_counts')}" + (f"; {', '.join(bad[:6])}" if bad else ""),
                  "mirror OK and every stream OK/WARMUP relative to the mirror head",
                  "" if st == GREEN else "collector feed gap on Fly or laptop pull lag"))

    recon = inputs.get("ledger_reconciliation") if isinstance(inputs.get("ledger_reconciliation"), Mapping) else None
    lr = _ledger_reconciliation_module()
    if recon is None or lr is None:
        add(check("ledger.reconciliation", "analyzer", SKIP,
                  "ledger_reconciliation.json not published yet" if recon is None else "reconciliation module missing",
                  "Fly, mirror ledger and analyzer agree on trades and PnL up to the analyzer watermark"))
    else:
        out = lr.compare_with_fly(recon, fstate, inputs.get("mirror_trades"))
        st = out["level"] if out["level"] in (GREEN, AMBER, RED) else AMBER
        per = out["breakdown"].get("per_lane") or {}
        lanes = "; ".join(
            f"{lane.replace('FAMILY_', '')}: fly {v['fly_n']}/mirror {v['mirror_n']}+{v['fly_newer_than_mirror']}"
            f"/analyzer {v['analyzer_n']} (quarantined {v['quarantined']}), Win {v['win_pct']}%"
            f" (Fly tile {v['fly_tile_win_pct']}%, Fly ledger cents {v['fly_ledger_win_pct_cents']}%)"
            for lane, v in per.items()
        ) or "; ".join(f"{lane.replace('FAMILY_', '')}: analyzer {v.get('n')}, Win {v.get('win_pct')}%"
                       for lane, v in (recon.get("analyzer_cohort") or {}).items())
        add(check("ledger.reconciliation", "analyzer", st,
                  f"through {recon.get('source_data_through')}: {lanes}"
                  + (f"; {'; '.join(out['reasons'])}" if out["reasons"] else ""),
                  "same trade ids and PnL (+-$0.01) on Fly, mirror ledger and analyzer up to the analyzer "
                  f"watermark; Win % = {recon.get('win_pct_definition')}",
                  "" if st == GREEN else "see ledger_reconciliation.json (trades[]) and the breakdown for missing ids",
                  json.dumps({k: out["breakdown"].get(k) for k in ("missing_in_mirror", "bounded", "fly_listed")},
                             default=str)[:400]))

    # ---------------- Exports (worker eed92197)
    exp = inputs.get("exports") or {}
    if not exp.get("present"):
        add(check("exports.freshness", "exports", SKIP, "analyzer-exports\\latest not provisioned yet",
                  f"<= {fmt_age(t['exports_amber_sec'])}"))
    else:
        gen_at = exp.get("generated_at")
        age = (now - gen_at) if gen_at else None
        st = AMBER if age is None or age > t["exports_amber_sec"] else GREEN
        add(check("exports.freshness", "exports", st, f"latest export {fmt_age(age)} old ({exp.get('files')} files)",
                  f"<= {fmt_age(t['exports_amber_sec'])}",
                  "" if st == GREEN else "export step not running after analyzer generations"))

    # ---------------- Data streams (research collection + d9f889db streams)
    coll = dig(status, "collection", "research_coverage", "collection_health", default=None)
    cross = dig(status, "collection", "cross_venue_tape", default=None) or dig(inputs.get("runtime_snapshot"),
                                                                                "cross_venue_health")
    streams = dig(status, "data_streams", default=None) or dig(status, "collection", "data_streams", default=None)
    issues = []
    if isinstance(coll, Mapping) and coll.get("status") not in (None, "OK"):
        issues.append(f"collection {coll.get('status')}: {', '.join(coll.get('alarms') or [])}")
    if isinstance(cross, Mapping) and cross.get("status") not in (None, "OK", "DISABLED"):
        issues.append(f"cross-venue {cross.get('status')} stale={cross.get('stale_venues')}")
    xvl = dig(status, "collection", "xvl_evaluator", default=None) or dig(inputs.get("runtime_snapshot"),
                                                                           "xvl_evaluator_health")
    if isinstance(xvl, Mapping) and xvl.get("status") not in (None, "OK", "DISABLED", "STARTING"):
        issues.append(f"XVL evaluator {xvl.get('status')} ({xvl.get('reason')})")
    dead = dig(inputs.get("runtime_snapshot"), "ai_input_health", default={})
    if isinstance(dead, Mapping) and dead.get("status") == "DEAD_INPUT":
        issues.append(f"AI dead inputs {[d.get('path') for d in dead.get('dead_fields') or []][:5]}")
    per_stream = []
    mct = dig(status, "collection", "market_context_tape", default=None)
    if isinstance(mct, Mapping) and mct.get("enabled", True):
        per_stream.append(f"market_context:{mct.get('status')}/{fmt_age(mct.get('age_sec'))}")
        if mct.get("status") not in (None, "OK"):
            issues.append(f"market_context {mct.get('status')} stale_feeds={mct.get('stale_feeds')}")
        for feed, info in (mct.get("feeds") or {}).items():
            if isinstance(info, Mapping) and (info.get("ok") is False or info.get("connected") is False):
                issues.append(f"market_context feed {feed} down")
    micro = dig(status, "collection", "microstructure_tape", default=None)
    if isinstance(micro, Mapping):
        last_bucket = micro.get("last_bucket_ts")
        age = (now - float(last_bucket)) if isinstance(last_bucket, (int, float)) else None
        per_stream.append(f"bitfinex_1s:{fmt_age(age)}")
        if age is None or age > t["streams_stale_amber_sec"]:
            issues.append(f"bitfinex 1s tape last bucket {fmt_age(age)} ago")
        if int(micro.get("write_failures_this_process") or 0) or int(micro.get("io_write_failures_this_process") or 0):
            issues.append("bitfinex 1s tape write failures")
    if isinstance(cross, Mapping):
        per_stream.append(f"cross_venue:{cross.get('status')}/{fmt_age(cross.get('collector_age_s'))}")
    if isinstance(xvl, Mapping):
        for lane, info in (xvl.get("lanes") or {}).items():
            by = (info or {}).get("by_status") or {}
            evals = int((info or {}).get("evaluations") or 0)
            if evals:
                per_stream.append(f"xvl_stale_feed:{100.0 * int(by.get('STALE_FEED') or 0) / evals:.0f}%")
    if isinstance(streams, Mapping):
        for name, info in streams.items():
            if not isinstance(info, Mapping):
                continue
            age = info.get("age_sec", info.get("staleness_sec"))
            cov = info.get("coverage", info.get("coverage_pct"))
            stale = info.get("stale") is True or (isinstance(age, (int, float)) and age > t["streams_stale_amber_sec"])
            per_stream.append(f"{name}:{fmt_age(age) if age is not None else '?'}/{cov if cov is not None else '?'}")
            if stale or str(info.get("status", "OK")).upper() not in ("OK", "GREEN", "DISABLED"):
                issues.append(f"stream {name} stale/unhealthy")
    if status is None:
        add(check("streams.coverage", "streams", SKIP, "Fly unreachable", "all streams fresh"))
    elif not issues and not per_stream:
        add(check("streams.coverage", "streams", AMBER,
                  "no stream health reported by Fly (no market_context/microstructure/cross-venue/XVL/data_streams "
                  "blocks); coverage unknown",
                  f"collection OK, every stream fresher than {fmt_age(t['streams_stale_amber_sec'])}",
                  "Fly /api/status collection blocks missing (shape change or collector disabled)"))
    else:
        add(check("streams.coverage", "streams", AMBER if issues else GREEN,
                  "; ".join(issues) or f"collection OK; streams {', '.join(per_stream)}",
                  f"collection OK, every stream fresher than {fmt_age(t['streams_stale_amber_sec'])}",
                  "" if not issues else "collector worker stalled, tape source missing, or venue feed stale"))

    sh_streams = inputs.get("analyzer_streams") if isinstance(inputs.get("analyzer_streams"), Mapping) else None
    af_threshold = (f"no analysed stream whose content lags its export by > {fmt_age(t['streams_content_lag_amber_sec'])} "
                    "or is STALE while continuous; not_fully_analysed empty")
    if sh_streams is None or not isinstance(sh_streams.get("streams"), list):
        add(check("streams.analysed_freshness", "streams", AMBER,
                  f":9001 /api/streams/health unavailable ({errors.get('analyzer_streams') or 'no streams list'})",
                  af_threshold, "analysed-stream freshness unknown"))
    else:
        partial = [str(x) for x in sh_streams.get("not_fully_analysed") or []]
        stale, lags = [], []
        for s in sh_streams["streams"]:
            if not isinstance(s, Mapping):
                continue
            name = str(s.get("stream"))
            usage = str(s.get("analyzer_usage") or "").upper()
            if usage in ("", "HEALTH_ONLY", "NONE") or name in partial:
                continue
            lag = s.get("content_lag_sec")
            lag = float(lag) if isinstance(lag, (int, float)) else None
            if lag is None and parse_ts(s.get("content_last_at")):
                lag = now - float(parse_ts(s.get("content_last_at")))
            if lag is not None:
                lags.append(lag)
            if lag is not None and lag > t["streams_content_lag_amber_sec"]:
                stale.append(f"{name} content {fmt_age(lag)} behind")
            elif str(s.get("status")).upper() == "STALE" and s.get("continuous"):
                stale.append(f"{name} STALE (content end {s.get('content_last_at') or 'unknown'})")
        st = AMBER if stale or partial else GREEN
        fresh = sh_streams.get("freshness") if isinstance(sh_streams.get("freshness"), Mapping) else {}
        add(check("streams.analysed_freshness", "streams", st,
                  ("; ".join(stale + ([f"not fully analysed: {', '.join(partial[:4])}"] if partial else [])) or
                   f"{len(sh_streams['streams'])} streams; analysed content lag max {fmt_age(max(lags) if lags else None)}")
                  + f" (export {fresh.get('age_min')}m old, status {sh_streams.get('status')})",
                  af_threshold,
                  "" if st == GREEN else "stream content stopped advancing on Fly/mirror but the analyzer still marks "
                                         "it analysed, or a stream is only partly analysed",
                  fields={"stale_analysed": stale, "not_fully_analysed": partial,
                          "export_age_min": fresh.get("age_min"), "status": sh_streams.get("status")}))

    # ---------------- Dashboards / roster parity
    registry = inputs.get("registry") or {}
    fly_lanes = [str(x.get("lane")) for x in dig(status, "active_tiles", default=[]) or [] if isinstance(x, Mapping)]
    reg_lanes = list(registry.get("lanes") or [])
    toggle_lanes = sorted(toggles)
    contradictions = []
    if reg_lanes and fly_lanes and reg_lanes != fly_lanes:
        contradictions.append(f"Fly roster {fly_lanes} != registry {reg_lanes}")
    if fly_lanes and toggle_lanes and sorted(fly_lanes) != toggle_lanes:
        contradictions.append(f"Fly toggles {toggle_lanes} != active tiles")
    api_health = inputs.get("analyzer_api") if isinstance(inputs.get("analyzer_api"), Mapping) else {}
    epoch = api_health.get("epoch_parity", dig(api_health, "generation_freshness", "epoch_parity"))
    if api_health and parity_state(epoch) is False:
        contradictions.append(f"analyzer epoch parity mismatch {str(epoch)[:120]}")
    ds = dig(fstate, "dashboard_truth", "deepseek", default=None)
    ai_age_now = (now - last_success) if last_success else None
    if isinstance(ds, Mapping) and str(ds.get("status")).upper() == "OK" and ai_expected and \
            (ai_age_now is None or ai_age_now > t["ai_success_red_sec"]):
        contradictions.append(f"dashboard says '{ds.get('label')}' but last AI success {fmt_age(ai_age_now)} ago")
    lpc = dig(fstate, "lane_position_counts", default={}) or {}
    for lane, counts in route_counts.items():
        lp = lpc.get(lane) or {}
        if lp and (int(lp.get("open") or 0) != int(counts.get("open") or 0)):
            contradictions.append(f"{lane} open {lp.get('open')} (positions) != {counts.get('open')} (tile route)")
    if contradictions:
        since = mem.setdefault("dashboard_contradiction_since", now)
        lasting = now - float(since)
        severe = any("dashboard says" in c for c in contradictions) or lasting > t["registry_mismatch_red_sec"]
        st = RED if severe else AMBER
    else:
        mem.pop("dashboard_contradiction_since", None)
        st = GREEN if (fly_lanes or reg_lanes) and status is not None else SKIP
    add(check("dashboards.parity", "dashboards", st,
              "; ".join(contradictions) or f"{len(fly_lanes)} tiles agree across Fly API, toggles and registry",
              "Fly dashboard API == registry roster == toggles; truth labels match evidence",
              "" if not contradictions else "stale deploy/registry drift or a truth label built from attempts, not results",
              fields={"epoch_parity": None if epoch is None else str(epoch)[:160],
                      "epoch_parity_ok": parity_state(epoch), "fly_lanes": list(fly_lanes),
                      "registry_lanes": list(reg_lanes or [])}))

    # ---------------- Railway relay / API
    relay = inputs.get("relay_snapshot") or {}
    rail = inputs.get("railway_health") if isinstance(inputs.get("railway_health"), Mapping) else None
    observed_at = parse_ts(relay.get("observedAt"))
    snap_age = (now - observed_at) if observed_at else None
    armed = bool(relay.get("relayArmedAt")) or str(relay.get("relayExecutionMode") or relay.get("status") or "").upper() \
        not in ("PAUSED", "DISARMED", "")
    recon_raw = relay.get("reconciliation")
    recon = recon_raw if isinstance(recon_raw, Mapping) else {}
    executor = relay.get("relayExecutor") or {}
    relay_hint = "relay executor heartbeat/snapshot stale"
    if not relay.get("ok"):
        st, obs = AMBER, f"relay status unreadable ({relay.get('error')})"
    else:
        st = RED if armed or relay.get("positionMismatchAlert") or recon.get("alert") else GREEN
        if st == GREEN and (snap_age is None or snap_age > t["relay_snapshot_amber_sec"] or not executor.get("healthy")
                            or float(executor.get("heartbeatAgeMs") or 0) > t["relay_heartbeat_amber_ms"]):
            st = AMBER
        if st == GREEN and recon_raw is None:
            st, relay_hint = AMBER, "Railway relay status reports reconciliation=null: exchange reconciliation unverified"
        obs = (f"mode={relay.get('relayExecutionMode')} armedAt={relay.get('relayArmedAt')} executor={executor.get('status')} "
               f"hb={fmt_age((executor.get('heartbeatAgeMs') or 0) / 1000)} snapshot {fmt_age(snap_age)} old "
               f"reconciliation={'null' if recon_raw is None else ('alert' if recon.get('alert') else 'ok')}")
    add(check("railway.relay", "railway", st, obs,
              "relay PAUSED/disarmed, executor healthy, reconciliation reported without alert",
              "" if st == GREEN else ("relay ARMED or reconciliation mismatch - verify on Railway immediately"
                                      if st == RED else relay_hint)))
    if rail is None:
        add(check("railway.api", "railway", AMBER if inputs.get("errors", {}).get("railway_health") != "CONFIG_MISSING" else SKIP,
                  f"/health {errors.get('railway_health')}", "api ok, database ok",
                  "Railway API down or redeploying"))
    else:
        db = dig(rail, "services", "database")
        add(check("railway.api", "railway", GREEN if db == "ok" else RED, f"api={dig(rail, 'services', 'api')} database={db}",
                  "api ok, database ok", "" if db == "ok" else "Neon connection failing from Railway"))

    # ---------------- Neon
    neon = inputs.get("neon")
    if neon is None:
        add(check("neon.usage", "neon", SKIP, "not configured (set NEON_API_KEY + NEON_PROJECT_ID)",
                  "egress growth below budget"))
    elif neon.get("missing"):
        add(check("neon.usage", "neon", SKIP, f"not configured (missing {' + '.join(neon['missing'])})",
                  "egress growth below budget"))
    elif neon.get("error"):
        hint = NEON_ERROR_HINTS.get(str(neon["error"]), "")
        add(check("neon.usage", "neon", AMBER,
                  f"Neon {neon.get('endpoint') or 'API'} {neon['error']}{f' ({hint})' if hint else ''}; usage unknown",
                  "consumption history readable", "check NEON_API_KEY / org access; see runbook"))
    else:
        budget = float(neon.get("budget_bytes_per_hour") or os.environ.get("NEON_EGRESS_BUDGET_BYTES_PER_HOUR")
                       or 200 * 1024**2)
        expected = f"egress <= {budget / 1e6:.0f}MB/h with non-zero reported usage"
        egress, compute = float(neon.get("egress_bytes") or 0), float(neon.get("compute_cu_hours") or 0)
        storage = float(neon.get("storage_gb_month") or 0)
        rate = neon.get("rate_bytes_per_hour")
        if not neon.get("buckets") or not (egress or compute or storage):
            add(check("neon.usage", "neon", AMBER,
                      f"no consumption reported for {neon.get('project')} since {neon.get('month_start')} "
                      f"({neon.get('buckets') or 0} buckets, all zero); usage unknown", expected,
                      "zeros are not proof of low usage - check the plan / consumption API"))
        else:
            summary = (f"MTD egress {egress / 1e9:.2f}GB "
                       f"({egress / 1e9 / NEON_LAUNCH_RATES['egress_included_gb'] * 100:.1f}% of 500GB incl.), "
                       f"rate {('%.1fMB/h' % (float(rate) / 1e6)) if rate is not None else '?'}"
                       f"{(' (hour ' + str(neon.get('rate_hour'))[11:16] + 'Z)') if rate is not None else ''}; "
                       f"compute {compute:.1f} CU-h; storage {storage:.3f} GB-mo "
                       f"+ PITR {float(neon.get('instant_restore_gb_month') or 0):.3f}")
            if neon.get("est_cost_usd") is not None:
                summary += f"; est. cost ${float(neon['est_cost_usd']):.2f} ({neon.get('plan')})"
            if rate is None:
                st, action = AMBER, "no complete hourly bucket reported yet - rate unknown"
            elif float(rate) > budget:
                st, action = AMBER, "polling loop or unbounded query (see #261)"
            else:
                st, action = GREEN, ""
            add(check("neon.usage", "neon", st, summary, expected, action))

    # ---------------- Bitfinex
    live_armed = dig(status, "live_armed", default=dig(health, "live_armed"))
    bfx_enabled = dig(status, "bitfinex_live_enabled", default=dig(health, "bitfinex_live_enabled"))
    force_paper = dig(status, "force_paper_mode", default=dig(health, "force_paper_mode"))
    exch_qty = recon.get("signedExchangePositionQty")
    audit = relay.get("exchangeOrderAudit") or {}
    active_orders = int(audit.get("activeOrderCount") or 0)
    problems = []
    if live_armed is True:
        problems.append("live_armed=true")
    if bfx_enabled is True:
        problems.append("bitfinex_live_enabled=true")
    if force_paper is False:
        problems.append("force_paper_mode=false")
    if exch_qty not in (None, 0, 0.0):
        problems.append(f"exchange position qty={exch_qty}")
    if active_orders:
        problems.append(f"{active_orders} active exchange orders")
    relay_disarmed = relay.get("ok") and not armed and str(relay.get("relayExecutionMode") or relay.get("status")
                                                          or "").upper() in ("PAUSED", "DISARMED")
    explicit_disarm = live_armed is False and bfx_enabled is False and force_paper is True and relay_disarmed
    if not reachable and not relay.get("ok"):
        add(check("bitfinex.exposure", "bitfinex", AMBER, "cannot observe (Fly and relay unreadable)",
                  "disarmed, 0 exchange position, 0 exchange orders"))
    elif problems:
        add(check("bitfinex.exposure", "bitfinex", RED, "; ".join(problems),
                  "disarmed, 0 exchange position, 0 exchange orders",
                  "UNEXPECTED REAL EXPOSURE/ARMING - never force-close; escalate to Danish"))
    elif exch_qty is None and not explicit_disarm:
        add(check("bitfinex.exposure", "bitfinex", AMBER,
                  f"exchange position unknown (qty not reported) while not explicitly disarmed: live_armed={live_armed} "
                  f"bitfinex_live_enabled={bfx_enabled} force_paper={force_paper} relay="
                  f"{relay.get('relayExecutionMode') or relay.get('status')}",
                  "disarmed, 0 exchange position, 0 exchange orders",
                  "real exposure cannot be ruled out; verify the relay reconciliation / Bitfinex position"))
    else:
        qty_text = (f"exchange qty={exch_qty}" if exch_qty is not None else
                    "exchange qty not probed (disarmed: Fly live_armed=false, bitfinex_live_enabled=false, "
                    f"relay {relay.get('relayExecutionMode') or relay.get('status')})")
        add(check("bitfinex.exposure", "bitfinex", GREEN,
                  f"disarmed (live_armed={live_armed}, force_paper={force_paper}), {qty_text}, orders={active_orders}",
                  "disarmed, 0 exchange position, 0 exchange orders"))

    # ---------------- Proof checker
    active = inputs.get("proof_active")
    row = inputs.get("proof_last_row")
    if not isinstance(active, Mapping) and not row:
        add(check("proof.latest", "proof", SKIP, "no proof window active", "latest row PASS"))
    else:
        row_at = parse_ts(dig(row, "at"))
        row_age = (now - row_at) if row_at else None
        result = dig(active, "status", "result")
        row_status = dig(row, "status")
        st = GREEN
        if row_status == "FAIL" or (row_age is not None and row_age > t["proof_row_amber_sec"] and result != "PASSED"):
            st = AMBER
        add(check("proof.latest", "proof", st,
                  f"window {result}; latest row {row_status} {fmt_age(row_age)} ago failed={dig(row, 'failed_checks', default=[])}",
                  f"latest row PASS within {fmt_age(t['proof_row_amber_sec'])}",
                  "" if st == GREEN else "see the failing proof checks; the matching health checks carry the cause"))

    # ---------------- Disk
    disk = inputs.get("laptop_disk") or {}
    vol = dig(health, "volume", default={}) or {}
    parts, st = [], GREEN
    if disk:
        free = disk["free"]
        parts.append(f"laptop free {free / 1024**3:.1f}GB")
        st = RED if free < t["laptop_free_red_bytes"] else AMBER if free < t["laptop_free_amber_bytes"] else st
    if vol.get("free_bytes") is not None:
        ffree = float(vol["free_bytes"])
        parts.append(f"Fly volume free {ffree / 1024**3:.1f}GB ({vol.get('hours_to_full')}h to full)")
        fst = RED if ffree < t["fly_free_red_bytes"] else AMBER if ffree < t["fly_free_amber_bytes"] else GREEN
        st = max(st, fst, key=RANK.get)
    if transfer and transfer.get("max_store_bytes"):
        pct = 100.0 * float(transfer.get("store_bytes") or 0) / float(transfer["max_store_bytes"])
        parts.append(f"segment store {pct:.1f}% of cap")
        sst = RED if pct >= t["store_cap_red_pct"] else AMBER if pct >= t["store_cap_amber_pct"] else GREEN
        st = max(st, sst, key=RANK.get)
    add(check("disk.space", "disk", st if parts else SKIP, "; ".join(parts) or "unknown",
              f"laptop >= {t['laptop_free_amber_bytes'] / 1024**3:.0f}GB, Fly >= {t['fly_free_amber_bytes'] / 1024**3:.0f}GB, "
              f"store < {t['store_cap_amber_pct']:.0f}% cap",
              "" if st == GREEN else "data growth; check storage.retention (custody-gated pruning) "
                                     "before raising the volume"))

    # ---------------- Retention (laptop 50 GB cap, prune ledger) and analysis archive
    run = inputs.get("retention_last_run")
    if not isinstance(run, Mapping):
        add(check("storage.retention", "storage", AMBER, "bot_data_retention has not run",
                  "retention run within 3h, usage below 80% of cap",
                  "run-segment-analyzer-cycle.ps1 calls bot_data_retention.py after each analyzer pass"))
    else:
        run_age = (now - parse_ts(run.get("finished_at"))) if parse_ts(run.get("finished_at")) else None
        level = str(run.get("level") or AMBER)
        st = level if level in (GREEN, AMBER, RED) else AMBER
        if run_age is None or run_age > t["retention_run_red_sec"]:
            st = RED
        elif run_age > t["retention_run_amber_sec"]:
            st = max(st, AMBER, key=RANK.get)
        denied = run.get("deny_reasons") or []
        if denied and st == GREEN and float(run.get("usage_fraction") or 0) >= 0.8:
            st = AMBER
        prune_mode = transfer.get("prune_mode") if transfer else None
        pruned, custody = (transfer or {}).get("pruned_through_seq"), (transfer or {}).get("custody_through_seq")
        if pruned and (custody is None or int(pruned) > int(custody)):
            st = RED
        exits = [row for row in inputs.get("retention_exits") or [] if isinstance(row, Mapping)]
        failed_runs = [row for row in exits if row.get("exit") != 0]
        exit_note = ""
        if exits:
            exit_note = f"; recent exits {[row.get('exit') for row in exits]}"
            if len(exits) >= 3 and len(failed_runs) == len(exits):
                st = RED
            elif exits[-1].get("exit") != 0 or len(failed_runs) >= 2:
                st = max(st, AMBER, key=RANK.get)
            if failed_runs:
                exit_note += f" (last failure: {str(failed_runs[-1].get('detail'))[:80]})"
        used = float(run.get("bytes_after") or 0) / 1e9
        cap = float(run.get("cap_bytes") or 0) / 1e9
        add(check("storage.retention", "storage", st,
                  f"laptop bot data {used:.1f}/{cap:.0f}GB ({float(run.get('usage_fraction') or 0) * 100:.0f}%), "
                  f"mode {run.get('mode')}, last run {fmt_age(run_age)} ago, ledger rows {run.get('ledger_rows')}, "
                  f"reclaimed {int(run.get('reclaimed_bytes') or 0) / 1e9:.2f}GB"
                  f"{' (would ' + format(int(run.get('would_reclaim_bytes') or 0) / 1e9, '.2f') + 'GB)' if run.get('mode') == 'dry_run' else ''}; "
                  f"deny={denied or 'none'}; Fly prune {prune_mode or 'off'} pruned<= {pruned} custody<= {custody}"
                  f"{exit_note}",
                  "run < 3h old, last run exit 0 and <2 of the last 3 failed, usage < 80% (AMBER) / 90% (RED) "
                  "of the 50GB cap, Fly pruned <= custody",
                  "" if st == GREEN else "see C:\\DoxxedCrypto\\bot-data-retention\\status.json and "
                                         "docs/runbooks/DATA-RETENTION.md"))

    tier_a = inputs.get("tier_a_health") if isinstance(inputs.get("tier_a_health"), Mapping) else None
    if tier_a is None:
        add(check("storage.tier_a", "storage", SKIP, "retention status has no tier_a block yet",
                  "every Tier A dataset promotes dated Parquet; no undated or 1970 staging"))
    else:
        level = str(tier_a.get("level") or AMBER).upper()
        st = level if level in (GREEN, AMBER, RED) else AMBER
        bad = [f"{d.get('dataset')}={d.get('level')}: {'; '.join(map(str, d.get('reasons') or []))[:80]}"
               for d in tier_a.get("datasets") or [] if isinstance(d, Mapping) and d.get("level") != GREEN]
        add(check("storage.tier_a", "storage", st,
                  f"{len(tier_a.get('datasets') or [])} datasets, backfilled={tier_a.get('backfilled')}"
                  + (f"; {', '.join(bad[:5])}" if bad else "; all promoted"),
                  "every Tier A dataset promotes dated Parquet; no undated or 1970 staging",
                  "" if st == GREEN else "run bot_data_retention.py --tier-a-backfill (dry run first) between "
                                         "analyzer cycles; see docs/runbooks/DATA-RETENTION.md"))
    snap = inputs.get("archive_last_snapshot")
    snap_at = parse_ts((snap or {}).get("written_at"))
    snap_age = (now - snap_at) if snap_at else None
    st = GREEN
    if snap_age is None or snap_age > t["archive_snapshot_red_sec"]:
        st = RED if snap_age is not None else AMBER
    elif snap_age > t["archive_snapshot_amber_sec"]:
        st = AMBER
    add(check("archive.freshness", "storage", st,
              f"last analysis snapshot {dig(snap, 'snapshot_id') or 'none'} {fmt_age(snap_age)} ago "
              f"(seq<= {dig(snap, 'segment_seq_through')})",
              "a verified snapshot per analyzer generation (< 3h)",
              "" if st == GREEN else "no snapshot means retention deletes nothing; check the analyzer log "
                                     "for 'Analysis archive failed'"))

    # ---------------- Self-aware keeper (:9021) - its real engine progress, not a ping
    sa = inputs.get("selfaware") if isinstance(inputs.get("selfaware"), Mapping) else None
    factor = t["selfaware_job_late_factor"]
    sa_threshold = (f"answers (RED after {fmt_age(t['selfaware_down_red_sec'])} down), generated_at and diagnose "
                    f"last_ok <= {fmt_age(t['selfaware_stale_red_sec'])} (RED), every job last_ok within "
                    f"{factor:g}x its cadence (AMBER)")
    if sa is None:
        down = now - float(mem.setdefault("selfaware_down_since", now))
        st = RED if down > t["selfaware_down_red_sec"] else AMBER
        add(check("selfaware.engine", "selfaware", st,
                  f":9021 unreachable for {fmt_age(down)} ({errors.get('selfaware')})", sa_threshold,
                  "DoxxedSelfAware keeper not running or its engine crashed"))
    else:
        mem.pop("selfaware_down_since", None)
        engine = sa.get("engine") if isinstance(sa.get("engine"), Mapping) else {}
        jobs = engine.get("jobs") if isinstance(engine.get("jobs"), Mapping) else {}
        cadence = engine.get("cadence_sec") if isinstance(engine.get("cadence_sec"), Mapping) else {}
        started = parse_ts(engine.get("started_at"))
        uptime = (now - started) if started else None
        gen_at = parse_ts(sa.get("generated_at"))
        gen_age = (now - gen_at) if gen_at else None
        diag = parse_ts(dig(jobs, "diagnose", "last_ok"))
        diag_age = (now - diag) if diag else None
        red, amber = [], []
        if gen_age is None or gen_age > t["selfaware_stale_red_sec"]:
            red.append(f"health generated {fmt_age(gen_age)} ago")
        if diag_age is None or diag_age > t["selfaware_stale_red_sec"]:
            red.append(f"diagnose last_ok {fmt_age(diag_age)} ago")
        if not jobs:
            amber.append("engine jobs not reported")
        for job, info in sorted(jobs.items()):
            interval = cadence.get(job)
            if not isinstance(interval, (int, float)) or interval <= 0:
                amber.append(f"{job} cadence unknown")
                continue
            last_ok = parse_ts(dig(info, "last_ok"))
            late = (now - last_ok) if last_ok else None
            if late is None and (uptime is None or uptime > factor * interval):
                amber.append(f"{job} never succeeded")
            elif late is not None and late > factor * interval:
                amber.append(f"{job} last_ok {fmt_age(late)} ago (cadence {fmt_age(interval)})")
        st = RED if red else AMBER if amber else GREEN
        add(check("selfaware.engine", "selfaware", st,
                  "; ".join(red + amber) or f"{len(jobs)} jobs on time; diagnose {fmt_age(diag_age)} ago, health "
                                            f"generated {fmt_age(gen_age)} ago (self-aware verdict {sa.get('verdict')})",
                  sa_threshold,
                  "" if st == GREEN else "self-aware engine stalled or a job keeps failing; see :9021 engine.jobs",
                  fields={"generated_age_sec": gen_age, "diagnose_age_sec": diag_age, "late_jobs": amber,
                          "verdict": sa.get("verdict")}))

    if "fly_platform" in inputs:
        add(fly_platform_check(inputs.get("fly_platform"), checks, now))
    add(_fail_open_guard(checks, errors, source_down, now, t))
    return checks


def fly_platform_check(snapshot: Any, checks: list[dict[str, Any]], now: float) -> dict[str, Any]:
    """Fly.io status page vs our own Fly checks; annotates failing ones with the platform correlation."""
    res = fly_platform_mod.assess(snapshot if isinstance(snapshot, Mapping) else None, checks, now,
                                  fly_platform_mod.app_region())
    fly_platform_mod.annotate_checks(checks, res)
    hint = {RED: "Fly platform incident on our region/components while our Fly checks fail; likely not our bug - "
                 "follow the status page before repairing",
            AMBER: "Fly incident/maintenance on our region or components; app still healthy - watch, do not deploy",
            SKIP: "status.flyio.net unreachable; platform attribution unavailable"}.get(res["status"], "")
    return check("fly.platform_status", "fly", res["status"], res["summary"],
                 "INFO: notices outside our region/components; AMBER: incident on our region or Machines/Volumes/"
                 "proxy/deploys; RED only if our Fly checks also fail", hint,
                 fields={**fly_platform_mod.compact(res), "classification": res["classification"]})


def summarize(checks: list[dict[str, Any]], state: dict[str, Any], now: float,
              acks: Any = None) -> dict[str, Any]:
    checks = meta_mod.dedupe(checks)
    checks.append(meta_mod.flapping(checks, state, check))
    acked = meta_mod.apply_acks(checks, acks, now, parse_ts)
    last_good = state.setdefault("last_good", {})
    for c in checks:
        if c["status"] == GREEN:
            last_good[c["id"]] = now
        c["last_good_at"] = iso(last_good.get(c["id"]))
    rated = [c for c in checks if c["status"] != SKIP and not c.get("acked")]
    verdict = max((c["status"] for c in rated), key=RANK.get, default=AMBER)
    failing = [c for c in checks if c["status"] in (RED, AMBER) and not c.get("acked")]
    failing.sort(key=lambda c: -RANK[c["status"]])
    subsystems: dict[str, str] = {}
    for c in checks:
        prev = subsystems.get(c["subsystem"], SKIP)
        subsystems[c["subsystem"]] = max(prev, c["status"], key=RANK.get)
    return {
        "schema": SCHEMA,
        "generated_at": iso(now),
        "generated_ts": now,
        "host": socket.gethostname(),
        "features": list(WATCHER_FEATURES),
        "verdict": verdict,
        "counts": {s: sum(1 for c in checks if c["status"] == s) for s in (RED, AMBER, GREEN, SKIP)},
        "subsystems": subsystems,
        "failing": [{k: c[k] for k in ("id", "status", "observed", "threshold", "hint", "runbook", "last_good_at")}
                    for c in failing],
        "acked": acked,
        "checks": checks,
        "runbook": RUNBOOK,
    }


# ------------------------------------------------------------------- alarms

def alarm_transitions(report: dict[str, Any], state: dict[str, Any], now: float,
                      thresholds: Mapping[str, float] | None = None) -> list[dict[str, Any]]:
    """Edge-triggered RED alarms with sustain, dedupe, re-notify and recovery.

    AMBER transitions are logged (``AMBER``/``AMBER_CLEAR``) but not pushed.
    """
    t = {**THRESHOLDS, **(thresholds or {})}
    alarms = state.setdefault("alarms", {})
    events: list[dict[str, Any]] = []
    present = set()
    for c in report["checks"]:
        cid, st = c["id"], c["status"]
        present.add(cid)
        a = alarms.setdefault(cid, {"open": False, "streak": 0, "amber": False})
        if st == RED:
            a["streak"] = int(a.get("streak") or 0) + 1
            if not a["open"] and a["streak"] >= SUSTAIN.get(cid, 1):
                a.update(open=True, opened_at=now, notified_at=now)
                events.append(_event("OPEN", c, now))
            elif a["open"] and now - float(a.get("notified_at") or now) >= t["renotify_sec"]:
                a["notified_at"] = now
                events.append(_event("STILL_RED", c, now, opened_at=a.get("opened_at")))
        else:
            a["streak"] = 0
            if a["open"] and st != SKIP:
                events.append(_event("RECOVERED", c, now, opened_at=a.get("opened_at")))
                a.update(open=False, opened_at=None, notified_at=None)
        if st == AMBER and not a.get("amber"):
            a["amber"] = True
            events.append(_event("AMBER", c, now))
        elif st in (GREEN,) and a.get("amber"):
            a["amber"] = False
            events.append(_event("AMBER_CLEAR", c, now))
    report["open_alarms"] = sorted(cid for cid, a in alarms.items() if a.get("open") and cid in present)
    return events


def _event(kind: str, c: Mapping[str, Any], now: float, opened_at: float | None = None) -> dict[str, Any]:
    return {"schema": ALARM_SCHEMA, "at": iso(now), "event": kind, "check": c["id"], "status": c["status"],
            "observed": c["observed"], "threshold": c["threshold"], "hint": c["hint"], "runbook": c["runbook"],
            **({"opened_at": iso(opened_at), "open_for": fmt_age(now - opened_at)} if opened_at else {})}


def alarm_text(events: list[dict[str, Any]]) -> str:
    parts = []
    for e in events:
        tag = {"OPEN": "RED", "STILL_RED": "STILL RED", "RECOVERED": "RECOVERED"}.get(e["event"], e["event"])
        parts.append(f"[{tag}] {e['check']}: {e['observed']}" + (f" -> {e['hint']}" if e["event"] != "RECOVERED" and e["hint"] else ""))
    return "\n".join(parts)


def notify(events: list[dict[str, Any]], state_dir: Path, *,
           toast: Callable[[str, str], bool] | None = None) -> dict[str, Any]:
    """Windows toast for RED OPEN / STILL_RED / RECOVERED events.

    There are deliberately no chat/webhook channels: the dashboards' Alerts
    section (fed from ``alarms.jsonl``) is the primary channel.
    """
    pushed = [e for e in events if e["event"] in ("OPEN", "STILL_RED", "RECOVERED")]
    result: dict[str, Any] = {"pushed": len(pushed)}
    if not pushed:
        return result
    text = alarm_text(pushed)
    red = any(e["event"] != "RECOVERED" for e in pushed)
    title = "Doxxed RED alarm" if red else "Doxxed recovered"
    result["toast"] = (toast or windows_toast)(title, text)
    return result


def windows_toast(title: str, text: str) -> bool:
    if os.name != "nt":
        return False
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")
    body = esc(text[:600]).replace("\n", "&#10;")
    script = (
        "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime];"
        "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime];"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast scenario=\"reminder\"><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text>"
        f"<text>{body}</text></binding></visual></toast>');"
        "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, timeout=30, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def banner_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compact summary for the dashboards' banner (no secrets, bounded size)."""
    return {
        "schema": SCHEMA,
        "verdict": report["verdict"],
        "generated_at": report["generated_at"],
        "open_alarms": report.get("open_alarms", []),
        "failing": [{k: f.get(k) for k in ("id", "status", "observed", "threshold", "hint", "runbook", "last_good_at")}
                    for f in report["failing"][:12]],
        "counts": report["counts"],
        "check_status": {c["id"]: c["status"] for c in report.get("checks") or []},
        "source": f"laptop {report.get('host')}",
        "proof": report.get("proof"),
    }


def proof_summary(active: Any, now: float) -> dict[str, Any] | None:
    """48h unattended-proof progress for the uptime strip (same shape as runtime_uptime.proof_progress)."""
    try:
        sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
        import runtime_uptime  # noqa: PLC0415
    except ImportError:
        return None
    return runtime_uptime.proof_progress(active if isinstance(active, dict) else None, now)


ALARM_RETAIN_SEC = 30 * 24 * HOUR
ALARM_PUSH_CHUNK = 120
ALARM_UNSUPPORTED_RETRY_SEC = HOUR


def read_alarm_log(path: Path, now: float, max_bytes: int = 4 * 1024 * 1024) -> list[dict[str, Any]]:
    """Alarm events from the last 30 days, oldest first (bounded tail read)."""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - max_bytes))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if size > max_bytes:
        lines = lines[1:]
    events = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = parse_ts(row.get("at")) if isinstance(row, dict) else None
        if ts is not None and now - ts <= ALARM_RETAIN_SEC:
            events.append({**row, "_ts": ts})
    events.sort(key=lambda e: e["_ts"])
    return events


def alarm_chunk(events: list[dict[str, Any]], through_ts: float | None) -> list[dict[str, Any]]:
    """The next oldest-first batch Fly has not acknowledged; never splits one timestamp."""
    pending = [e for e in events if through_ts is None or e["_ts"] > through_ts + 1e-3]
    if len(pending) > ALARM_PUSH_CHUNK:
        cut = pending[ALARM_PUSH_CHUNK - 1]["_ts"]
        pending = [e for e in pending if e["_ts"] <= cut]
    trimmed = []
    for e in pending:
        row = {k: e.get(k) for k in ("at", "event", "check", "status", "observed", "threshold", "hint", "runbook",
                                     "opened_at") if e.get(k) is not None}
        for key, limit in (("observed", 240), ("threshold", 200), ("hint", 300)):
            if isinstance(row.get(key), str):
                row[key] = row[key][:limit]
        trimmed.append(row)
    return trimmed


def push_fly_banner(report: Mapping[str, Any], opts: argparse.Namespace, state: dict[str, Any] | None = None,
                    now: float | None = None, post: Callable[..., Any] = http_json) -> str:
    """POST the banner summary plus the alarm events Fly does not hold yet (Fly keeps them in memory)."""
    admin = os.environ.get("BOT_ADMIN_TOKEN") or load_vault(opts.vault).get("BOT_ADMIN_TOKEN")
    if not admin:
        return "no_admin_token"
    now = utcnow() if now is None else now
    state = {} if state is None else state
    sync = state.setdefault("fly_alarm_sync", {})
    body = banner_payload(report)
    unsupported_until = float(sync.get("unsupported_until") or 0)
    chunk: list[dict[str, Any]] = []
    if now >= unsupported_until:
        chunk = alarm_chunk(read_alarm_log(health_dir(opts) / "alarms.jsonl", now), sync.get("through_ts"))
        body["alarm_events"] = chunk
    payload, err = post(f"{opts.fly_url}/api/system-health/report", method="POST", timeout=20,
                        headers={"X-Bot-Admin-Token": admin}, body=body)
    if err:
        return err
    history = payload.get("alarm_history") if isinstance(payload, Mapping) else None
    if isinstance(history, Mapping):
        sync.update(through_ts=history.get("through_ts"), count=history.get("count"), synced_at=now,
                    unsupported_until=None)
        return f"ok alarms={history.get('count')} sent={len(chunk)}"
    if chunk:
        sync.update(unsupported_until=now + ALARM_UNSUPPORTED_RETRY_SEC, through_ts=None)
    return "ok (Fly has no alarm history endpoint yet)"


# --------------------------------------------------------------------- tick

class TickLock:
    """Non-blocking exclusive lock file: exactly one watcher evaluates at a time."""

    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def __exit__(self, *exc: Any) -> None:
        if self.handle:
            try:
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.handle.close()


def health_dir(opts: argparse.Namespace) -> Path:
    return Path(opts.state_dir) / "health"


def run_once(opts: argparse.Namespace, *, alarms: bool) -> dict[str, Any]:
    hdir = health_dir(opts)
    state_path = hdir / "health-state.json"
    state = read_json(state_path) or {}
    now = utcnow()
    inputs = collect(opts, state, now)
    checks = evaluate(inputs, state)
    previous = read_json(hdir / "system-health-latest.json") or {}
    published, _ = http_json(f"{opts.fly_url}/api/system-health", timeout=15)
    meta = meta_mod.collect_meta(state_dir=Path(opts.state_dir), shadow_root=Path(opts.shadow_root),
                                 wall=Path(opts.proof_dir) / "WALL-STATUS-FLY.md", parse_ts=parse_ts,
                                 fly_published=published)
    checks += meta_mod.meta_checks(meta, state, now, check, fmt_age,
                                   local_generated_ts=parse_ts(previous.get("generated_ts")))
    report = summarize(checks, state, now, acks=meta.get("acks"))
    report["source_errors"] = inputs.get("errors")
    report["proof"] = proof_summary(inputs.get("proof_active"), now)
    report["fly_platform"] = next((c.get("observed_fields") for c in checks if c["id"] == "fly.platform_status"), None)
    if alarms:
        events = alarm_transitions(report, state, now)
        for e in events:
            append_jsonl(hdir / "alarms.jsonl", e)
        delivery = notify(events, Path(opts.state_dir)) if not opts.no_notify else {"pushed": 0, "muted": True}
        report["last_delivery"] = delivery
        report["fly_banner"] = push_fly_banner(report, opts, state, now) if not opts.no_fly_banner else "disabled"
        meta_mod.record_delivery(state, report["fly_banner"], delivery)
        write_json_atomic(hdir / "system-health-latest.json", report)
        append_jsonl(hdir / f"verdicts-{datetime.now(timezone.utc):%Y%m}.jsonl",
                     {"at": report["generated_at"], "verdict": report["verdict"], "open_alarms": report["open_alarms"],
                      "failing": [f["id"] for f in report["failing"]]})
        write_json_atomic(state_path, state)
    return report


def tick(opts: argparse.Namespace) -> tuple[str, int]:
    hdir = health_dir(opts)
    latest = read_json(hdir / "system-health-latest.json") or {}
    age = utcnow() - float(latest.get("generated_ts") or 0)
    if age < THRESHOLDS["tick_min_interval_sec"] and not opts.force:
        ensure_server(opts)
        return f"SYSTEM_HEALTH skipped (last tick {age:.0f}s ago) verdict={latest.get('verdict')}", 0
    with TickLock(hdir / "tick.lock") as owned:
        if not owned:
            return "SYSTEM_HEALTH skipped (another watcher holds the lock)", 0
        report = run_once(opts, alarms=True)
    ensure_server(opts)
    line = (f"SYSTEM_HEALTH verdict={report['verdict']} red={report['counts'][RED]} amber={report['counts'][AMBER]} "
            f"open={','.join(report['open_alarms']) or 'none'} pushed={report['last_delivery'].get('pushed')} "
            f"fly_banner={report['fly_banner']}")
    return line, 0


def ensure_server(opts: argparse.Namespace) -> None:
    """Start the read-only local endpoint if nothing answers on its port."""
    try:
        with socket.create_connection(("127.0.0.1", opts.port), timeout=2):
            return
    except OSError:
        pass
    server = Path(__file__).resolve().parent / "system_health_server.py"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    cmd = [str(pythonw if pythonw.exists() else exe), str(server), "--state-dir", opts.state_dir, "--port", str(opts.port)]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(cmd, cwd=str(server.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        pass


def render(report: Mapping[str, Any]) -> str:
    icon = {RED: "RED  ", AMBER: "AMBER", GREEN: "ok   ", SKIP: "skip "}
    lines = [f"SYSTEM HEALTH: {report['verdict']}  ({report['generated_at']})  "
             f"red={report['counts'][RED]} amber={report['counts'][AMBER]} green={report['counts'][GREEN]}"]
    for c in sorted(report["checks"], key=lambda c: (-RANK[c["status"]], c["id"])):
        lines.append(f"  {icon[c['status']]} {c['id']:<22} {c['observed']}")
        if c["status"] in (RED, AMBER):
            lines.append(f"        threshold: {c['threshold']}")
            if c["hint"]:
                lines.append(f"        likely:    {c['hint']}")
            lines.append(f"        last good: {c.get('last_good_at') or 'never seen'}   fix: {c['runbook']}")
    if report.get("open_alarms") is not None:
        lines.append(f"open alarms: {', '.join(report['open_alarms']) or 'none'}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state-dir", default=os.environ.get("DOXXED_LAPTOP_CHAIN_STATE") or DEFAULT_STATE_DIR)
    p.add_argument("--analyzer-repo", default=DEFAULT_ANALYZER_REPO)
    p.add_argument("--mirror-tree", default=DEFAULT_MIRROR_TREE)
    p.add_argument("--shadow-root", default=DEFAULT_SHADOW_ROOT)
    p.add_argument("--exports", default=DEFAULT_EXPORTS)
    p.add_argument("--proof-dir", default=DEFAULT_PROOF_DIR)
    p.add_argument("--vault", default=DEFAULT_VAULT)
    p.add_argument("--retention-dir", default=DEFAULT_RETENTION_DIR)
    p.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR)
    p.add_argument("--fly-url", default=FLY_URL)
    p.add_argument("--analyzer-url", default=ANALYZER_URL)
    p.add_argument("--selfaware-url", default=SELFAWARE_URL)
    p.add_argument("--port", type=int, default=SERVER_PORT)
    p.add_argument("--json", action="store_true")
    p.add_argument("--tick", action="store_true", help="watcher tick: evaluate, alarm, publish")
    p.add_argument("--latest", action="store_true", help="print the last published verdict")
    p.add_argument("--force", action="store_true", help="tick even inside the minimum interval")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--no-fly-banner", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    if "onedrive" in str(Path(opts.state_dir).resolve()).lower():
        print("refusing a OneDrive state directory", file=sys.stderr)
        return 2
    if opts.tick:
        line, code = tick(opts)
        print(line)
        return code
    if opts.latest:
        report = read_json(health_dir(opts) / "system-health-latest.json")
        if not report:
            print("no published verdict yet", file=sys.stderr)
            return 2
    else:
        report = run_once(opts, alarms=False)
    print(json.dumps(report, indent=2, default=str) if opts.json else render(report))
    return {GREEN: 0, AMBER: 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
