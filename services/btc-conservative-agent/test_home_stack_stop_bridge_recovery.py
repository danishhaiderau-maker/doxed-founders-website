"""Regression check: global Stop must leave a usable bridge-only command path."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER = (ROOT / "scripts" / "home-stack-cmd-worker.ps1").read_text(
    encoding="utf-8"
)
STOP_SCRIPT = (ROOT / "scripts" / "home-stack-stop-everything.ps1").read_text(
    encoding="utf-8"
)
COMMON = (ROOT / "scripts" / "home-stack-common.ps1").read_text(encoding="utf-8")
WATCHDOG = (
    ROOT / "scripts" / "home-stack-supervisor-watchdog.ps1"
).read_text(encoding="utf-8")


def main() -> None:
    assert "function Restore-BridgeAfterGlobalStop" in WORKER
    assert "if (Test-BridgeHealthy) { return }" in WORKER
    assert (
        'Start-HiddenPs1 (Join-Path $scriptDir "ensure-home-bridge.ps1")'
        in WORKER
    )
    assert WORKER.count("Restore-BridgeAfterGlobalStop") >= 3
    for marker in (
        ".home-bot-starter.pid",
        ".home-bot-crash-monitor.pid",
        ".home-analyzer-starter.pid",
        ".home-analyzer-crash-monitor.pid",
        ".home-stack-supervisor.heartbeat",
        ".home-relay-pusher.pid",
        ".home-cloudflared.pid",
    ):
        assert marker in STOP_SCRIPT, f"global Stop must clear stale {marker}"
        assert marker in COMMON, f"fast global Stop must clear stale {marker}"
    assert ".home-stack-user-stopped" in WATCHDOG
    assert "supervisor restart skipped - user stopped stack" in WATCHDOG
    assert '.home-bot-crash-monitor.pid")' in COMMON
    assert '.home-analyzer-crash-monitor.pid")' in COMMON
    print("PASS: global Stop restores bridge-only command path")


if __name__ == "__main__":
    main()
