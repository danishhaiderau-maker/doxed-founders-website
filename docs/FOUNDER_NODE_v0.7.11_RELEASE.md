# Founder Node v0.7.11

**Tag:** `founder-node-v0.7.11`  
**Fixes:** Windows Cursor dispatch PowerShell scripts, honest mobile dispatch status, scroll/typing UX

## What changed

- **PowerShell dispatch:** Multiline `.ps1` temp files fix here-string parsing so phone→Cursor dispatches no longer fail on Windows.
- **Delivery status:** Web UI polls dispatch outcome and shows queued / delivering / delivered / failed honestly on mobile and desktop.
- **Mobile UX:** Stable chat scroll, agent typing strip after successful Cursor delivery.
- **Attribution:** Dispatched prompts include `[Founder OS → Cursor]` prefix in Cursor.

## Upgrade from v0.7.9 / v0.7.10

1. **Quit Founder Node completely** — right-click the tray icon → **Quit**.
2. Download and install **Founder-Node-0.7.11-win-x64.exe** from GitHub Releases.
3. Launch Founder Node from Start Menu; confirm tray tooltip shows **v0.7.11**.
4. Hard refresh Founder OS in the browser (`Ctrl+Shift+R`) so the web UI enforces v0.7.11+.

The tray menu checks for updates hourly. After installing, use **Sync now** once before remote dispatch from your phone.
