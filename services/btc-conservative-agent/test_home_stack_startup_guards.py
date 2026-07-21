"""Static regression checks for Windows one-owner Start/Stop safety."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMON = (ROOT / "scripts" / "home-stack-common.ps1").read_text(encoding="utf-8")
START = (ROOT / "scripts" / "start-home-bot.ps1").read_text(encoding="utf-8")


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"  [PASS] {name}")


def main() -> None:
    check(
        "listener cleanup always merges netstat owners",
        "Always merge native netstat results" in COMMON
        and "netstat.exe -ano -p TCP" in COMMON,
    )
    check(
        "listener cleanup has a taskkill fallback",
        "taskkill.exe /PID $procId /T /F" in COMMON,
    )
    check(
        "start refuses an occupied port after cleanup",
        "refusing duplicate bot start" in START
        and START.count("Test-PortOpen $BotListenPort") >= 3,
    )
    print("PASS: home stack one-owner startup guards")


if __name__ == "__main__":
    main()
