"""Agent-facing helper: one call returns the whole system state.

    import health_client
    snap = health_client.snapshot()          # last published verdict (fast)
    snap = health_client.snapshot(live=True) # fresh evaluation (~20s, read-only)
    snap["verdict"]                          # GREEN / AMBER / RED
    for f in health_client.failing(snap):    # failing checks, worst first
        print(f["id"], f["observed"], f["hint"], f["runbook"])
    hist = health_client.alerts(limit=50)    # alert history, active first then newest first
    for a in hist["alerts"]:
        print(a["severity"], a["started"]["aest"], a["title"], a["duration_text"])

Order of sources: the local endpoint (127.0.0.1:9011), then the published
file, then an in-process evaluation. ``python scripts/health_client.py``
prints a one-screen summary; ``--json`` prints the snapshot.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system_health as sh  # noqa: E402

ENDPOINT = f"http://127.0.0.1:{sh.SERVER_PORT}/api/system-health"


def snapshot(live: bool = False, *, endpoint: str = ENDPOINT, state_dir: str = sh.DEFAULT_STATE_DIR,
             timeout: float = 90.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(endpoint + ("?live=1" if live else ""), timeout=timeout) as response:
            report = json.loads(response.read().decode("utf-8"))
        report.setdefault("source", "endpoint")
        return report
    except (OSError, ValueError):
        pass
    if not live:
        import system_health_server as server  # noqa: PLC0415

        report = server.published(state_dir)
        if "checks" in report:
            report["source"] = "file"
            return report
    report = sh.run_once(sh.parse_args(["--state-dir", state_dir, "--no-notify", "--no-fly-banner"]), alarms=False)
    report["source"] = "in_process"
    return report


def alerts(limit: int | None = None, *, state_dir: str = sh.DEFAULT_STATE_DIR) -> dict[str, Any]:
    """Alert history (same data as the dashboards' Alerts section), read from the local alarm log."""
    sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
    import system_health_alerts  # noqa: PLC0415

    health = Path(state_dir) / "health"
    return system_health_alerts.history_from_file(health / "alarms.jsonl", health / "system-health-latest.json",
                                                  limit=limit)


def failing(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report.get("failing") or [])


def verdict(report: dict[str, Any] | None = None) -> str:
    return (report or snapshot()).get("verdict", sh.AMBER)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    report = snapshot(live="--live" in argv)
    if "--json" in argv:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"{report.get('verdict')} at {report.get('generated_at')} (source={report.get('source')}, "
              f"age={report.get('age_sec')}s, open alarms={report.get('open_alarms')})")
        for f in failing(report):
            print(f"  {f['status']:<5} {f['id']}: {f['observed']}\n        -> {f.get('hint')}  [{f.get('runbook')}]")
    return {"GREEN": 0, "AMBER": 1}.get(report.get("verdict"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
