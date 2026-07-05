# Founder Node v0.7.9

**Tag:** `founder-node-v0.7.9`  
**Fixes:** Cursor dispatch focus guard (browser/Bing hijack), triple-paste dedupe

## What changed

- **Cursor dispatch:** Removed Ctrl+L entirely. Foreground is verified by **Cursor.exe process name** (not window title). Focus check and SendKeys run in a **single PowerShell process** so keys cannot land in the browser between calls.
- **Enter gating:** Submit Enter only after a second verified-focus check with delay.
- **Dedupe:** Stronger API (60s), node fingerprint (60s), web send guard (12s), and awaited dispatch cycle.

## Upgrade from v0.7.7 / v0.7.8

1. **Quit Founder Node completely** — right-click the tray icon → **Quit** (not just close a window).
2. Download and install **Founder-Node-0.7.9-win-x64.exe** from GitHub Releases.
3. Launch Founder Node from Start Menu; confirm tray tooltip shows **v0.7.9**.
4. Hard refresh Founder OS in the browser (`Ctrl+Shift+R`) so the web UI enforces v0.7.9+.

The tray menu checks for updates hourly. After installing, use **Sync now** once before demo recording.
