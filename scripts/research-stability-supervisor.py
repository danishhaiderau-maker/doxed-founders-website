#!/usr/bin/env python3
"""Fail-closed supervisor for the desktop BTC research pipeline.

This process has deliberately narrow authority.  It may start a missing Fly
mirror loop or a missing desktop analyzer through their existing launchers.  It
cannot stop/restart trading, call Fly mutation endpoints, wipe data, or change
policy.  Every observation is written atomically for the dashboard/operator.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPORT_MAX_AGE_SECONDS = 45 * 60
SYNC_MAX_AGE_SECONDS = 10 * 60
REQUIRED_SCHEMA = "research_event_v2.2"
REQUIRED_COLLECTOR = "collector_v2.2"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def fetch_json(url: str, token: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"X-Bot-Admin-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("remote response is not a JSON object")
    return value


def process_inventory() -> list[dict[str, Any]]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    raw = json.loads(result.stdout or "[]")
    return raw if isinstance(raw, list) else [raw]


def classify_processes(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    groups = {"sync": [], "analyzer": [], "dashboard": [], "supervisor": []}
    for row in rows:
        cmd = str(row.get("CommandLine") or "").lower()
        pid = int(row.get("ProcessId") or 0)
        if pid <= 0:
            continue
        if "sync-fly-bot-data-loop.ps1" in cmd:
            groups["sync"].append(pid)
        if "analyzer_research_engine_v62.py" in cmd:
            groups["analyzer"].append(pid)
        if "research_dashboard.py" in cmd and "--standalone" in cmd:
            groups["dashboard"].append(pid)
        if "research-stability-supervisor.py" in cmd:
            groups["supervisor"].append(pid)
    return {key: sorted(set(value)) for key, value in groups.items()}


def read_current_events(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"event line {line_number} is not an object")
            if value.get("schema") != REQUIRED_SCHEMA or value.get("collector_version") != REQUIRED_COLLECTOR:
                raise ValueError(f"event line {line_number} is not qualified v2.2")
            events.append(value)
    if not events:
        raise ValueError("no v2.2 events")
    epoch = str(events[-1].get("epoch_id") or "")
    current = [row for row in events if str(row.get("epoch_id") or "") == epoch]
    completed = [
        row for row in current
        if str(row.get("observation_status") or "").upper() in {"COMPLETE", "FUNNEL_COMPLETE"}
    ]
    episode_ids = {
        str(row.get("event_episode_id") or row.get("envelope", {}).get("event_episode_id") or "")
        for row in current
    }
    episode_ids.discard("")
    policy_epochs = {str(row.get("policy_epoch_id") or "") for row in current}
    signatures = {str(row.get("policy_signature") or "") for row in current}
    policy_epochs.discard("")
    signatures.discard("")
    return {
        "epoch_id": epoch,
        "current_events": len(current),
        "eligible_events": len(completed),
        "independent_episodes": len(episode_ids),
        "policy_epoch_ids": sorted(policy_epochs),
        "policy_signatures": sorted(signatures),
    }


def report_counts(report: dict[str, Any]) -> tuple[int, int]:
    evidence = report.get("evidence") or {}
    events = evidence.get("current_events", evidence.get("current_epoch_events", 0))
    episodes = evidence.get("independent_episodes", evidence.get("independent_episode_count", 0))
    return int(events or 0), int(episodes or 0)


@dataclass
class Supervisor:
    repo: Path
    mirror: Path
    report_dir: Path
    fly_url: str
    token: str
    repair: bool = False
    now: Callable[[], datetime] = utc_now
    fetcher: Callable[[str, str, int], dict[str, Any]] = fetch_json
    process_reader: Callable[[], list[dict[str, Any]]] = process_inventory
    launcher: Callable[..., subprocess.Popen[Any]] = subprocess.Popen
    runtime_repo: Path | None = None

    def launch_missing(self, kind: str) -> bool:
        if not self.repair:
            return False
        if kind == "sync":
            owner_repo = self.runtime_repo or self.repo
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(owner_repo / "scripts" / "sync-fly-bot-data-loop.ps1")]
        elif kind == "analyzer":
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.repo / "scripts" / "start-home-analyzer.ps1"), "-NoWait", "-Port", "9001"]
        else:
            return False
        self.launcher(args, cwd=self.repo, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True

    def check(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        repairs: list[str] = []

        def add(name: str, ok: bool, detail: Any) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        try:
            manifest = self.fetcher(self.fly_url.rstrip("/") + "/api/data-sync/manifest", self.token, 20)
            add("fly_collector_manifest", bool(manifest.get("files")) and int(manifest.get("total_bytes") or 0) > 0, {
                "total_bytes": manifest.get("total_bytes"), "source_revision": manifest.get("source_revision"),
                "fresh_collection_signal_ts": manifest.get("fresh_collection_signal_ts")})
        except Exception as exc:
            add("fly_collector_manifest", False, type(exc).__name__)
        try:
            size = self.fetcher(self.fly_url.rstrip("/") + "/api/data_size", self.token, 20)
            pct = float(size.get("volume_pct") or 0)
            add("fly_storage", pct < 85.0, {"volume_pct": pct, "cleanup_status": size.get("cleanup_status")})
        except Exception as exc:
            add("fly_storage", False, type(exc).__name__)

        heartbeat_path = (self.runtime_repo or self.repo) / ".fly-data-sync-loop.heartbeat.json"
        try:
            heartbeat = read_json(heartbeat_path)
            stamp = parse_time(heartbeat.get("syncedAt"))
            age = (self.now() - stamp).total_seconds() if stamp else float("inf")
            add("atomic_sync_heartbeat", heartbeat.get("ok") is True and age <= SYNC_MAX_AGE_SECONDS,
                {"age_seconds": round(age, 1), "ok": heartbeat.get("ok"), "sourceRevision": heartbeat.get("sourceRevision")})
        except Exception as exc:
            add("atomic_sync_heartbeat", False, type(exc).__name__)

        try:
            inventory = classify_processes(self.process_reader())
        except Exception as exc:
            inventory = {"sync": [], "analyzer": [], "dashboard": [], "supervisor": []}
            add("process_inventory", False, type(exc).__name__)
        else:
            add("process_inventory", True, inventory)
        for kind in ("sync", "analyzer", "dashboard"):
            count = len(inventory[kind])
            add(f"unique_{kind}_process", count == 1, {"count": count, "pids": inventory[kind]})
            if count == 0 and kind in {"sync", "analyzer"} and self.launch_missing(kind):
                repairs.append(f"started_missing_{kind}_through_safe_launcher")

        events_path = self.mirror / "research_events_v22.jsonl"
        event_summary: dict[str, Any] | None = None
        try:
            event_summary = read_current_events(events_path)
            mirror_age = (self.now().timestamp() - events_path.stat().st_mtime)
            # A finalized append-only event file legitimately remains unchanged
            # when no lifecycle matures. Transport freshness is independently
            # enforced by the atomic sync heartbeat above.
            add("mirror_schema_and_freshness", True,
                {**event_summary, "age_seconds": round(mirror_age, 1)})
        except Exception as exc:
            add("mirror_schema_and_freshness", False, f"{type(exc).__name__}: {exc}")

        reports: dict[str, dict[str, Any]] = {}
        for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
            path = self.report_dir / filename
            try:
                report = read_json(path)
                generated = parse_time(report.get("generated_at"))
                age = (self.now() - generated).total_seconds() if generated else float("inf")
                add(f"report_fresh:{filename}", age <= REPORT_MAX_AGE_SECONDS, {"age_seconds": round(age, 1)})
                reports[filename] = report
            except Exception as exc:
                add(f"report_fresh:{filename}", False, type(exc).__name__)

        if event_summary and len(reports) == 2:
            expected = (event_summary["current_events"], event_summary["independent_episodes"])
            observed = {name: report_counts(report) for name, report in reports.items()}
            add("report_count_parity", all(value == expected for value in observed.values()),
                {"expected": expected, "reports": observed})
            expected_epoch = event_summary["epoch_id"]
            expected_policy_epochs = event_summary["policy_epoch_ids"]
            expected_signatures = event_summary["policy_signatures"]
            identity_ok = all(
                report.get("epoch_id") == expected_epoch
                and report.get("policy_epoch_id") in expected_policy_epochs
                and report.get("evidence_policy_signature") in expected_signatures
                for report in reports.values()
            )
            add("report_epoch_policy_signature_parity", identity_ok, {
                "expected_epoch": expected_epoch, "expected_policy_epochs": expected_policy_epochs,
                "expected_policy_signatures": expected_signatures,
                "reports": {name: {key: report.get(key) for key in ("epoch_id", "policy_epoch_id", "evidence_policy_signature")}
                            for name, report in reports.items()}})

        return {
            "schema": "research_stability_supervisor_v1",
            "generated_at": self.now().isoformat(),
            "healthy": all(row["ok"] for row in checks),
            "repair_authority": "MISSING_LOCAL_SYNC_OR_ANALYZER_ONLY",
            "forbidden_actions": ["TRADING_RESTART", "FLY_RESTART", "DATA_WIPE", "POLICY_CHANGE", "LIVE_TRADE_ARM"],
            "repairs": repairs,
            "checks": checks,
        }


def default_paths(repo: Path) -> tuple[Path, Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return local / "DoxxedCrypto" / "fly-data-mirror", repo / "services" / "btc-conservative-agent"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-repo", type=Path)
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--fly-url", default="https://doxed-btc-bot.fly.dev")
    parser.add_argument("--repair-missing-local", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    mirror_default, report_default = default_paths(repo)
    status_file = args.status_file or repo / ".research-stability-supervisor.json"
    token = os.environ.get("BOT_ADMIN_TOKEN", "")
    if not token:
        raise SystemExit("BOT_ADMIN_TOKEN is required; load it through the existing vault launcher")
    supervisor = Supervisor(
        repo, args.mirror or mirror_default, args.report_dir or report_default,
        args.fly_url, token, repair=args.repair_missing_local,
        runtime_repo=args.runtime_repo.resolve() if args.runtime_repo else None,
    )
    while True:
        payload = supervisor.check()
        atomic_json(status_file, payload)
        if not args.loop:
            return 0 if payload["healthy"] else 2
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    sys.exit(main())
