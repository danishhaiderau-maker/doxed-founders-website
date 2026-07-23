"""Static regression checks for Windows one-owner Start/Stop safety."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMON = (ROOT / "scripts" / "home-stack-common.ps1").read_text(encoding="utf-8")
START = (ROOT / "scripts" / "start-home-bot.ps1").read_text(encoding="utf-8")
HEALTH = (ROOT / "scripts" / "home-stack-health.ps1").read_text(encoding="utf-8")
MONITOR = (ROOT / "scripts" / "bot-auto-restart.ps1").read_text(encoding="utf-8")
AUTOSTART = (ROOT / "scripts" / "register-bot-autostart.ps1").read_text(
    encoding="utf-8"
)
BOT = (
    ROOT / "services" / "btc-conservative-agent" / "bot.py"
).read_text(encoding="utf-8")
BOT_HUNG = HEALTH.split("function Test-BotHung", 1)[1].split(
    "function Test-AnalyzerHung", 1
)[0]


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"  [PASS] {name}")


def main() -> None:
    check(
        "listener ownership uses non-blocking native netstat",
        "function Get-ListenPortOwners" in COMMON
        and "netstat.exe -ano -p TCP" in COMMON
        and "Get-NetTCPConnection -LocalPort $ListenPort" not in COMMON,
    )
    check(
        "listener cleanup has a taskkill fallback",
        "taskkill.exe /PID $procId /T /F" in COMMON
        and "Unable to stop listener PID $procId" in COMMON
        and "$killed += $procId" in COMMON,
    )
    check(
        "start refuses an occupied port after cleanup",
        "refusing duplicate bot start" in START
        and START.count("Test-PortBound $BotListenPort") >= 3,
    )
    check(
        "hung detection includes bound-but-unconnectable listeners",
        "Test-PortBound $BotPort" in BOT_HUNG
        and 'Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 20' in BOT_HUNG,
    )
    check(
        "auto-restart supervises liveness instead of process existence only",
        "function Test-BotPingQuick" in MONITOR
        and "consecutiveLivenessFailures -ge 3" in MONITOR
        and "bot process alive but /api/ping unavailable" in MONITOR,
    )
    check(
        "auto-restart never calls the blocking TCP provider",
        "Stop-ListenPortFast $Port" in MONITOR
        and "Get-NetTCPConnection -LocalPort $Port" not in MONITOR,
    )
    check(
        "autostart has a current-user fallback when admin rights are unavailable",
        "$runLevel = if ($isAdministrator) { 'Highest' } else { 'Limited' }"
        in AUTOSTART
        and "-RunLevel $runLevel" in AUTOSTART,
    )
    check(
        "autostart preserves an existing administrator-owned task",
        "Existing administrator-owned task" in AUTOSTART
        and "if ($_.Exception.Message -match 'Access is denied')" in AUTOSTART,
    )
    check(
        "Bitfinex market metadata cannot block normal bot import",
        "EAGER_EXCHANGE_MARKET_LOAD = (" in BOT
        and "if EAGER_EXCHANGE_MARKET_LOAD" in BOT
        and "else {}" in BOT,
    )
    print("PASS: home stack one-owner startup guards")


if __name__ == "__main__":
    main()
