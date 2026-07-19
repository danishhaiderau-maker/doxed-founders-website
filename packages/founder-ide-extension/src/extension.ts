/**
 * Activation entry point for the Founder OS chat extension.
 *
 * On activation:
 *   1. Resolve credentials (settings → `~/FounderVault/node-config.json`).
 *   2. Register the native chat participant and its agentic tools.
 *   3. Wire the execution-profile selector (status bar + QuickPick).
 *   4. Wire the DDollar cost tracker (status bar + breakdown).
 *   5. If creds missing, show a "not paired" notification + status bar.
 *   6. Always wire commands and a status bar item so the user can re-pair
 *      without reloading.
 */
import * as vscode from 'vscode';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  type FounderOsCredentials,
  nodeConfigPath,
  resolveCredentials,
  syncVaultIntoSettings,
  vaultFileExists,
} from './credentials';
import { FOUNDER_OS_MODELS } from './models';
import { editFileTool } from './tools/edit-file';
import { runCommandTool } from './tools/run-command';
import { readWorkspaceTool } from './tools/read-workspace';
import { ProfileManager } from './profile-manager';
import { CostTracker } from './cost-tracker';
import { GatewayMetadataUi } from './gateway-metadata-ui';
import { registerFounderOsChatParticipant } from './chat-participant';
import { createDebugSquasherStatus } from './debug-squasher-status';
// Phase 2 — device-code sign-in + pairing-state status bar + IPC server.
import {
  ensureInstallIdentity,
  runDeviceCodeSignIn,
} from './device-code-sign-in';
import { PairingStatusBar } from './pairing-status-bar';
import { startIpcServer, stopIpcServer } from './ipc/server';
import { FOUNDER_TOOL_IDS } from './tool-names';

let connectionStatusBar: vscode.StatusBarItem | undefined;
let registeredParticipant: vscode.Disposable | undefined;
let profileManager: ProfileManager | undefined;
let costTracker: CostTracker | undefined;
let gatewayMetadataUi: GatewayMetadataUi | undefined;
let debugSquasherDisposable: vscode.Disposable | undefined;
let currentCreds: FounderOsCredentials | null = null;
/** Phase 2 — pairing-state status bar (refreshes every 15s + on config change). */
let pairingStatusBar: PairingStatusBar | undefined;

export function activate(context: vscode.ExtensionContext): void {
  // Status bar (connection state) ----------------------------------------------------
  connectionStatusBar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  connectionStatusBar.command = 'founderOs.manage';
  context.subscriptions.push(connectionStatusBar);

  // Phase 2 — pairing-state status bar (6 canonical states, heartbeat-driven).
  pairingStatusBar = new PairingStatusBar();
  context.subscriptions.push(pairingStatusBar);
  pairingStatusBar.start();

  // Execution-profile selector + DDollar cost tracker (independent of creds) ---------
  profileManager = new ProfileManager(context);
  costTracker = new CostTracker();
  gatewayMetadataUi = new GatewayMetadataUi();
  context.subscriptions.push(profileManager, costTracker, gatewayMetadataUi);
  profileManager.show();
  costTracker.show();
  gatewayMetadataUi.show();

  // Agentic tools (registered once; available to any chat participant / model).
  // `vscode.lm.registerTool` only exists on VS Code 1.96+ (proposed `lmTools`
  // API, later stable). Guard so activation does not crash on 1.93.1, where the
  // chat participant (stable API) is the primary surface. Even where the
  // function exists, registration fails if the `languageModelTools` contribution
  // point isn't processed (proposed-gated on 1.93.1), so we swallow that case.
  if (typeof vscode.lm.registerTool === 'function') {
    const tools: ReadonlyArray<readonly [string, vscode.LanguageModelTool<unknown>]> = [
      [FOUNDER_TOOL_IDS.editFile, editFileTool as vscode.LanguageModelTool<unknown>],
      [FOUNDER_TOOL_IDS.runCommand, runCommandTool as vscode.LanguageModelTool<unknown>],
      [
        FOUNDER_TOOL_IDS.readWorkspace,
        readWorkspaceTool as vscode.LanguageModelTool<unknown>,
      ],
    ];
    for (const [name, tool] of tools) {
      try {
        context.subscriptions.push(vscode.lm.registerTool(name, tool));
      } catch {
        // Tool contribution point not processed on this VS Code build — skip.
      }
    }
  }

  // Commands -------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand('founderOs.manage', () => manageConnection(context)),
    vscode.commands.registerCommand('founderOs.pair', () => pairWithFounderNode(context)),
    vscode.commands.registerCommand('founderOs.signIn', () => signInWithFounderId(context)),
    vscode.commands.registerCommand('founderOs.connectFounderOs', () =>
      signInWithFounderId(context),
    ),
    vscode.commands.registerCommand('founderOs.openVaultConfig', openVaultConfig),
    vscode.commands.registerCommand('founderOs.selectModel', selectModelAlias),
    vscode.commands.registerCommand('founderOs.selectProfile', () =>
      profileManager?.selectProfile(),
    ),
    vscode.commands.registerCommand('founderOs.showCostBreakdown', () =>
      costTracker?.showBreakdown(),
    ),
    vscode.commands.registerCommand('founderOs.resetCost', () => {
      costTracker?.reset();
      void vscode.window.showInformationMessage('Founder OS DDollar session counter reset.');
    }),
    vscode.commands.registerCommand('founderOs.showGatewayMetadata', () =>
      gatewayMetadataUi?.revealChannel(),
    ),
    vscode.commands.registerCommand('founderOs.recentGatewayMetadata', () =>
      gatewayMetadataUi?.showRecent(),
    ),
  );

  // First-pass registration (synchronous so the model picker populates fast).
  registerOrNotify(context);

  // Phase 3 — start the named-pipe IPC server so the Founder Node tray can
  // connect to this IDE instance. The server only starts if we have a config
  // with an installId (otherwise it's a no-op until the user signs in).
  // Best-effort: failure to start (e.g. another IDE instance already bound)
  // logs a warning but does not crash activation.
  if (vaultFileExists()) {
    // Legacy paired vaults predate install.json. Mint the local pipe identity
    // before starting the server so either the IDE or Founder Node may launch
    // first and both converge on the same authenticated endpoint.
    ensureInstallIdentity();
  }
  startIpcServer().catch((err) => {
    console.warn('Founder OS IPC server failed to start:', err);
  });

  // Auto-load ~/FounderVault/node-config.json into founderOs.* settings so
  // pairing isn't manual every session. Fire-and-forget; re-registers after.
  void syncVaultIntoSettings().then((synced) => {
    if (synced) {
      void startIpcServer();
      registerOrNotify(context);
    }
  });

  // Re-resolve when relevant settings change.
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('founderOs')) {
        registerOrNotify(context);
      }
    }),
  );

  // Re-resolve when the vault file appears / changes (pairing completed while
  // the editor is open). We watch the FounderVault directory if it exists.
  watchVaultFile(context, () => {
    void syncVaultIntoSettings().then((synced) => {
      if (synced) {
        void startIpcServer();
        registerOrNotify(context);
      }
    });
  });
}

export function deactivate(): void {
  registeredParticipant?.dispose();
  connectionStatusBar?.dispose();
  profileManager?.dispose();
  costTracker?.dispose();
  gatewayMetadataUi?.dispose();
  debugSquasherDisposable?.dispose();
  pairingStatusBar?.dispose();
  // Phase 3 — stop the named-pipe IPC server so we release the pipe name.
  stopIpcServer();
}

/** Register the chat participant if we have creds; otherwise show "not paired". */
function registerOrNotify(context: vscode.ExtensionContext): void {
  const creds = resolveCredentials();
  if (!creds) {
    registeredParticipant?.dispose();
    registeredParticipant = undefined;
    debugSquasherDisposable?.dispose();
    debugSquasherDisposable = undefined;
    currentCreds = null;
    setStatusNotPaired();
    void showPairPrompt(context);
    return;
  }

  if (
    currentCreds &&
    currentCreds.apiBaseUrl === creds.apiBaseUrl &&
    currentCreds.nodeId === creds.nodeId &&
    currentCreds.nodeToken === creds.nodeToken
  ) {
    // Already registered with identical creds — just refresh the label.
    setStatusConnected(currentCreds);
    return;
  }

  // Credentials changed — re-register.
  registeredParticipant?.dispose();
  debugSquasherDisposable?.dispose();

  registeredParticipant = registerFounderOsChatParticipant(context, {
    creds,
    profileManager: profileManager!,
    costTracker: costTracker!,
    onRequestStart: (modelId) => {
      connectionStatusBar!.text = `$(sync~spin) Founder OS: ${modelId}`;
      connectionStatusBar!.tooltip = 'Streaming response from Founder OS gateway…';
    },
    onMetadata: (meta) => {
      const tier = meta.tier ?? '?';
      const cost = typeof meta.ddollarCost === 'number' ? `${meta.ddollarCost} D$` : '';
      const provider2 = meta.provider ?? '';
      const model = meta.model ?? '';
      connectionStatusBar!.text = `$(sparkle) Founder OS: ${tier}${cost ? ` · ${cost}` : ''}`;
      connectionStatusBar!.tooltip = `Last route — tier: ${tier}, provider: ${provider2}, model: ${model}, cost: ${cost || 'n/a'}`;
      costTracker?.record(meta);
      gatewayMetadataUi?.record(meta);
    },
    onRequestEnd: (_modelId, ok, errorMessage) => {
      if (ok) {
        connectionStatusBar!.text = '$(check) Founder OS: Connected';
        connectionStatusBar!.tooltip = `Founder OS gateway connected. Creds source: ${creds.source}.`;
      } else if (errorMessage) {
        connectionStatusBar!.text = '$(error) Founder OS: Error';
        connectionStatusBar!.tooltip = `Last request failed: ${errorMessage}`;
      }
    },
  });

  // Debug Squasher status bar — polls /api/debug-squasher/latest every 2 min.
  debugSquasherDisposable = createDebugSquasherStatus(context, () =>
    resolveCredentials(),
  );
  context.subscriptions.push(debugSquasherDisposable);

  currentCreds = creds;
  setStatusConnected(creds);
}

function setStatusConnected(creds: FounderOsCredentials): void {
  if (!connectionStatusBar) return;
  connectionStatusBar.text = '$(check) Founder OS: Connected';
  connectionStatusBar.tooltip = `Connected to ${creds.apiBaseUrl} (creds: ${creds.source}). Click to manage.`;
  connectionStatusBar.show();
}

function setStatusNotPaired(): void {
  if (!connectionStatusBar) return;
  connectionStatusBar.text = '$(warning) Founder OS: Not Paired';
  connectionStatusBar.tooltip =
    'Founder Node credentials not found. Pair Founder Node, or set founderOs.apiBaseUrl / nodeId / nodeToken.';
  connectionStatusBar.show();
}

let pairPromptShownThisSession = false;
async function showPairPrompt(context: vscode.ExtensionContext): Promise<void> {
  if (pairPromptShownThisSession) return;
  pairPromptShownThisSession = true;

  // Phase 2 — first-run sign-in prompt. Offer the device-code flow first,
  // then a BYO-provider fallback (opt-in only, not recommended because it
  // bypasses the authed IPC layer).
  const byoEnabled = vscode.workspace
    .getConfiguration('founderOs')
    .get<boolean>('byoFallbackEnabled', false);

  const choices: string[] = ['Sign in with Founder ID', 'Dismiss'];
  if (!byoEnabled) choices.splice(1, 0, 'Enable BYO-provider fallback (not recommended)');

  const choice = await vscode.window.showWarningMessage(
    'Founder OS not configured. Sign in with Founder ID to enable the authed IPC + chat provider.',
    ...choices,
  );
  if (choice === 'Sign in with Founder ID') {
    await signInWithFounderId(context);
  } else if (choice === 'Enable BYO-provider fallback (not recommended)') {
    await vscode.workspace
      .getConfiguration('founderOs')
      .update('byoFallbackEnabled', true, vscode.ConfigurationTarget.Global);
    void vscode.window.showInformationMessage(
      'BYO-provider fallback enabled. You can paste your own API credentials in Founder OS settings.',
    );
    // Re-resolve now that the user opted in — they'll need to fill creds.
    registerOrNotify(context);
  }
}

/**
 * Phase 2 — device-code sign-in. Drives the RFC 8628 flow interactively,
 * writes the resulting credentials to ~/FounderVault/node-config.json, then
 * refreshes the chat provider + status bar.
 */
async function signInWithFounderId(context: vscode.ExtensionContext): Promise<void> {
  if (!pairingStatusBar) return;
  pairingStatusBar.setPairingInProgress(true);
  try {
    const creds = await runDeviceCodeSignIn();
    if (!creds) return;
    // Reload from the vault file so the chat provider picks up the new creds.
    await syncVaultIntoSettings();
    await startIpcServer();
    registerOrNotify(context);
    pairingStatusBar.setGatewayResult('ok');
    void vscode.window.showInformationMessage(
      `Founder OS: signed in as ${creds.founderId} (node ${creds.nodeId}).`,
    );
  } finally {
    pairingStatusBar.setPairingInProgress(false);
  }
}

async function manageConnection(context: vscode.ExtensionContext): Promise<void> {
  const items: (vscode.QuickPickItem & { action: () => unknown })[] = [
    {
      label: 'Sign in with X to Founder OS…',
      description: 'connect this IDE and Founder Node to your account',
      action: () => signInWithFounderId(context),
    },
    {
      label: 'Load Founder Node from vault…',
      description: vaultFileExists() ? 'vault file present — no pairing code needed' : 'vault file missing',
      action: () => pairWithFounderNode(context),
    },
    {
      label: 'Open settings (founderOs.*)',
      description: 'override apiBaseUrl / nodeId / nodeToken',
      action: () =>
        vscode.commands.executeCommand('workbench.action.openSettings', 'founderOs'),
    },
    {
      label: 'Open node-config.json',
      description: nodeConfigPath(),
      action: () => openVaultConfig(),
    },
    {
      label: 'Select model alias',
      description: 'change the default Founder OS model',
      action: () => selectModelAlias(),
    },
    {
      label: 'Select execution profile…',
      description: 'Turbo / Balanced / Architect / Autonomous',
      action: () => profileManager?.selectProfile(),
    },
    {
      label: 'Reset DDollar session counter',
      description: 'clear the session spend display',
      action: () => {
        costTracker?.reset();
      },
    },
  ];
  const picked = await vscode.window.showQuickPick(items, {
    placeHolder: 'Founder OS — manage connection',
  });
  if (picked) await picked.action();
}

async function pairWithFounderNode(context: vscode.ExtensionContext): Promise<void> {
  const file = nodeConfigPath();

  // Prefer vault auto-load — Founder Node writes this on pair / Connect IDE.
  if (vaultFileExists()) {
    const synced = await syncVaultIntoSettings();
    if (synced) {
      registerOrNotify(context);
      const open = await vscode.window.showInformationMessage(
        `Loaded Founder Node credentials from:\n${file}\n\napiBaseUrl=${synced.apiBaseUrl}\nnodeId=${synced.nodeId}`,
        'Reload window',
        'Open file',
        'OK',
      );
      if (open === 'Open file') await openVaultConfig();
      else if (open === 'Reload window')
        void vscode.commands.executeCommand('workbench.action.reloadWindow');
      return;
    }
  }

  // Optional: paste a Twitter-auth session JWT + API base for cloud-only pairing
  // when Founder Node isn't installed yet (thin slice — nodeId/token still preferred).
  const choice = await vscode.window.showInformationMessage(
    'Founder OS chat needs a paired Founder Node.\n\nOpen Founder Node on this machine and click "Connect IDE / Pair". This writes ~/FounderVault/node-config.json, which the extension loads automatically.',
    'Open Founder OS settings',
    'Paste API + node credentials',
    'Open docs',
    'Cancel',
  );
  if (choice === 'Open Founder OS settings') {
    void vscode.commands.executeCommand(
      'workbench.action.openSettings',
      'founderOs',
    );
  } else if (choice === 'Paste API + node credentials') {
    const apiBaseUrl = await vscode.window.showInputBox({
      prompt: 'Founder OS API base URL',
      value: 'https://doxxedcrypto.digital',
      placeHolder: 'https://doxxedcrypto.digital',
    });
    if (!apiBaseUrl) return;
    const nodeId = await vscode.window.showInputBox({
      prompt: 'Founder Node ID (from Settings → Founder Stack after pairing)',
      placeHolder: 'fn_…',
    });
    if (!nodeId) return;
    const nodeToken = await vscode.window.showInputBox({
      prompt: 'Founder Node token (shown once at pair time, or from node-config.json)',
      password: true,
    });
    if (!nodeToken) return;
    const cfg = vscode.workspace.getConfiguration('founderOs');
    await cfg.update('apiBaseUrl', apiBaseUrl.replace(/\/$/, ''), vscode.ConfigurationTarget.Global);
    await cfg.update('nodeId', nodeId.trim(), vscode.ConfigurationTarget.Global);
    await cfg.update('nodeToken', nodeToken.trim(), vscode.ConfigurationTarget.Global);
    registerOrNotify(context);
    void vscode.window.showInformationMessage('Founder OS credentials saved to User settings.');
  } else if (choice === 'Open docs') {
    void vscode.env.openExternal(
      vscode.Uri.parse('https://doxxedcrypto.digital/downloads#founder-node'),
    );
  }
}

async function openVaultConfig(): Promise<void> {
  const file = nodeConfigPath();
  if (!fs.existsSync(file)) {
    const choice = await vscode.window.showWarningMessage(
      `No node-config.json at ${file}. Pair Founder Node first.`,
      'Pair Founder Node',
      'Cancel',
    );
    if (choice === 'Pair Founder Node') {
      void vscode.commands.executeCommand('founderOs.pair');
    }
    return;
  }
  const doc = await vscode.workspace.openTextDocument(file);
  await vscode.window.showTextDocument(doc);
}

async function selectModelAlias(): Promise<void> {
  const items = FOUNDER_OS_MODELS.map((m) => ({
    label: m.name,
    description: m.id,
    detail: m.detail,
    picked: m.isDefault,
  }));
  const picked = await vscode.window.showQuickPick(items, {
    placeHolder: 'Select a Founder OS model alias (changes the chat model dropdown selection)',
  });
  if (!picked) return;
  // Open the chat model picker so the user can apply the selection. The
  // underlying provider is already registered; this is a UX hint.
  void vscode.window.showInformationMessage(
    `Selected ${picked.label}. Pick it from the model dropdown in Chat to apply.`,
  );
}

/**
 * Watch `~/FounderVault/node-config.json` for create/change so pairing done
 * while the editor is open is picked up without a manual reload. Best-effort:
 * if the `FounderVault` directory doesn't exist yet, we fall back to watching
 * the home directory one level up and re-check on each event.
 */
function watchVaultFile(
  context: vscode.ExtensionContext,
  onChange: () => void,
): void {
  const file = nodeConfigPath();
  const dir = path.dirname(file);

  let pattern: vscode.RelativePattern;
  try {
    if (!fs.existsSync(dir)) {
      // Watch the home dir for the FounderVault folder appearing at all.
      const home = vscode.Uri.file(os.homedir()).fsPath;
      pattern = new vscode.RelativePattern(
        vscode.Uri.file(home),
        'FounderVault/node-config.json',
      );
    } else {
      pattern = new vscode.RelativePattern(vscode.Uri.file(dir), 'node-config.json');
    }
  } catch {
    return;
  }

  try {
    const watcher = vscode.workspace.createFileSystemWatcher(pattern, false, false, false);
    context.subscriptions.push(watcher);
    context.subscriptions.push(
      watcher.onDidCreate(() => onChange()),
      watcher.onDidChange(() => onChange()),
    );
  } catch {
    // Filesystem watching can fail on some platforms / network drives. Non-fatal.
  }
}
