# Founder Node

Self-custody desktop vault for Founder OS project memory — your machine, your data.

Founder Node stores roadmaps, AI context, and build memory in `~/FounderVault/` on your laptop. Founder OS stays the control plane; the node syncs **metadata only** (never your full vault contents).

## Quick start (recommended)

### 1. Download & install

| Platform | Download |
|----------|----------|
| **Windows** | [Founder-Node-0.2.0-win-x64.exe](https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest) |
| **macOS** | [Founder-Node-0.2.0-mac-x64.dmg](https://github.com/danishhaiderau-maker/doxed-founders-website/releases/latest) |

Or open **[doxxedcrypto.digital/founder-node](https://doxxedcrypto.digital/founder-node)** for one-click download buttons.

> **Windows:** SmartScreen may warn because the app is not code-signed yet — choose **More info → Run anyway**.  
> **macOS:** First launch may require **right-click → Open** (unsigned app).

### 2. Launch the app

After install, Founder Node runs in your **system tray** (Windows notification area / macOS menu bar).  
On first launch, a **Pair this machine** window opens automatically.

### 3. Generate a pairing code in Founder OS

1. Sign in at [doxxedcrypto.digital](https://doxxedcrypto.digital)
2. Go to **Settings → Builder** ([direct link](https://doxxedcrypto.digital/settings/builder))
3. Under **Memory storage**, choose **Founder Node**
4. Click **Generate pairing code** and copy the 8-character code

### 4. Pair your machine

1. In the Founder Node tray app, click **Pair with Founder OS…** (or use the window that opened on launch)
2. Confirm **Founder OS URL** is `https://doxxedcrypto.digital` (or your self-hosted URL)
3. Paste the pairing code and click **Connect vault**

When paired, sync runs automatically every 60 seconds. Right-click the tray icon → **Sync now** for manual sync.

### 5. Your vault location

```
~/FounderVault/
  meta.json           # project + node metadata
  project-context.md  # AI / founder context
  roadmap.md          # milestones
  tasks.json          # build queue
  node-config.json    # pairing token (keep private)
```

---

## What syncs vs what stays local

| Stays on your machine | Synced to Founder OS (metadata only) |
|-----------------------|--------------------------------------|
| Full vault files | Project name, task counts, health status |
| Roadmap & context text | Device label, platform, app version |
| node-config.json (secrets) | Last-seen heartbeat |

---

## Troubleshooting

**Pairing failed / invalid code**  
Codes expire after a few minutes. Generate a new code in Builder settings and try again.

**Tray icon missing**  
Check the hidden icons area (Windows) or menu bar (macOS). Restart Founder Node from Start Menu / Applications.

**Sync errors**  
Confirm you are signed in on the web, the node shows as connected in Builder settings, and your internet is up. Try **Sync now** from the tray menu.

**Re-pair on a new laptop**  
Install Founder Node on the new machine, generate a new pairing code, and pair again. Each machine gets its own vault path.

---

## Developers — run from source

```bash
git clone https://github.com/danishhaiderau-maker/doxed-founders-website.git
cd doxed-founders-website
npm install
npm run dev:founder-node
```

Build installers locally:

```bash
npm run pack:founder-node:win   # Windows .exe
npm run pack:founder-node:mac   # macOS .dmg (on Mac)
```

Release tag (CI builds both platforms):

```bash
git tag founder-node-v0.2.0
git push origin founder-node-v0.2.0
```

---

## Privacy

- No database credentials or API keys are bundled in the installer
- Your vault never uploads full file contents — only lightweight metadata for resume/sync status
- Pairing tokens live in `~/FounderVault/node-config.json` — do not share this file
