"""Side-effect-free checks for authenticated home-stack trading controls."""

from pathlib import Path

root = Path(__file__).parents[2]
launcher = (root / "scripts" / "home-stack-launcher.ps1").read_text(encoding="utf-8")
worker = (root / "scripts" / "home-stack-cmd-worker.ps1").read_text(encoding="utf-8")

checks = {
    "bridge loads BOT_ADMIN_TOKEN from the external vault": (
        "function Import-BotAdminToken" in launcher
        and "doxedcryptofounder-secrets\\vault\\home-bot.env" in launcher
    ),
    "bridge authenticates pause/resume requests": (
        '"X-Bot-Admin-Token"' in launcher
        and "-Headers $headers" in launcher
    ),
    "background worker loads admin headers": (
        "function Get-BotAdminHeaders" in worker
        and '"X-Bot-Admin-Token"' in worker
    ),
    "background worker authenticates both controls": (
        worker.count("-Headers (Get-BotAdminHeaders)") == 2
    ),
    "forced bridge recovery is not swallowed by PowerShell parsing": (
        "if ((Test-BridgeHealthy) -and -not $Force)" in (
            root / "scripts" / "ensure-home-bridge.ps1"
        ).read_text(encoding="utf-8")
    ),
}

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
if failed:
    raise SystemExit(f"failed: {', '.join(failed)}")
print(f"PASS: {len(checks)} authenticated home-stack control checks")
