import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain } from 'electron';
import os from 'node:os';
import {
  defaultVaultRoot,
  ensureVault,
  loadOrCreateNodeId,
  readNodeConfig,
  writeNodeConfig,
  buildSnapshotFromVault,
  vaultDiskStats,
} from './vault-manager';
import {
  defaultHeartbeat,
  pairNode,
  sendHeartbeat,
  syncVaultMetadata,
} from './sync-client';
import { FOUNDER_NODE_APP_VERSION } from '@dcf/founder-vault';

const DEFAULT_API = process.env.FOUNDER_OS_API_URL ?? 'https://doxxedcrypto.digital';
const SYNC_INTERVAL_MS = 60_000;

let tray: Tray | null = null;
let pairWindow: BrowserWindow | null = null;
let syncTimer: ReturnType<typeof setInterval> | null = null;

function startSyncLoop(vaultRoot: string) {
  if (syncTimer) return;
  runSyncCycle(vaultRoot).catch(console.error);
  syncTimer = setInterval(() => runSyncCycle(vaultRoot).catch(console.error), SYNC_INTERVAL_MS);
}

async function runSyncCycle(vaultRoot: string): Promise<void> {
  const config = readNodeConfig(vaultRoot);
  if (!config) return;

  const snapshot = buildSnapshotFromVault(vaultRoot, config.label);
  const disk = vaultDiskStats(vaultRoot);

  await sendHeartbeat(config.apiBaseUrl, config.nodeId, config.nodeToken, {
    ...defaultHeartbeat(config.label, vaultRoot),
    nodeId: config.nodeId,
    storageGb: disk.storageGb,
    storageFreeGb: disk.storageFreeGb,
  });

  await syncVaultMetadata(config.apiBaseUrl, config.nodeId, config.nodeToken, snapshot);
}

function openPairWindow(): void {
  if (pairWindow) {
    pairWindow.focus();
    return;
  }

  pairWindow = new BrowserWindow({
    width: 440,
    height: 520,
    title: 'Pair Founder Node',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Pair Founder Node</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0a0a0f; color: #e4e4e7; padding: 24px; margin: 0; }
  h1 { font-size: 1.25rem; margin: 0 0 8px; }
  p { color: #a1a1aa; font-size: 0.875rem; line-height: 1.5; }
  label { display: block; font-size: 0.75rem; color: #a1a1aa; margin: 16px 0 6px; }
  input { width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 8px; border: 1px solid #3f3f46; background: #18181b; color: white; }
  button { margin-top: 20px; width: 100%; padding: 12px; border: none; border-radius: 8px; background: #7c3aed; color: white; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.5; }
  .err { color: #f87171; font-size: 0.8rem; margin-top: 12px; }
  .ok { color: #34d399; font-size: 0.8rem; margin-top: 12px; }
  code { background: #27272a; padding: 2px 6px; border-radius: 4px; }
</style></head><body>
  <h1>Pair this machine</h1>
  <p>Generate a code in <strong>Founder OS → Settings → Builder</strong>, choose <strong>Founder Node</strong>, then enter it here.</p>
  <label>Founder OS URL</label>
  <input id="api" value="${DEFAULT_API}" />
  <label>Pairing code</label>
  <input id="code" placeholder="8-character code" maxlength="12" />
  <label>Node label</label>
  <input id="label" value="${os.hostname()} Founder Node" />
  <button id="pair">Connect vault</button>
  <div id="msg"></div>
  <script>
    const { ipcRenderer } = require('electron');
    document.getElementById('pair').onclick = async () => {
      const btn = document.getElementById('pair');
      const msg = document.getElementById('msg');
      btn.disabled = true;
      msg.textContent = '';
      msg.className = '';
      try {
        await ipcRenderer.invoke('pair', {
          apiBaseUrl: document.getElementById('api').value.trim(),
          code: document.getElementById('code').value.trim(),
          label: document.getElementById('label').value.trim(),
        });
        msg.textContent = 'Paired! Vault sync will run in the background.';
        msg.className = 'ok';
        setTimeout(() => window.close(), 1500);
      } catch (e) {
        msg.textContent = e.message || String(e);
        msg.className = 'err';
        btn.disabled = false;
      }
    };
  </script>
</body></html>`;

  pairWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  pairWindow.on('closed', () => {
    pairWindow = null;
  });
}

function buildTrayMenu(vaultRoot: string) {
  const config = readNodeConfig(vaultRoot);
  return Menu.buildFromTemplate([
    {
      label: config ? `Connected: ${config.label}` : 'Not paired',
      enabled: false,
    },
    { type: 'separator' },
    {
      label: 'Pair with Founder OS…',
      click: () => openPairWindow(),
    },
    {
      label: 'Sync now',
      enabled: Boolean(config),
      click: () => {
        runSyncCycle(vaultRoot).catch(console.error);
      },
    },
    { type: 'separator' },
    { label: `Vault: ${vaultRoot}`, enabled: false },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() },
  ]);
}

app.whenReady().then(() => {
  const backgroundKeeper = new BrowserWindow({ show: false });
  backgroundKeeper.hide();

  const vaultRoot = defaultVaultRoot();
  const nodeId = loadOrCreateNodeId(vaultRoot);
  ensureVault(vaultRoot, nodeId);

  const icon = nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip('Founder Node');
  tray.setContextMenu(buildTrayMenu(vaultRoot));
  tray.on('click', () => tray?.popUpContextMenu());

  const config = readNodeConfig(vaultRoot);
  if (!config) {
    openPairWindow();
  } else {
    startSyncLoop(vaultRoot);
  }

  ipcMain.handle(
    'pair',
    async (
      _event: unknown,
      input: { apiBaseUrl: string; code: string; label: string },
    ) => {
      const result = await pairNode(input.apiBaseUrl, {
        code: input.code,
        nodeId,
        label: input.label || `${os.hostname()} Founder Node`,
        platform: process.platform,
        appVersion: FOUNDER_NODE_APP_VERSION,
      });

      writeNodeConfig(vaultRoot, {
        version: 1,
        apiBaseUrl: input.apiBaseUrl.replace(/\/$/, ''),
        nodeId: result.nodeId,
        nodeToken: result.nodeToken,
        label: input.label || `${os.hostname()} Founder Node`,
        pairedAt: new Date().toISOString(),
      });

      tray?.setContextMenu(buildTrayMenu(vaultRoot));
      startSyncLoop(vaultRoot);
    },
  );
});

app.on('window-all-closed', () => {
  // Tray app — keep running in background
});
