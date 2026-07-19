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
import { InferenceUsageReporter } from './inference-usage-reporter';
import { maybeRebuildVectorIndex, processPendingSyncJobs } from './sync-jobs-client';
import { processPendingDispatches } from './cursor-dispatch';
import {
  buildVaultEncryptedBlob,
  deriveVaultKey,
  encryptVaultJson,
} from '@dcf/founder-vault';
import { buildMergePatchForSync, pullPendingVaultMerges } from './vault-sync-pull';
import { cleanupLegacyPortableInstallers } from './legacy-cleanup';
import {
  discoverCursorAgents,
  discoverCursorSessions,
  discoverCursorWorkspaces,
} from './cursor-discovery';
import {
  discoverClaudeCodeAgents,
  discoverClaudeCodeSessions,
  discoverClaudeCodeWorkspaces,
} from './claude-code-discovery';
import {
  connectAllIdes,
  connectCursor,
  connectFounderIde,
  connectShellEnv,
  disconnectCursor,
  disconnectFounderIde,
  type ConnectResult,
} from './connect-ide';
import {
  CLAUDE_CODE_CAPABILITIES,
  CURSOR_CAPABILITIES,
  type BridgeCapabilityReport,
  type BridgeSession,
} from '@dcf/utils';
import { FOUNDER_NODE_LOCAL_VERSION } from './app-version';
import type { FounderNodeHeartbeatExt } from './sync-client';
import {
  bindUpdateTray,
  checkForUpdates,
  checkForUpdatesAfterSyncFailure,
  configureUpdateChecks,
  downloadAndInstallUpdate,
  getPendingUpdate,
  setUpdateMenuRefresh,
  startAutoUpdateChecks,
} from './update-manager';
import {
  bindIdeUpdateTray,
  checkForIdeUpdates,
  configureIdeUpdates,
  downloadVerifyInstallAndHandshake,
  getIdeUpdateState,
  getIdeUpdateFailureReason,
  getLastResolvedUpdate,
  ideUpdateTooltipSuffix,
  setIdeUpdateMenuRefresh,
  startIdeAutoUpdateChecks,
} from './ide-update-manager';
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
import {
  getFounderCloudMode,
  patchFounderCloudConfig,
  resolveFounderCloudRepo,
  runFounderLocalAsync,
} from './founder-cloud';
import { startDeploymentRuntimeStatusServer } from './deployment-mode-status';
// Phase 2 — device-code first-run + pairing state machine.
import {
  newInstallId,
  newIpcSecret,
  requestDeviceCode,
  pollDeviceCode,
  postLogout,
  postRevoke,
  postRotateToken,
  type DeviceCodeRendererGrant,
  type DeviceCodePollRendererResult,
  type AuthorizedPair,
} from './device-code-flow';
import {
  computePairingState,
  pairingStateLabel,
  pairingStateTooltip,
} from './pairing-state';
// Phase 3 — named-pipe IPC client (connects to the IDE extension's server).
import {
  IdeIpcClient,
  ensureIdeInstallIdentity,
  startIdeIpcClient,
} from './ide-ipc-client';
// Phase 3 — report IDE handshake state to the API so the adapter can decide
// isConnected() in real time. Defined inline (small) to avoid pulling a new
// dependency for a single fetch helper.
function reportIdeHandshake(apiBaseUrl: string, nodeId: string, nodeToken: string, active: boolean): void {
  const base = apiBaseUrl.replace(/\/$/, '');
  void fetch(`${base}/api/founder-node/ide-handshake`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `FounderNode ${nodeId}:${nodeToken}`,
    },
    body: JSON.stringify({ active }),
  }).catch((err) => console.warn('[ide-ipc] failed to report handshake state:', err));
}

const DEFAULT_API = process.env.FOUNDER_OS_API_URL ?? 'https://doxxedcrypto.digital';
const SETTINGS_BUILDER_URL = `${DEFAULT_API.replace(/\/$/, '')}/settings/builder`;
const SYNC_INTERVAL_MS = 30_000;
/** Fast loop: refresh Cursor chat messages + claim pending dispatches. */
const SESSION_MESSAGE_SYNC_MS = 3_000;
const INFERENCE_POLL_MS = 3_000;
const SYNC_JOB_POLL_MS = 1_500;
const IDE_HANDSHAKE_REPORT_MS = 15_000;
const STARTUP_SYNC_DELAYS_MS = [0, 5_000, 15_000, 45_000];

// ─── File logging bootstrap ────────────────────────────────────────────────
// Founder Node has exited silently several times (no Event Log, no crash dump,
// no console capture). Redirect console + install unhandledRejection /
// uncaughtException handlers so the next crash leaves a trail at
// ~/FounderVault/logs/founder-node.log. Best-effort: if setup fails, continue
// without logs rather than crash.
const LOG_DIR = path.join(os.homedir(), 'FounderVault', 'logs');
const LOG_FILE = path.join(LOG_DIR, 'founder-node.log');
try {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const origLog = console.log;
  const origErr = console.error;
  const origWarn = console.warn;
  const ts = (): string => new Date().toISOString();
  const writeLine = (level: string, args: unknown[]): void => {
    try {
      const line = args
        .map((a) => (a instanceof Error ? (a.stack ?? a.message) : typeof a === 'string' ? a : JSON.stringify(a)))
        .join(' ');
      fs.appendFileSync(LOG_FILE, `[${ts()}] [${level}] ${line}\n`);
    } catch {
      /* disk full / locked — ignore */
    }
  };
  console.log = (...args: unknown[]): void => {
    origLog(...args);
    writeLine('INFO', args);
  };
  console.error = (...args: unknown[]): void => {
    origErr(...args);
    writeLine('ERR', args);
  };
  console.warn = (...args: unknown[]): void => {
    origWarn(...args);
    writeLine('WARN', args);
  };
  process.on('unhandledRejection', (reason): void => {
    writeLine('UNHANDLED-REJECT', [reason instanceof Error ? (reason.stack ?? reason.message) : String(reason)]);
  });
  process.on('uncaughtException', (err: Error): void => {
    writeLine('UNCAUGHT', [err.stack ?? err.message]);
  });
  fs.appendFileSync(LOG_FILE, `\n[${ts()}] === Founder Node starting, pid=${process.pid} ===\n`);
} catch {
  /* logging setup failed — continue without logs */
}

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
let ideTooltipTimer: ReturnType<typeof setInterval> | null = null;
let ideHandshakeReportTimer: ReturnType<typeof setInterval> | null = null;
let nodeConfigWatchPath: string | null = null;
const loops: BackgroundLoopHandles = createLoopHandles();
let syncJobInFlight = false;
let syncCycleInFlight = false;
let sessionSyncInFlight = false;
/** Phase 7 — Private-mode runtime-status HTTP server handle. */
let deploymentStatusServer: { close: () => void } | null = null;
let lastCachedHeartbeat: FounderNodeHeartbeatExt | null = null;
let lastSyncOkAt: Date | null = null;
let lastSyncError: string | null = null;
let consecutiveTransientFailures = 0;
let syncPausedUntil = 0;
let authRecoveryHandled = false;
let lastAuthDialogAt = 0;
const AUTH_DIALOG_COOLDOWN_MS = 5 * 60 * 1000;

// ─── Phase 2 — device-code + pairing state runtime handles ────────────────
/**
 * In-flight device-code grant. The renderer drives the polling; we hold the
 * `deviceCode` (the secret) on this object so it never crosses the IPC
 * boundary (the renderer only sees the userCode + verificationUri).
 */
interface ActiveDeviceCode {
  deviceCode: string;
  rendererGrant: DeviceCodeRendererGrant;
}
let activeDeviceCode: ActiveDeviceCode | null = null;
/** Set to true while the pair window is open with a flow in progress. */
let pairingInProgress = false;
/**
 * Phase 3 — named-pipe IPC client (connects to the IDE extension's server).
 * Owned here so the heartbeat loop can read ideHandshakeActive from it.
 */
let ideIpcClient: IdeIpcClient | null = null;

function ensureIdeIpcClient(vaultRoot: string): boolean {
  if (ideIpcClient || !readNodeConfig(vaultRoot)) return false;

  ideIpcClient = startIdeIpcClient();
  ideIpcClient.on('connected', () => {
    const config = readNodeConfig(vaultRoot);
    if (config) {
      reportIdeHandshake(
        config.apiBaseUrl,
        config.nodeId,
        config.nodeToken,
        true,
      );
    }
  });
  ideIpcClient.on('disconnected', () => {
    const config = readNodeConfig(vaultRoot);
    if (config) {
      reportIdeHandshake(
        config.apiBaseUrl,
        config.nodeId,
        config.nodeToken,
        false,
      );
    }
  });
  return true;
}

function reportCurrentIdeHandshake(vaultRoot: string): void {
  const config = readNodeConfig(vaultRoot);
  if (!config) return;
  reportIdeHandshake(
    config.apiBaseUrl,
    config.nodeId,
    config.nodeToken,
    ideIpcClient?.isHandshakeActive() ?? false,
  );
}

function startIdeHandshakeReporting(vaultRoot: string): void {
  if (ideHandshakeReportTimer) return;
  reportCurrentIdeHandshake(vaultRoot);
  ideHandshakeReportTimer = setInterval(
    () => reportCurrentIdeHandshake(vaultRoot),
    IDE_HANDSHAKE_REPORT_MS,
  );
}

function disconnectIdeIpcClient(vaultRoot: string): void {
  const config = readNodeConfig(vaultRoot);
  if (config) {
    reportIdeHandshake(
      config.apiBaseUrl,
      config.nodeId,
      config.nodeToken,
      false,
    );
  }
  ideIpcClient?.disconnect();
  ideIpcClient = null;
}

function watchForNodeConfig(vaultRoot: string): void {
  if (nodeConfigWatchPath) return;

  nodeConfigWatchPath = path.join(vaultRoot, 'node-config.json');
  fs.watchFile(
    nodeConfigWatchPath,
    { interval: 2_000, persistent: false },
    () => {
      const config = readNodeConfig(vaultRoot);
      if (!config || !ensureIdeIpcClient(vaultRoot)) return;

      pairingInProgress = false;
      pairWindow?.close();
      refreshTrayMenu(vaultRoot);
      startSyncLoop(vaultRoot);
      configureUpdateChecks({ apiBaseUrl: config.apiBaseUrl });
      configureIdeUpdates({
        apiBaseUrl: config.apiBaseUrl,
        channel:
          (process.env.FOUNDER_STACK_CHANNEL as
            | 'stable'
            | 'beta'
            | 'insider'
            | undefined) ?? 'stable',
        allowUnsigned: process.env.FOUNDER_STACK_ALLOW_UNSIGNED === '1',
        handshakeProbe: () => ideIpcClient?.isHandshakeActive() ?? false,
      });
      notifyDesktop(
        'Founder Node connected',
        'Your Founder IDE is now available for secure remote control.',
      );
    },
  );
}

const FOUNDER_IDE_CAPABILITIES: BridgeCapabilityReport = {
  discoverWorkspaces: false,
  listRecentSessions: true,
  resumeSession: true,
  sendPrompt: true,
  streamEvents: true,
  getGitState: false,
  getTerminal: true,
  getDeployments: false,
  getAgents: true,
};

function discoverFounderIdeSessions(nodeId: string): BridgeSession[] {
  if (!ideIpcClient?.isHandshakeActive()) return [];
  return [
    {
      id: `founder-ide:${nodeId}`,
      workspaceId: `founder-ide:${nodeId}`,
      title: 'Founder IDE',
      subtitle: 'Connected through Founder Node',
      ideProvider: 'founder-ide',
      restorable: true,
      lastActiveAt: new Date().toISOString(),
    },
  ];
}

const inferenceUsageReporter = new InferenceUsageReporter();

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
  loops.sessionSyncTimer = setInterval(
    () => runSessionMessageSync(vaultRoot).catch(console.error),
    SESSION_MESSAGE_SYNC_MS,
  );
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
  disconnectIdeIpcClient(vaultRoot);
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
      const isConnError = lastSyncError
        ? /ECONNREFUSED|ETIMEDOUT|ENOTFOUND|ECONNRESET|EHOSTUNREACH|fetch failed|TypeError|network|socket/i.test(lastSyncError)
        : true;
      notifyDesktop(
        'Founder Node sync failed',
        isConnError && isWindows()
          ? 'Open the tray menu → Allow through Windows Firewall, then Sync now.'
          : `Check your network or try again. ${lastSyncError ? `(${lastSyncError.slice(0, 80)})` : ''}`,
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
    const isConnError = lastSyncError
      ? /ECONNREFUSED|ETIMEDOUT|ENOTFOUND|ECONNRESET|EHOSTUNREACH|fetch failed|TypeError|network|socket/i.test(lastSyncError)
      : true;
    notifyDesktop(
      'Founder Node sync failed',
      isConnError && isWindows()
        ? 'Tray → Allow through Windows Firewall, then Sync now.'
        : `Check network or try again. ${lastSyncError ? `(${lastSyncError.slice(0, 80)})` : ''}`,
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
    await processPendingInference(
      config.apiBaseUrl,
      config.nodeId,
      config.nodeToken,
      ollama,
      inferenceUsageReporter,
    );
    // Best-effort flush of any accumulated local-inference token usage.
    if (inferenceUsageReporter.pendingCount() > 0) {
      void inferenceUsageReporter
        .flush(config.apiBaseUrl, config.nodeId, config.nodeToken)
        .catch((err) => console.warn('Inference usage flush failed:', err));
    }
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

async function runSessionMessageSync(vaultRoot: string): Promise<void> {
  if (sessionSyncInFlight || syncCycleInFlight || Date.now() < syncPausedUntil) return;
  const config = readNodeConfig(vaultRoot);
  if (!config || !lastCachedHeartbeat) return;

  sessionSyncInFlight = true;
  try {
    const cursorSessions = discoverCursorSessions();
    const claudeSessions = discoverClaudeCodeSessions();
    const sessions = [
      ...discoverFounderIdeSessions(config.nodeId),
      ...cursorSessions,
      ...claudeSessions,
    ];

    await sendHeartbeat(config.apiBaseUrl, config.nodeId, config.nodeToken, {
      ...lastCachedHeartbeat,
      sessions,
    });
    lastCachedHeartbeat = { ...lastCachedHeartbeat, sessions };

    // Claim IDE dispatches on the fast loop so a Send from Founder OS reaches
    // Cursor within a few seconds, not only on the 30s full sync.
    await processPendingDispatches(vaultRoot, ideIpcClient);
  } catch (err) {
    console.warn('Session message sync failed:', err);
  } finally {
    sessionSyncInFlight = false;
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
    const hb = defaultHeartbeat(config.label, vaultRoot);

    // Phase A — discover real Cursor + Claude Code workspaces/agents/sessions.
    const cursorWorkspaces = discoverCursorWorkspaces();
    const cursorAgents = discoverCursorAgents();
    const cursorSessions = discoverCursorSessions();
    const claudeWorkspaces = discoverClaudeCodeWorkspaces();
    const claudeAgents = discoverClaudeCodeAgents();
    const claudeSessions = discoverClaudeCodeSessions();
    const workspaces = [...cursorWorkspaces, ...claudeWorkspaces];
    const agents = [...cursorAgents, ...claudeAgents];
    const sessions = [
      ...discoverFounderIdeSessions(config.nodeId),
      ...cursorSessions,
      ...claudeSessions,
    ];
    const activeWorkspace = workspaces[0];

    // The cloud persists `desktopBridge` and exposes it via
    // GET /ide-bridge/workspaces (one workspace per node). Enrich it with the
    // most-active Cursor workspace so the browser shows real branch + title
    // instead of only vault-derived file names.
    const enrichedDesktopBridge = {
      ...hb.desktopBridge,
      ...(activeWorkspace
        ? {
            branch: activeWorkspace.branch ?? hb.desktopBridge?.branch,
            taskLabel: activeWorkspace.title,
            agentStatus:
              agents[0]?.status === 'running' ? 'running' : hb.desktopBridge?.agentStatus,
            openFilePaths: hb.desktopBridge?.openFilePaths,
          }
        : {}),
    };

    const heartbeat: FounderNodeHeartbeatExt = {
      ...hb,
      nodeId: config.nodeId,
      storageGb: disk.storageGb,
      storageFreeGb: disk.storageFreeGb,
      ollamaEnabled: Boolean(ollama),
      ollamaBaseUrl: ollama?.baseUrl,
      ollamaModel: ollama?.model,
      founderCloud: getFounderCloudMode(config),
      desktopBridge: {
        ...enrichedDesktopBridge,
        openFilePaths: metadataPayload.mergePatch?.fileManifest
          ? Object.keys(metadataPayload.mergePatch.fileManifest).slice(0, 12)
          : enrichedDesktopBridge.openFilePaths,
      },
      capabilities: CURSOR_CAPABILITIES,
      ideCapabilities: {
        cursor: CURSOR_CAPABILITIES,
        claude_code: CLAUDE_CODE_CAPABILITIES,
        founder_ide: FOUNDER_IDE_CAPABILITIES,
      },
      desktop: {
        online: true,
        platform: process.platform,
        hostname: os.hostname(),
        uptime: process.uptime(),
      },
      workspaces,
      agents,
      sessions,
    };

    await sendHeartbeat(config.apiBaseUrl, config.nodeId, config.nodeToken, heartbeat);
    lastCachedHeartbeat = heartbeat;

    await syncVaultMetadata(config.apiBaseUrl, config.nodeId, config.nodeToken, metadataPayload);

    const merged = await pullPendingVaultMerges(vaultRoot);
    if (merged > 0) {
      notifyDesktop('Founder Vault synced', `Applied ${merged} update(s) from your other device.`);
    }

    // Session sync (every 3s) claims pending IDE dispatches — no duplicate poll here.
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

function pairWindowAssetsDir(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'pair');
  }
  return path.join(__dirname, '..');
}

function openPairWindow(): void {
  // Phase 2 — mark pairing in progress so the tray shows the right state
  // while the user is in the device-code flow. Cleared when pair succeeds
  // (handled in the IPC handler) or when the window closes without success.
  pairingInProgress = true;
  refreshTrayMenu(defaultVaultRoot());

  if (pairWindow) {
    pairWindow.focus();
    return;
  }

  const icon = loadAppIcon();
  const assetsDir = pairWindowAssetsDir();
  const pairHtml = path.join(assetsDir, 'pair.html');
  const pairPreload = app.isPackaged
    ? path.join(process.resourcesPath, 'pair', 'pair-preload.js')
    : path.join(__dirname, 'pair-preload.js');

  if (!fs.existsSync(pairHtml)) {
    console.error('Pair window assets missing:', pairHtml);
    dialog.showErrorBox(
      'Founder Node install incomplete',
      `Pairing UI files are missing from this build.\n\nExpected:\n${pairHtml}\n\nReinstall Founder Node from a fresh release build (npm run pack:win).`,
    );
    return;
  }

  pairWindow = new BrowserWindow({
    width: 480,
    height: 640,
    title: 'Sign in to Founder OS',
    autoHideMenuBar: true,
    show: false,
    icon: icon.isEmpty() ? undefined : icon,
    webPreferences: {
      preload: pairPreload,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  pairWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('Pair window failed to load:', errorCode, errorDescription, validatedURL);
    dialog.showErrorBox(
      'Pair window failed to load',
      `${errorDescription} (${errorCode})\n\nTry quitting Founder Node from the tray and reinstalling from a fresh build.`,
    );
  });

  void pairWindow.loadFile(pairHtml).then(() => {
    pairWindow?.show();
  });
  pairWindow.on('closed', () => {
    pairWindow = null;
    // If we didn't successfully pair (config still null + active grant dropped),
    // flip back out of the pairing state.
    if (activeDeviceCode) {
      activeDeviceCode = null;
    }
    pairingInProgress = false;
    refreshTrayMenu(defaultVaultRoot());
  });
}

function buildTrayMenu(vaultRoot: string) {
  const config = readNodeConfig(vaultRoot);
  const cloud = getFounderCloudMode(config);
  const cloudRepo = resolveFounderCloudRepo(config);
  const pending = getPendingUpdate();

  // Phase 2 — pairing state drives the tray label + available actions.
  const pairingState = computePairingState({
    config,
    pairingInProgress,
    lastSyncOkAt,
    lastSyncError,
    authRecoveryHandled,
    tokenExpiresAt: config?.tokenExpiresAt ? new Date(config.tokenExpiresAt) : null,
  });
  const stateLabel = pairingStateLabel(pairingState, config);

  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: `Founder Node v${FOUNDER_NODE_LOCAL_VERSION}`,
      enabled: false,
    },
    {
      label: stateLabel,
      enabled: false,
    },
    {
      label: config ? formatLastSyncLine() : 'Sign in to enable sync',
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

  template.push({ type: 'separator' });

  // Phase 2 — first-run / re-pair entry point. Label depends on the state.
  if (pairingState === 'not_paired' || pairingState === 'revoked' || pairingState === 'token_expired') {
    template.push({
      label: 'Sign in with Founder ID…',
      click: () => openPairWindow(),
    });
  } else if (pairingState === 'pairing') {
    template.push({
      label: 'Pairing in progress…',
      enabled: false,
    });
  } else {
    template.push({
      label: 'Re-pair this device…',
      click: () => openPairWindow(),
    });
  }

  // Phase 2 — token + identity lifecycle actions (only when paired).
  if (config) {
    template.push({
      label: 'Rotate node token',
      enabled: pairingState === 'connected',
      click: () => {
        void (async () => {
          try {
            const rotated = await postRotateToken(config.apiBaseUrl, config.nodeId, config.nodeToken);
            const next = { ...config, nodeToken: rotated.nodeToken, tokenRotatedAt: rotated.tokenRotatedAt };
            if (rotated.tokenExpiresAt) next.tokenExpiresAt = rotated.tokenExpiresAt;
            writeNodeConfig(vaultRoot, next);
            notifyDesktop('Token rotated', 'Node token refreshed successfully.');
            refreshTrayMenu(vaultRoot);
          } catch (err) {
            notifyDesktop('Token rotation failed', err instanceof Error ? err.message : String(err));
          }
        })();
      },
    });

    template.push({
      label: 'Sign out (logout, keep node identity)',
      click: () => {
        void (async () => {
          await postLogout(config.apiBaseUrl, config.nodeId, config.nodeToken);
          disconnectIdeIpcClient(vaultRoot);
          clearNodeConfig(vaultRoot);
          authRecoveryHandled = false;
          pairingInProgress = false;
          activeDeviceCode = null;
          stopBackgroundLoops(loops);
          refreshTrayMenu(vaultRoot);
          notifyDesktop('Signed out', 'Founder Node credentials cleared. Re-pair from the tray.');
          openPairWindow();
        })();
      },
    });

    template.push({
      label: 'Revoke this node (permanent)…',
      click: () => {
        void (async () => {
          const choice = await dialog.showMessageBox({
            type: 'warning',
            title: 'Revoke this node?',
            message: 'This permanently revokes the node identity on the server.',
            detail: 'You will need to re-pair with a new device-code flow to use this install again.',
            buttons: ['Revoke', 'Cancel'],
            defaultId: 1,
            cancelId: 1,
          });
          if (choice.response !== 0) return;
          await postRevoke(config.apiBaseUrl, config.nodeId);
          disconnectIdeIpcClient(vaultRoot);
          clearNodeConfig(vaultRoot);
          authRecoveryHandled = false;
          pairingInProgress = false;
          activeDeviceCode = null;
          stopBackgroundLoops(loops);
          refreshTrayMenu(vaultRoot);
          notifyDesktop('Node revoked', 'Server-side identity removed. Re-pair to continue.');
          openPairWindow();
        })();
      },
    });
  }

  // Phase 5 — IDE updater retry/install entry points (merged from Workstream E).
  const idePending = getLastResolvedUpdate();
  const ideState = getIdeUpdateState();
  if (ideState === 'failed') {
    template.push({
      label: getIdeUpdateFailureReason()
        ? `Founder Stack update failed — retry…`
        : `Founder Stack update failed — retry…`,
      click: () => {
        checkForIdeUpdates({ silent: false }).catch(console.error);
      },
    });
  } else if (idePending) {
    template.push({
      label: `Install Founder Stack v${idePending.version}…`,
      click: () => {
        downloadVerifyInstallAndHandshake(idePending).catch(console.error);
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
      label: 'Check for Founder Stack updates…',
      click: () => {
        checkForIdeUpdates({ silent: false }).catch(console.error);
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
      label: 'Open Founder OS Settings…',
      click: () => {
        void shell.openExternal(SETTINGS_BUILDER_URL);
      },
    },
    {
      label: 'Sync now',
      enabled: Boolean(config) && pairingState !== 'token_expired' && pairingState !== 'revoked',
      click: () => {
        runSyncCycle(vaultRoot).catch(console.error);
      },
    },
    { type: 'separator' },
    {
      label: cloud.enabled ? 'Founder Cloud mode: ON' : 'Enable Founder Cloud mode',
      enabled: Boolean(config),
      click: () => {
        if (!config) return;
        const next = patchFounderCloudConfig(config, {
          enabled: !cloud.enabled,
          repoPath: cloudRepo ?? undefined,
        });
        writeNodeConfig(vaultRoot, next);
        refreshTrayMenu(vaultRoot);
        notifyDesktop(
          'Founder Cloud',
          next.founderCloud?.enabled ? 'Local stack mode enabled' : 'Founder Cloud mode disabled',
        );
      },
    },
    {
      label: cloud.stackRunning ? 'Stop local stack' : 'Start local stack',
      enabled: Boolean(config) && cloud.enabled && Boolean(cloudRepo),
      click: () => {
        if (!config || !cloudRepo) {
          notifyDesktop('Founder Cloud', 'Set FOUNDER_CLOUD_REPO to your Founder OS checkout');
          return;
        }
        void (async () => {
          const action = cloud.stackRunning ? 'stop' : 'start';
          const result = await runFounderLocalAsync(cloudRepo, action);
          const next = patchFounderCloudConfig(config, {
            enabled: true,
            repoPath: cloudRepo,
            stackRunning: action === 'start' ? result.ok : false,
            lastStartedAt: action === 'start' && result.ok ? new Date().toISOString() : config.founderCloud?.lastStartedAt,
            lastError: result.ok ? undefined : result.detail,
          });
          writeNodeConfig(vaultRoot, next);
          refreshTrayMenu(vaultRoot);
          notifyDesktop('Founder Cloud', result.detail);
          if (result.ok && action === 'start') {
            void shell.openExternal(next.founderCloud?.webUrl ?? 'http://127.0.0.1:3000/founder-den');
          }
        })();
      },
    },
    {
      label: 'Open local Mission Control',
      enabled: Boolean(cloud.stackRunning && cloud.webUrl),
      click: () => {
        void shell.openExternal(`${cloud.webUrl?.replace(/\/$/, '')}/founder-den`);
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
  // Update tooltip with the current pairing state for at-a-glance status.
  const config = readNodeConfig(vaultRoot);
  const state = computePairingState({
    config,
    pairingInProgress,
    lastSyncOkAt,
    lastSyncError,
    authRecoveryHandled,
    tokenExpiresAt: config?.tokenExpiresAt ? new Date(config.tokenExpiresAt) : null,
  });
  tray?.setToolTip(pairingStateTooltip(state, lastSyncError));
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
    cleanupLegacyPortableInstallers(FOUNDER_NODE_LOCAL_VERSION);
  }

  const backgroundKeeper = new BrowserWindow({ show: false });
  backgroundKeeper.hide();

  const vaultRoot = defaultVaultRoot();
  const nodeId = loadOrCreateNodeId(vaultRoot);
  ensureVault(vaultRoot, nodeId);

  const icon = loadAppIcon();
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon.resize({ width: 16, height: 16 }));
  tray.setToolTip(`Founder Node v${FOUNDER_NODE_LOCAL_VERSION}`);
  bindUpdateTray(tray);
  setUpdateMenuRefresh(() => refreshTrayMenu(vaultRoot));
  bindIdeUpdateTray(tray);
  setIdeUpdateMenuRefresh(() => refreshTrayMenu(vaultRoot));
  refreshTrayMenu(vaultRoot);
  tray.on('click', () => tray?.popUpContextMenu());

  ensureOnlyOneFounderNodeProcess();

  const config = readNodeConfig(vaultRoot);
  watchForNodeConfig(vaultRoot);
  startIdeHandshakeReporting(vaultRoot);
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

  // Phase 3 — start the named-pipe IPC client so the IDE extension can
  // bridge workspace state to the API via Founder Node. The client retries
  // in the background; if no IDE is running yet, it'll connect when one
  // starts. Safe to call when not paired (resolves false immediately).
  if (config) {
    // Migrate vaults paired before authenticated IDE IPC introduced the
    // install.json sidecar. This also lets the IDE and Node agree on a pipe
    // identity regardless of which application starts first.
    ensureIdeInstallIdentity(vaultRoot);
    ensureIdeIpcClient(vaultRoot);
  }

  configureUpdateChecks({ apiBaseUrl: config?.apiBaseUrl ?? DEFAULT_API });
  startAutoUpdateChecks();
  // Phase 5 (Workstream E) — IDE / Founder Stack updater. Mirrors the Node
  // updater but adds SHA-256 + Authenticode + health-handshake + rollback.
  // Channel defaults to "stable"; beta/insider can opt into allowUnsigned.
  configureIdeUpdates({
    apiBaseUrl: config?.apiBaseUrl ?? DEFAULT_API,
    channel: (process.env.FOUNDER_STACK_CHANNEL as 'stable' | 'beta' | 'insider' | undefined) ?? 'stable',
    allowUnsigned: process.env.FOUNDER_STACK_ALLOW_UNSIGNED === '1',
    // An IDE update is healthy only after Founder Node completes the
    // authenticated named-pipe handshake with the installed extension.
    handshakeProbe: () => ideIpcClient?.isHandshakeActive() ?? false,
  });
  startIdeAutoUpdateChecks();
  // Refresh the tray tooltip periodically so IDE-update state transitions
  // (downloading → verifying → installing → idle) surface alongside the Node
  // version. The menu itself refreshes via setIdeUpdateMenuRefresh above.
  ideTooltipTimer = setInterval(() => {
    const suffix = ideUpdateTooltipSuffix();
    tray?.setToolTip(
      suffix
        ? `Founder Node v${FOUNDER_NODE_LOCAL_VERSION} — ${suffix}`
        : `Founder Node v${FOUNDER_NODE_LOCAL_VERSION}`,
    );
  }, 5_000);
  setInterval(() => refreshTrayMenu(vaultRoot), 60_000);

  // Phase 7 — start the Private-mode runtime-status endpoint on 127.0.0.1.
  // Best-effort: if the port is busy (dev server already on :7002) it logs a
  // warning and continues; the cloud API degrades to "unknown" for the panel.
  if (!deploymentStatusServer) {
    deploymentStatusServer = startDeploymentRuntimeStatusServer(vaultRoot);
  }

  powerMonitor.on('resume', () => {
    syncPausedUntil = 0;
    runSyncCycle(vaultRoot).catch(console.error);
  });

  ipcMain.handle('open-settings', async () => {
    await shell.openExternal(SETTINGS_BUILDER_URL);
  });

  ipcMain.handle('get-pair-defaults', async () => ({
    apiBaseUrl: DEFAULT_API,
    label: `${os.hostname()} Founder Node`,
  }));

  // ─── Founder OS AI Proxy — IDE connect/disconnect ────────────────────────
  // One click in the UI writes Founder OS proxy credentials into the IDE
  // config (Founder IDE + Cursor settings.json, or shell env for OpenAI-compat).
  ipcMain.handle(
    'connect-founder-ide',
    async (_event: unknown): Promise<ConnectResult> => {
      const config = readNodeConfig(vaultRoot);
      if (!config) {
        return { ok: false, error: 'Founder Node is not paired yet.' };
      }
      return connectFounderIde(config);
    },
  );

  ipcMain.handle(
    'connect-cursor',
    async (_event: unknown): Promise<ConnectResult> => {
      const config = readNodeConfig(vaultRoot);
      if (!config) {
        return { ok: false, error: 'Founder Node is not paired yet.' };
      }
      return connectCursor(config);
    },
  );

  ipcMain.handle(
    'connect-ides',
    async (_event: unknown): Promise<ConnectResult> => {
      const config = readNodeConfig(vaultRoot);
      if (!config) {
        return { ok: false, error: 'Founder Node is not paired yet.' };
      }
      return connectAllIdes(config);
    },
  );

  ipcMain.handle(
    'connect-shell-env',
    async (_event: unknown): Promise<ConnectResult> => {
      const config = readNodeConfig(vaultRoot);
      if (!config) {
        return { ok: false, error: 'Founder Node is not paired yet.' };
      }
      return connectShellEnv(config);
    },
  );

  ipcMain.handle(
    'disconnect-cursor',
    async (_event: unknown): Promise<ConnectResult> => {
      const config = readNodeConfig(vaultRoot);
      if (!config) {
        return { ok: false, error: 'Founder Node is not paired yet.' };
      }
      return disconnectCursor(config);
    },
  );

  ipcMain.handle(
    'disconnect-founder-ide',
    async (_event: unknown): Promise<ConnectResult> => {
      const config = readNodeConfig(vaultRoot);
      if (!config) {
        return { ok: false, error: 'Founder Node is not paired yet.' };
      }
      return disconnectFounderIde(config);
    },
  );

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
        appVersion: FOUNDER_NODE_LOCAL_VERSION,
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

      // Auto-wire Founder IDE (+ Cursor) settings so the extension picks up
      // apiBaseUrl / nodeId / nodeToken without a second "Connect IDE" click.
      try {
        const ideResult = connectAllIdes(draftConfig);
        if (ideResult.ok) {
          console.log(`[pair] IDE credentials written → ${ideResult.target}`);
        } else {
          console.warn(`[pair] IDE connect skipped: ${ideResult.error}`);
        }
      } catch (err) {
        console.warn('[pair] IDE connect failed:', err);
      }

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

      // Phase 3 — start the IPC client on legacy pair too.
      ensureIdeIpcClient(vaultRoot);
    },
  );

  // ─── Phase 2 — device-code first-run flow ──────────────────────────────
  // Renderer (pair.js) drives the loop via start-device-code + poll-device-code.
  // The main process holds the `deviceCode` (the secret) and never crosses
  // the IPC boundary with it — only the userCode + verificationUri are sent
  // to the renderer for display.

  /**
   * Ensure the install has an installId + ipcSecret stored in a sidecar
   * `install.json` next to node-config.json. These are needed by both the
   * device-code request (server pairs a node with this installId) and the
   * named-pipe IPC server (pipe name is `founder-ide-{installId}`). On
   * successful authorize, they're merged into node-config.json so the
   * existing read/write helpers pick them up.
   */
  function ensureInstallIdentity(vaultRoot: string): { installId: string; ipcSecret: string } {
    const installFile = path.join(vaultRoot, 'install.json');
    let install: { installId?: string; ipcSecret?: string } = {};
    try {
      if (fs.existsSync(installFile)) {
        install = JSON.parse(fs.readFileSync(installFile, 'utf8'));
      }
    } catch {
      install = {};
    }
    if (!install.installId || !install.ipcSecret) {
      install.installId = install.installId ?? newInstallId();
      install.ipcSecret = install.ipcSecret ?? newIpcSecret();
      try {
        fs.writeFileSync(installFile, JSON.stringify(install, null, 2), 'utf8');
      } catch (err) {
        console.warn('Failed to persist install identity:', err);
      }
    }
    return { installId: install.installId, ipcSecret: install.ipcSecret };
  }

  ipcMain.handle('start-device-code', async (): Promise<DeviceCodeRendererGrant> => {
    const install = ensureInstallIdentity(vaultRoot);
    const apiBaseUrl = (readNodeConfig(vaultRoot)?.apiBaseUrl ?? DEFAULT_API).replace(/\/$/, '');
    const { grant } = await requestDeviceCode(apiBaseUrl, install.installId);
    activeDeviceCode = {
      deviceCode: grant.deviceCode,
      rendererGrant: {
        userCode: grant.userCode,
        verificationUri: grant.verificationUri,
        verificationUriComplete:
          grant.verificationUriComplete ??
          `${grant.verificationUri}?user_code=${encodeURIComponent(grant.userCode)}`,
        expiresAt: grant.expiresAt,
        interval: grant.interval,
        installId: install.installId,
      },
    };
    pairingInProgress = true;
    refreshTrayMenu(vaultRoot);
    return activeDeviceCode.rendererGrant;
  });

  ipcMain.handle(
    'poll-device-code',
    async (): Promise<DeviceCodePollRendererResult> => {
      // Use the deviceCode we stashed on start — never trust the renderer.
      if (!activeDeviceCode) {
        return { status: 'expired', error: 'No active device-code grant — restart the flow.' };
      }
      const apiBaseUrl = (readNodeConfig(vaultRoot)?.apiBaseUrl ?? DEFAULT_API).replace(/\/$/, '');
      const polled = await pollDeviceCode(apiBaseUrl, activeDeviceCode.deviceCode);

      if (polled.kind === 'authorized') {
        // Success — write node-config.json with the issued credentials plus
        // the installId/ipcSecret used at grant time so the named-pipe IPC
        // can be opened later.
        const install = ensureInstallIdentity(vaultRoot);
        const pair: AuthorizedPair = polled.pair;
        const label = `${os.hostname()} Founder Node`;
        const newConfig = {
          version: 1 as const,
          apiBaseUrl,
          nodeId: pair.nodeId,
          nodeToken: pair.nodeToken,
          label,
          pairedAt: new Date().toISOString(),
          ollama: defaultOllamaConfig(),
          founderId: pair.founderId,
          installId: pair.installId ?? install.installId,
          ipcSecret: install.ipcSecret,
          ...(pair.tokenExpiresAt ? { tokenExpiresAt: pair.tokenExpiresAt } : {}),
          tokenRotatedAt: new Date().toISOString(),
        };

        // Prove credentials before saving — avoids a "paired" UI that 401s
        // on the next sync. If the heartbeat fails, we still save so the
        // user can debug; the next sync cycle will surface the error.
        try {
          await sendHeartbeat(apiBaseUrl, pair.nodeId, pair.nodeToken, {
            ...defaultHeartbeat(label, vaultRoot),
            nodeId: pair.nodeId,
          });
        } catch (err) {
          console.warn('Post-pair heartbeat failed (saving anyway):', err);
        }

        writeNodeConfig(vaultRoot, newConfig);
        activeDeviceCode = null;
        pairingInProgress = false;
        authRecoveryHandled = false;
        resetAuthRecoveryState();
        refreshTrayMenu(vaultRoot);
        startSyncLoop(vaultRoot);

        // Phase 3 — start the IPC client now that we have credentials +
        // install identity. If the IDE isn't running yet, the client will
        // retry in the background until it is.
        ensureIdeIpcClient(vaultRoot);

        notifyDesktop(
          'Founder Node connected',
          `Signed in as ${pair.founderId}. Keep one tray app open.`,
        );
        if (isWindows() && app.isPackaged) {
          void tryAddWindowsFirewallRules().catch(console.warn);
        }

        // Close the pair window after a short delay so the user sees the
        // "authorized" status in the renderer.
        setTimeout(() => {
          if (pairWindow && !pairWindow.isDestroyed()) pairWindow.close();
        }, 1500);
        return polled.result;
      }

      return polled.result;
    },
  );

  ipcMain.handle('open-url', async (_event: unknown, url: string) => {
    if (typeof url === 'string' && /^https?:\/\//i.test(url)) {
      await shell.openExternal(url);
    }
  });
});

app.on('window-all-closed', () => {
  // Tray app — keep running in background
});

app.on('before-quit', () => {
  stopBackgroundLoops(loops);
  if (ideHandshakeReportTimer) {
    clearInterval(ideHandshakeReportTimer);
    ideHandshakeReportTimer = null;
  }
  if (nodeConfigWatchPath) {
    fs.unwatchFile(nodeConfigWatchPath);
    nodeConfigWatchPath = null;
  }
  disconnectIdeIpcClient(defaultVaultRoot());
  if (ideTooltipTimer) {
    clearInterval(ideTooltipTimer);
    ideTooltipTimer = null;
  }
  deploymentStatusServer?.close();
  deploymentStatusServer = null;
  tray?.destroy();
  tray = null;
  releaseGlobalInstanceLock();
});
