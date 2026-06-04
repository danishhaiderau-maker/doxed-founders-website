import {
  app,
  BrowserWindow,
  Tray,
  Menu,
  nativeImage,
  ipcMain,
  Notification,
  powerMonitor,
  shell,
  dialog,
} from 'electron';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { configureSharedElectronUserData } from './app-paths';
import {
  enforceSingleFounderNodeInstance,
  ensureOnlyOneFounderNodeProcess,
  releaseGlobalInstanceLock,
} from './single-instance';
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
  sendHeartbeat,
  syncVaultMetadata,
} from './sync-client';
import { defaultOllamaConfig, probeOllama } from './ollama-client';
import { processPendingInference } from './inference-client';
import { maybeRebuildVectorIndex, processPendingSyncJobs } from './sync-jobs-client';
import {
  FOUNDER_NODE_APP_VERSION,
  buildVaultEncryptedBlob,
  deriveVaultKey,
  encryptVaultJson,
} from '@dcf/founder-vault';
import { buildMergePatchForSync, pullPendingVaultMerges } from './vault-sync-pull';
import { cleanupLegacyPortableInstallers } from './legacy-cleanup';
import {
  bindUpdateTray,
  checkForUpdates,
  checkForUpdatesAfterSyncFailure,
  downloadAndInstallUpdate,
  getPendingUpdate,
  setUpdateMenuRefresh,
  startAutoUpdateChecks,
} from './update-manager';
import {
  authFailureUserMessage,
  classifySyncFailure,
  transientRetryDelayMs,
} from './connection-health';
import {
  isWindows,
  promptFirewallBlocked,
  resetFirewallPromptCooldown,
  tryAddWindowsFirewallRules,
  openWindowsFirewallSettings,
} from './firewall-helper';
import { createLoopHandles, stopBackgroundLoops, type BackgroundLoopHandles } from './background-loops';

const DEFAULT_API = process.env.FOUNDER_OS_API_URL ?? 'https://doxxedcrypto.digital';
const SETTINGS_BUILDER_URL = `${DEFAULT_API.replace(/\/$/, '')}/settings/builder`;
const SYNC_INTERVAL_MS = 45_000;
const INFERENCE_POLL_MS = 3_000;
const SYNC_JOB_POLL_MS = 1_500;
const STARTUP_SYNC_DELAYS_MS = [0, 5_000, 15_000, 45_000];

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
const loops: BackgroundLoopHandles = createLoopHandles();
let syncJobInFlight = false;
let syncCycleInFlight = false;
let lastSyncOkAt: Date | null = null;
let lastSyncError: string | null = null;
let consecutiveTransientFailures = 0;
let syncPausedUntil = 0;
let authRecoveryHandled = false;
let lastAuthDialogAt = 0;
const AUTH_DIALOG_COOLDOWN_MS = 5 * 60 * 1000;

function notifyDesktop(title: string, body: string): void {
  if (!Notification.isSupported()) return;
  try {
    new Notification({ title, body }).show();
  } catch {
    /* optional on some Linux setups */
  }
}

function resetAuthRecoveryState(): void {
  authRecoveryHandled = false;
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

function scheduleStartupSyncBursts(vaultRoot: string) {
  for (const delay of STARTUP_SYNC_DELAYS_MS) {
    const timer = setTimeout(() => {
      runSyncCycle(vaultRoot).catch(console.error);
    }, delay);
    loops.startupTimers.push(timer);
  }
}

function startSyncLoop(vaultRoot: string) {
  stopBackgroundLoops(loops);
  syncPausedUntil = 0;
  consecutiveTransientFailures = 0;
  scheduleStartupSyncBursts(vaultRoot);
  loops.syncTimer = setInterval(() => runSyncCycle(vaultRoot).catch(console.error), SYNC_INTERVAL_MS);
  if (!loops.inferenceTimer) {
    loops.inferenceTimer = setInterval(
      () => runInferenceCycle(vaultRoot).catch(console.error),
      INFERENCE_POLL_MS,
    );
  }
  if (!loops.syncJobTimer) {
    runSyncJobCycle(vaultRoot).catch(console.error);
    loops.syncJobTimer = setInterval(
      () => runSyncJobCycle(vaultRoot).catch(console.error),
      SYNC_JOB_POLL_MS,
    );
  }
}

function handleAuthFailure(vaultRoot: string): void {
  if (authRecoveryHandled) return;
  authRecoveryHandled = true;

  lastSyncError = authFailureUserMessage();
  stopBackgroundLoops(loops);
  clearNodeConfig(vaultRoot);
  syncCycleInFlight = false;
  syncJobInFlight = false;
  refreshTrayMenu(vaultRoot);

  const showDialog =
    app.isPackaged && Date.now() - lastAuthDialogAt >= AUTH_DIALOG_COOLDOWN_MS;

  void (async () => {
    if (showDialog) {
      lastAuthDialogAt = Date.now();
      const { response } = await dialog.showMessageBox({
        type: 'warning',
        title: 'Desktop link expired',
        message: 'Not a firewall issue — you need a new pairing code',
        detail: `${lastSyncError}\n\nYour cloud account still shows as linked. Use the pairing window (not extra browser tabs) — generate one code in Founder OS → Settings → Builder, paste it here.`,
        buttons: ['OK', 'Open pairing in browser'],
        defaultId: 0,
        cancelId: 0,
      });
      if (response === 1) {
        await shell.openExternal(SETTINGS_BUILDER_URL);
      }
    } else if (!app.isPackaged) {
      notifyDesktop('Founder Node needs pairing', lastSyncError);
    }
    openPairWindow();
  })();
}

function handleSyncCycleError(vaultRoot: string, err: unknown): void {
  const kind = classifySyncFailure(err);
  if (kind === 'auth') {
    handleAuthFailure(vaultRoot);
    return;
  }

  lastSyncError = err instanceof Error ? err.message : String(err);
  refreshTrayMenu(vaultRoot);

  if (kind === 'transient') {
    consecutiveTransientFailures += 1;
    syncPausedUntil = Date.now() + transientRetryDelayMs(consecutiveTransientFailures);
    checkForUpdatesAfterSyncFailure();
    if (consecutiveTransientFailures >= 1) {
      notifyDesktop(
        'Founder Node cannot reach Founder OS',
        isWindows()
          ? 'Open the tray menu → Allow through Windows Firewall, then Sync now.'
          : 'Check your network, then tray → Sync now.',
      );
      void promptFirewallBlocked({
        consecutiveFailures: consecutiveTransientFailures,
        lastError: lastSyncError,
        onRetrySync: () => runSyncCycle(vaultRoot).catch(console.error),
      });
    }
    return;
  }

  consecutiveTransientFailures += 1;
  syncPausedUntil = Date.now() + transientRetryDelayMs(consecutiveTransientFailures);
  checkForUpdatesAfterSyncFailure();
  if (consecutiveTransientFailures >= 2) {
    notifyDesktop(
      'Founder Node cannot reach Founder OS',
      isWindows()
        ? 'Tray → Allow through Windows Firewall, then Sync now.'
        : 'Check network, then tray → Sync now.',
    );
    void promptFirewallBlocked({
      consecutiveFailures: consecutiveTransientFailures,
      lastError: lastSyncError,
      onRetrySync: () => runSyncCycle(vaultRoot).catch(console.error),
    });
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

  try {
    await processPendingInference(config.apiBaseUrl, config.nodeId, config.nodeToken, ollama);
  } catch (err) {
    if (classifySyncFailure(err) === 'auth') {
      handleAuthFailure(vaultRoot);
      return;
    }
    console.warn('Inference poll:', err);
  }
}

async function runSyncJobCycle(vaultRoot: string): Promise<void> {
  if (syncJobInFlight) return;
  const config = readNodeConfig(vaultRoot);
  if (!config) return;
  syncJobInFlight = true;
  try {
    await processPendingSyncJobs(vaultRoot);
  } catch (err) {
    if (classifySyncFailure(err) === 'auth') {
      handleAuthFailure(vaultRoot);
      return;
    }
    console.warn('Sync job cycle:', err);
  } finally {
    syncJobInFlight = false;
  }
}

async function runSyncCycle(vaultRoot: string): Promise<void> {
  if (syncCycleInFlight || Date.now() < syncPausedUntil) return;
  const config = readNodeConfig(vaultRoot);
  if (!config) return;

  syncCycleInFlight = true;
  const snapshot = buildSnapshotFromVault(vaultRoot, config.label);
  const disk = vaultDiskStats(vaultRoot);
  const vaultKey = deriveVaultKey(config.nodeToken, config.nodeId);
  const metadataPayload = buildVaultEncryptedBlob(snapshot, (json) =>
    encryptVaultJson(json, vaultKey),
  );
  metadataPayload.mergePatch = buildMergePatchForSync(
    vaultRoot,
    config.nodeId,
    config.label,
    process.platform,
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

    const merged = await pullPendingVaultMerges(vaultRoot);
    if (merged > 0) {
      notifyDesktop('Founder Vault synced', `Applied ${merged} update(s) from your other device.`);
    }

    lastSyncOkAt = new Date();
    lastSyncError = null;
    consecutiveTransientFailures = 0;
    syncPausedUntil = 0;
    resetFirewallPromptCooldown();
    refreshTrayMenu(vaultRoot);

    try {
      maybeRebuildVectorIndex(vaultRoot);
    } catch (err) {
      console.warn('Vector index rebuild skipped:', err);
    }
  } catch (err) {
    console.error('Founder Node sync cycle failed:', err);
    handleSyncCycleError(vaultRoot, err);
  } finally {
    syncCycleInFlight = false;
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
  <p>Generate a code in <strong>Founder OS → Settings → Builder</strong>, choose <strong>Founder Vault (Founder Node)</strong>, then paste the <strong>8-character code here</strong> (not in the browser).</p>
  <p style="font-size:0.8rem;color:#fbbf24">If you already generated a new code after pairing once, the old link is dead — use the latest code only.</p>
  <p style="margin-top:12px"><button type="button" id="openWeb" style="width:100%;padding:10px;border-radius:8px;border:1px solid #3f3f46;background:#27272a;color:#e4e4e7;cursor:pointer;font-weight:600">Open Builder settings in browser</button></p>
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
    document.getElementById('openWeb').onclick = () => ipcRenderer.invoke('open-settings');
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
    ...(isWindows() && config
      ? [
          {
            label: 'Allow through Windows Firewall…',
            click: () => {
              void (async () => {
                const result = await tryAddWindowsFirewallRules();
                notifyDesktop('Firewall', result.detail);
                await runSyncCycle(vaultRoot);
              })();
            },
          },
          {
            label: 'Open Windows Firewall settings…',
            click: () => {
              void openWindowsFirewallSettings();
            },
          },
        ]
      : []),
    {
      label: 'Repair connection (new pairing code)…',
      click: () => {
        void shell.openExternal(SETTINGS_BUILDER_URL);
        openPairWindow();
      },
    },
    {
      label: 'Open Founder OS Settings…',
      click: () => {
        void shell.openExternal(SETTINGS_BUILDER_URL);
      },
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
    ensureOnlyOneFounderNodeProcess();
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

  ensureOnlyOneFounderNodeProcess();

  const config = readNodeConfig(vaultRoot);
  if (!config) {
    if (isWindows() && app.isPackaged) {
      void tryAddWindowsFirewallRules().catch(console.warn);
    }
    openPairWindow();
  } else {
    startSyncLoop(vaultRoot);
  }

  if (app.isPackaged) {
    app.setLoginItemSettings({
      openAtLogin: true,
      openAsHidden: true,
      name: 'Founder Node',
    });
  }

  startAutoUpdateChecks();
  setInterval(() => refreshTrayMenu(vaultRoot), 60_000);

  powerMonitor.on('resume', () => {
    syncPausedUntil = 0;
    runSyncCycle(vaultRoot).catch(console.error);
  });

  ipcMain.handle('open-settings', async () => {
    await shell.openExternal(SETTINGS_BUILDER_URL);
  });

  ipcMain.handle(
    'pair',
    async (
      _event: unknown,
      input: { apiBaseUrl: string; code: string; label: string },
    ) => {
      const apiBaseUrl = input.apiBaseUrl.replace(/\/$/, '');
      const label = input.label || `${os.hostname()} Founder Node`;
      const result = await pairNode(apiBaseUrl, {
        code: input.code,
        nodeId,
        label,
        platform: process.platform,
        appVersion: FOUNDER_NODE_APP_VERSION,
      });

      const draftConfig = {
        version: 1 as const,
        apiBaseUrl,
        nodeId: result.nodeId,
        nodeToken: result.nodeToken,
        label,
        pairedAt: new Date().toISOString(),
        ollama: defaultOllamaConfig(),
      };

      // Prove credentials before saving — avoids "paired" UI with immediate 401 sync.
      await sendHeartbeat(apiBaseUrl, result.nodeId, result.nodeToken, {
        ...defaultHeartbeat(label, vaultRoot),
        nodeId: result.nodeId,
      });

      writeNodeConfig(vaultRoot, draftConfig);

      resetAuthRecoveryState();
      refreshTrayMenu(vaultRoot);
      startSyncLoop(vaultRoot);
      notifyDesktop(
        'Founder Node connected',
        'Vault paired. Keep one tray app open — do not launch Founder Node again from the Start Menu.',
      );
      if (isWindows() && app.isPackaged) {
        void tryAddWindowsFirewallRules().catch(console.warn);
      }
    },
  );
});

app.on('window-all-closed', () => {
  // Tray app — keep running in background
});

app.on('before-quit', () => {
  stopBackgroundLoops(loops);
  tray?.destroy();
  tray = null;
  releaseGlobalInstanceLock();
});
