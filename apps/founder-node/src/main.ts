import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, Notification } from 'electron';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { configureSharedElectronUserData } from './app-paths';
import { enforceSingleFounderNodeInstance, releaseGlobalInstanceLock } from './single-instance';
import {
  defaultVaultRoot,
  ensureVault,
  loadOrCreateNodeId,
  readNodeConfig,
  clearNodeConfig,
  writeNodeConfig,
  buildSnapshotFromVault,
  vaultDiskStats,
} from './vault-manager';
import {
  defaultHeartbeat,
  pairNode,
  isFounderNodeAuthError,
  sendHeartbeat,
  syncVaultMetadata,
} from './sync-client';
import { defaultOllamaConfig, probeOllama } from './ollama-client';
import { processPendingInference } from './inference-client';
import { maybeRebuildVectorIndex, processPendingSyncJobs } from './sync-jobs-client';
import { FOUNDER_NODE_APP_VERSION, buildVaultEncryptedBlob, deriveVaultKey, encryptVaultJson } from '@dcf/founder-vault';
import { cleanupLegacyPortableInstallers } from './legacy-cleanup';
import {
  bindUpdateTray,
  checkForUpdates,
  downloadAndInstallUpdate,
  getPendingUpdate,
  setUpdateMenuRefresh,
  startAutoUpdateChecks,
} from './update-manager';

const DEFAULT_API = process.env.FOUNDER_OS_API_URL ?? 'https://doxxedcrypto.digital';
const SYNC_INTERVAL_MS = 60_000;
const INFERENCE_POLL_MS = 3_000;
const SYNC_JOB_POLL_MS = 1_500;

configureSharedElectronUserData();

/** Cross-path lock (portable + NSIS) then Electron single-instance. */
if (!enforceSingleFounderNodeInstance()) {
  app.quit();
  process.exit(0);
}

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
  process.exit(0);
}

let tray: Tray | null = null;
let pairWindow: BrowserWindow | null = null;
let syncTimer: ReturnType<typeof setInterval> | null = null;
let inferenceTimer: ReturnType<typeof setInterval> | null = null;
let syncJobTimer: ReturnType<typeof setInterval> | null = null;
let syncJobInFlight = false;
let lastSyncOkAt: Date | null = null;
let lastSyncError: string | null = null;

function notifyDesktop(title: string, body: string): void {
  if (!Notification.isSupported()) return;
  try {
    new Notification({ title, body }).show();
  } catch {
    /* optional on some Linux setups */
  }
}

function formatLastSyncLine(): string {
  if (lastSyncError) {
    return `Last sync failed: ${lastSyncError.slice(0, 72)}`;
  }
  if (lastSyncOkAt) {
    const mins = Math.max(0, Math.floor((Date.now() - lastSyncOkAt.getTime()) / 60_000));
    return mins < 1 ? 'Last sync: just now' : `Last sync: ${mins}m ago`;
  }
  return 'Last sync: waiting…';
}

function loadAppIcon() {
  const candidates = [
    path.join(process.resourcesPath, 'icon.png'),
    path.join(__dirname, '../build/icon.png'),
    path.join(__dirname, '../../build/icon.png'),
  ];
  for (const iconPath of candidates) {
    if (fs.existsSync(iconPath)) {
      const img = nativeImage.createFromPath(iconPath);
      if (!img.isEmpty()) return img;
    }
  }
  return nativeImage.createEmpty();
}

function startSyncLoop(vaultRoot: string) {
  if (syncTimer) return;
  runSyncCycle(vaultRoot).catch(console.error);
  syncTimer = setInterval(() => runSyncCycle(vaultRoot).catch(console.error), SYNC_INTERVAL_MS);
  if (!inferenceTimer) {
    inferenceTimer = setInterval(() => runInferenceCycle(vaultRoot).catch(console.error), INFERENCE_POLL_MS);
  }
  if (!syncJobTimer) {
    runSyncJobCycle(vaultRoot).catch(console.error);
    syncJobTimer = setInterval(() => runSyncJobCycle(vaultRoot).catch(console.error), SYNC_JOB_POLL_MS);
  }
}

async function resolveOllamaConfig(vaultRoot: string) {
  const config = readNodeConfig(vaultRoot);
  if (!config) return null;
  if (config.ollama?.enabled === false) return null;

  const stored = config.ollama ?? defaultOllamaConfig();
  const probed = await probeOllama(stored.baseUrl);
  if (!probed) return null;

  return {
    enabled: true,
    baseUrl: stored.baseUrl,
    model: stored.model || probed.model,
  };
}

async function runInferenceCycle(vaultRoot: string): Promise<void> {
  const config = readNodeConfig(vaultRoot);
  if (!config) return;

  const ollama = await resolveOllamaConfig(vaultRoot);
  if (!ollama) return;

  await processPendingInference(config.apiBaseUrl, config.nodeId, config.nodeToken, ollama);
}

async function runSyncJobCycle(vaultRoot: string): Promise<void> {
  if (syncJobInFlight) return;
  const config = readNodeConfig(vaultRoot);
  if (!config) return;
  syncJobInFlight = true;
  try {
    await processPendingSyncJobs(vaultRoot);
  } finally {
    syncJobInFlight = false;
  }
}

async function runSyncCycle(vaultRoot: string): Promise<void> {
  const config = readNodeConfig(vaultRoot);
  if (!config) return;

  const snapshot = buildSnapshotFromVault(vaultRoot, config.label);
  const disk = vaultDiskStats(vaultRoot);
  const vaultKey = deriveVaultKey(config.nodeToken, config.nodeId);
  const metadataPayload = buildVaultEncryptedBlob(snapshot, (json) =>
    encryptVaultJson(json, vaultKey),
  );

  const ollama = await resolveOllamaConfig(vaultRoot);

  try {
    await sendHeartbeat(config.apiBaseUrl, config.nodeId, config.nodeToken, {
      ...defaultHeartbeat(config.label, vaultRoot),
      nodeId: config.nodeId,
      storageGb: disk.storageGb,
      storageFreeGb: disk.storageFreeGb,
      ollamaEnabled: Boolean(ollama),
      ollamaBaseUrl: ollama?.baseUrl,
      ollamaModel: ollama?.model,
    });

    await syncVaultMetadata(config.apiBaseUrl, config.nodeId, config.nodeToken, metadataPayload);

    lastSyncOkAt = new Date();
    lastSyncError = null;
    refreshTrayMenu(vaultRoot);

    try {
      maybeRebuildVectorIndex(vaultRoot);
    } catch (err) {
      console.warn('Vector index rebuild skipped:', err);
    }
  } catch (err) {
    lastSyncError = err instanceof Error ? err.message : String(err);
    console.error('Founder Node sync cycle failed:', lastSyncError);
    if (isFounderNodeAuthError(err)) {
      if (syncTimer) {
        clearInterval(syncTimer);
        syncTimer = null;
      }
      clearNodeConfig(vaultRoot);
      lastSyncError =
        'Session expired on the server — enter a new pairing code from Founder OS (Settings → Builder).';
      refreshTrayMenu(vaultRoot);
      notifyDesktop(
        'Founder Node needs pairing',
        'Your link expired. Generate a new code on the website and pair again.',
      );
      openPairWindow();
      return;
    }
    refreshTrayMenu(vaultRoot);
    throw err;
  }
}

function openPairWindow(): void {
  if (pairWindow) {
    pairWindow.focus();
    return;
  }

  const icon = loadAppIcon();
  pairWindow = new BrowserWindow({
    width: 440,
    height: 520,
    title: 'Pair Founder Node',
    autoHideMenuBar: true,
    icon: icon.isEmpty() ? undefined : icon,
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
        msg.textContent = 'Paired! This window will close — Founder Node keeps running in your system tray (icon near the clock). Do not click Quit in the tray menu.';
        msg.className = 'ok';
        setTimeout(() => window.close(), 5000);
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
  const pending = getPendingUpdate();
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: `Founder Node v${FOUNDER_NODE_APP_VERSION}`,
      enabled: false,
    },
    {
      label: config ? `Connected: ${config.label}` : 'Not paired',
      enabled: false,
    },
    {
      label: config ? formatLastSyncLine() : 'Pair to enable sync',
      enabled: false,
    },
  ];

  if (pending) {
    template.push({
      label: `Install update v${pending.version}…`,
      click: () => {
        downloadAndInstallUpdate(pending).catch(console.error);
      },
    });
  }

  template.push(
    { type: 'separator' },
    {
      label: 'Check for updates…',
      click: () => {
        checkForUpdates({ silent: false }).catch(console.error);
      },
    },
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
  );

  return Menu.buildFromTemplate(template);
}

function refreshTrayMenu(vaultRoot: string): void {
  tray?.setContextMenu(buildTrayMenu(vaultRoot));
}

if (gotSingleInstanceLock) {
  app.on('second-instance', () => {
    if (pairWindow && !pairWindow.isDestroyed()) {
      pairWindow.show();
      pairWindow.focus();
      return;
    }
    tray?.popUpContextMenu();
  });
}

app.whenReady().then(() => {
  if (app.isPackaged) {
    cleanupLegacyPortableInstallers(FOUNDER_NODE_APP_VERSION);
  }

  const backgroundKeeper = new BrowserWindow({ show: false });
  backgroundKeeper.hide();

  const vaultRoot = defaultVaultRoot();
  const nodeId = loadOrCreateNodeId(vaultRoot);
  ensureVault(vaultRoot, nodeId);

  const icon = loadAppIcon();
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon.resize({ width: 16, height: 16 }));
  tray.setToolTip(`Founder Node v${FOUNDER_NODE_APP_VERSION}`);
  bindUpdateTray(tray);
  setUpdateMenuRefresh(() => refreshTrayMenu(vaultRoot));
  refreshTrayMenu(vaultRoot);
  tray.on('click', () => tray?.popUpContextMenu());

  const config = readNodeConfig(vaultRoot);
  if (!config) {
    openPairWindow();
  } else {
    startSyncLoop(vaultRoot);
  }

  startAutoUpdateChecks();
  setInterval(() => refreshTrayMenu(vaultRoot), 60_000);

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
        ollama: defaultOllamaConfig(),
      });

      refreshTrayMenu(vaultRoot);
      startSyncLoop(vaultRoot);
      notifyDesktop(
        'Founder Node connected',
        'Vault paired. This app runs in the system tray — keep it open so Founder OS shows online.',
      );
    },
  );
});

app.on('window-all-closed', () => {
  // Tray app — keep running in background
});

app.on('before-quit', () => {
  if (syncTimer) clearInterval(syncTimer);
  if (inferenceTimer) clearInterval(inferenceTimer);
  if (syncJobTimer) clearInterval(syncJobTimer);
  tray?.destroy();
  tray = null;
  releaseGlobalInstanceLock();
});
