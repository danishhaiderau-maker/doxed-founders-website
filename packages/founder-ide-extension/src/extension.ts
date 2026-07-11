/**
 * Activation entry point for the Founder OS chat extension.
 *
 * On activation:
 *   1. Resolve credentials (settings → `~/FounderVault/node-config.json`).
 *   2. Register the `LanguageModelChatProvider` + agentic tools + chat participant.
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
  vaultFileExists,
} from './credentials';
import { FounderOsChatProvider } from './chat-provider';
import { FOUNDER_OS_MODELS, FOUNDER_OS_VENDOR } from './models';
import { editFileTool } from './tools/edit-file';
import { runCommandTool } from './tools/run-command';
import { readWorkspaceTool } from './tools/read-workspace';
import { ProfileManager } from './profile-manager';
import { CostTracker } from './cost-tracker';
import { registerFounderOsChatParticipant } from './chat-participant';
import { createDebugSquasherStatus } from './debug-squasher-status';

let connectionStatusBar: vscode.StatusBarItem | undefined;
let registeredProvider: vscode.Disposable | undefined;
let registeredParticipant: vscode.Disposable | undefined;
let profileManager: ProfileManager | undefined;
let costTracker: CostTracker | undefined;
let debugSquasherDisposable: vscode.Disposable | undefined;
let currentCreds: FounderOsCredentials | null = null;

export function activate(context: vscode.ExtensionContext): void {
  // Status bar (connection state) ----------------------------------------------------
  connectionStatusBar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  connectionStatusBar.command = 'founderOs.manage';
  context.subscriptions.push(connectionStatusBar);

  // Execution-profile selector + DDollar cost tracker (independent of creds) ---------
  profileManager = new ProfileManager(context);
  costTracker = new CostTracker();
  context.subscriptions.push(profileManager, costTracker);
  profileManager.show();
  costTracker.show();

  // Agentic tools (registered once; available to any chat participant / model) ------
  context.subscriptions.push(
    vscode.lm.registerTool('founder.editFile', editFileTool),
    vscode.lm.registerTool('founder.runCommand', runCommandTool),
    vscode.lm.registerTool('founder.readWorkspace', readWorkspaceTool),
  );

  // Commands -------------------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand('founderOs.manage', () => manageConnection(context)),
    vscode.commands.registerCommand('founderOs.pair', () => pairWithFounderNode(context)),
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
  );

  // First-pass registration (synchronous so the model picker populates fast).
  registerOrNotify(context);

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
  watchVaultFile(context, () => registerOrNotify(context));
}

export function deactivate(): void {
  registeredProvider?.dispose();
  registeredParticipant?.dispose();
  connectionStatusBar?.dispose();
  profileManager?.dispose();
  costTracker?.dispose();
  debugSquasherDisposable?.dispose();
}

/** Register the chat provider + participant if we have creds; otherwise show "not paired". */
function registerOrNotify(context: vscode.ExtensionContext): void {
  const creds = resolveCredentials();
  if (!creds) {
    registeredProvider?.dispose();
    registeredProvider = undefined;
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
    currentCreds.nodeToken === creds.nodeToken &&
    registeredProvider
  ) {
    // Already registered with identical creds — just refresh the label.
    setStatusConnected(currentCreds);
    return;
  }

  // Credentials changed — re-register.
  registeredProvider?.dispose();
  registeredParticipant?.dispose();
  debugSquasherDisposable?.dispose();

  const provider = new FounderOsChatProvider(creds, {
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

  registeredProvider = vscode.lm.registerLanguageModelChatProvider(
    FOUNDER_OS_VENDOR,
    provider,
  );
  context.subscriptions.push(registeredProvider);

  // Enhanced chat participant — drives a real vscode.lm round-trip with
  // Memory Engine injection + tool use. Falls back to onboarding only if the
  // participant id is already claimed by another extension.
  registeredParticipant = registerFounderOsChatParticipant(context, {
    creds,
    profileManager: profileManager!,
    costTracker: costTracker!,
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
  const choice = await vscode.window.showWarningMessage(
    'Founder OS chat: Founder Node not paired. Pair it to enable Founder OS models in Chat.',
    'Pair Founder Node',
    'Open settings',
    'Dismiss',
  );
  if (choice === 'Pair Founder Node') {
    await pairWithFounderNode(context);
  } else if (choice === 'Open settings') {
    void vscode.commands.executeCommand(
      'workbench.action.openSettings',
      'founderOs',
    );
  }
}

async function manageConnection(context: vscode.ExtensionContext): Promise<void> {
  const items: (vscode.QuickPickItem & { action: () => unknown })[] = [
    {
      label: 'Pair with Founder Node…',
      description: vaultFileExists() ? 'vault file present' : 'vault file missing',
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
  if (vaultFileExists()) {
    const open = await vscode.window.showInformationMessage(
      `Founder Node config already exists at:\n${file}\n\nOpen it, or reload the window to re-detect?`,
      'Open file',
      'Reload window',
      'Cancel',
    );
    if (open === 'Open file') await openVaultConfig();
    else if (open === 'Reload window')
      void vscode.commands.executeCommand('workbench.action.reloadWindow');
    return;
  }

  const choice = await vscode.window.showInformationMessage(
    'Founder OS chat needs a paired Founder Node.\n\nOpen Founder Node on this machine and click "Connect IDE / Pair". This writes ~/FounderVault/node-config.json, which the extension reads automatically.',
    'Open Founder OS docs',
    'Set credentials manually',
    'Cancel',
  );
  if (choice === 'Set credentials manually') {
    void vscode.commands.executeCommand(
      'workbench.action.openSettings',
      'founderOs',
    );
  } else if (choice === 'Open Founder OS docs') {
    void vscode.env.openExternal(
      vscode.Uri.parse('https://doxxedcrypto.digital/docs/founder-node'),
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
