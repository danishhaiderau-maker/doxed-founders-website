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
} from './device-code-sign-in';
import { PairingStatusBar } from './pairing-status-bar';
import { startIpcServer, stopIpcServer } from './ipc/server';
import { FOUNDER_TOOL_IDS } from './tool-names';
import {
  embeddedRelayExecutable,
  launchEmbeddedRelay,
} from './embedded-relay';
import {
  FOUNDER_AUTH_PROVIDER_ID,
  FounderAuthenticationProvider,
} from './founder-authentication';
import { FounderHubProvider } from './founder-hub';
import {
  FounderSettingsPanel,
  normalizeFounderSettingsTab,
} from './founder-settings';
import { FounderShortcutRegistry } from './founder-shortcuts';
import { FounderCompanionViewProvider } from './founder-companion';
import { FounderAgentAwareness } from './agent-awareness';
import { FounderWorkspaceContextIndex } from './workspace-context-index';
import { FounderSafeResultCache } from './safe-result-cache';
import { FounderVerifiedSolutionMemory } from './verified-solution-memory';
import { FounderProjectActivityStore } from './project-activity';
import { PersonalAiProfileStore } from './personal-ai-profiles';
import { createDailyQualityReview } from './daily-quality-review';
import {
  normalizeFounderWorkMode,
  readFounderWorkMode,
  writeFounderWorkMode,
} from './founder-agent-mode';
import {
  founderInterfaceModeDefinition,
  normalizeFounderInterfaceMode,
} from './founder-interface-mode';
import {
  transcribeManagedVoice,
  type ManagedVoiceInput,
} from './managed-voice';
import {
  describeManagedVisuals,
  type ManagedVisualInput,
} from './managed-visual';

let registeredParticipant: vscode.Disposable | undefined;
let profileManager: ProfileManager | undefined;
let costTracker: CostTracker | undefined;
let gatewayMetadataUi: GatewayMetadataUi | undefined;
let debugSquasherDisposable: vscode.Disposable | undefined;
let currentCreds: FounderOsCredentials | null = null;
/** Phase 2 — pairing-state status bar (refreshes every 15s + on config change). */
let pairingStatusBar: PairingStatusBar | undefined;
let founderAuthenticationProvider: FounderAuthenticationProvider | undefined;
let founderHub: FounderHubProvider | undefined;
let founderSettings: FounderSettingsPanel | undefined;
let founderShortcuts: FounderShortcutRegistry | undefined;
let founderCompanion: FounderCompanionViewProvider | undefined;
let founderAgentAwareness: FounderAgentAwareness | undefined;
let founderWorkspaceContext: FounderWorkspaceContextIndex | undefined;
let founderSafeResultCache: FounderSafeResultCache | undefined;
let founderVerifiedSolutionMemory: FounderVerifiedSolutionMemory | undefined;
let founderProjectActivity: FounderProjectActivityStore | undefined;
let personalAiProfiles: PersonalAiProfileStore | undefined;
let dailyQualityReviewDisposable: vscode.Disposable | undefined;

export function activate(context: vscode.ExtensionContext): void {
  startEmbeddedRelay();

  // One canonical status control for pairing, requests and route health.
  pairingStatusBar = new PairingStatusBar();
  context.subscriptions.push(pairingStatusBar);
  pairingStatusBar.start();

  // Founder identity and control surface.
  founderHub = new FounderHubProvider(context);
  context.subscriptions.push(
    founderHub,
    vscode.window.registerWebviewViewProvider(FounderHubProvider.viewId, founderHub, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );
  founderShortcuts = new FounderShortcutRegistry();
  context.subscriptions.push(founderShortcuts);
  founderCompanion = new FounderCompanionViewProvider(context);
  context.subscriptions.push(
    founderCompanion,
    vscode.tasks.onDidStartTask((event) => {
      founderCompanion?.setVerifying(`Checking ${event.execution.task.name}`, 'Running a local workspace task');
    }),
    vscode.tasks.onDidEndTaskProcess((event) => {
      if (event.exitCode === 0) {
        founderCompanion?.setSuccess(event.execution.task.name, 'Task completed with exit code 0');
      } else {
        founderCompanion?.setError(
          event.execution.task.name,
          event.exitCode == null ? 'Task ended without a result' : `Task exited with code ${event.exitCode}`,
        );
      }
    }),
  );
  founderAgentAwareness = new FounderAgentAwareness();
  founderHub.setAgentAwareness(founderAgentAwareness.summary());
  context.subscriptions.push(
    founderAgentAwareness,
    founderAgentAwareness.onDidChange((summary) => {
      founderHub?.setAgentAwareness(summary);
      if (summary.conflictCount > 0) {
        founderCompanion?.setCoordinating(
          'Agents are coordinating',
          `${summary.conflictCount} overlapping task${summary.conflictCount === 1 ? '' : 's'} detected`,
        );
      }
    }),
  );
  founderWorkspaceContext = new FounderWorkspaceContextIndex(context);
  context.subscriptions.push(founderWorkspaceContext);
  founderSafeResultCache = new FounderSafeResultCache(
    path.join(context.globalStorageUri.fsPath, 'safe-result-cache'),
  );
  context.subscriptions.push(
    founderWorkspaceContext.onDidInvalidate(({ workspaceId }) => {
      founderSafeResultCache?.invalidateWorkspace(workspaceId);
    }),
  );
  founderVerifiedSolutionMemory = new FounderVerifiedSolutionMemory(
    path.join(context.globalStorageUri.fsPath, 'verified-solutions'),
  );
  founderProjectActivity = new FounderProjectActivityStore(
    path.join(context.globalStorageUri.fsPath, 'project-activity.json'),
  );
  dailyQualityReviewDisposable = createDailyQualityReview(context, {
    activity: founderProjectActivity,
    workspaceId: () => founderWorkspaceContext?.workspaceIdValue() ?? null,
    awareness: () => founderAgentAwareness?.summary() ?? {
      activeCount: 0,
      conflictCount: 0,
      tasks: [],
    },
  });
  context.subscriptions.push(dailyQualityReviewDisposable);

  founderAuthenticationProvider = new FounderAuthenticationProvider({
    onDidSignIn: async () => {
      await syncVaultIntoSettings();
      await startIpcServer();
      registerOrNotify(context);
      pairingStatusBar?.setGatewayResult('ok');
      founderHub?.refresh();
      founderSettings?.refresh();
      founderShortcuts?.refresh();
      founderCompanion?.setSuccess('Founder connected', 'Identity, Node, and remote control are ready');
    },
    onDidSignOut: () => {
      registerOrNotify(context);
      pairingStatusBar?.refresh();
      founderHub?.refresh();
      founderSettings?.refresh();
      founderShortcuts?.refresh();
      founderCompanion?.setOffline('Sign in required', 'Connect Founder to use managed AI and remote control');
    },
  });
  context.subscriptions.push(
    founderAuthenticationProvider,
    vscode.authentication.registerAuthenticationProvider(
      FOUNDER_AUTH_PROVIDER_ID,
      'Founder',
      founderAuthenticationProvider,
      { supportsMultipleAccounts: false },
    ),
  );

  // Execution-profile selector + DDollar cost tracker (independent of creds) ---------
  profileManager = new ProfileManager(context, { showStatusBar: false });
  personalAiProfiles = new PersonalAiProfileStore(context, {
    save: async (profile) => {
      await vscode.commands.executeCommand('founder.personalAi.save', profile);
    },
    select: async (id) => {
      await vscode.commands.executeCommand('founder.personalAi.select', id);
    },
    setEnabled: async (id, enabled) => {
      await vscode.commands.executeCommand('founder.personalAi.enable', { id, enabled });
    },
    delete: async (id) => {
      await vscode.commands.executeCommand('founder.personalAi.delete', id);
    },
  });
  costTracker = new CostTracker();
  gatewayMetadataUi = new GatewayMetadataUi();
  context.subscriptions.push(profileManager, personalAiProfiles, costTracker, gatewayMetadataUi);
  profileManager.show();
  founderSettings = new FounderSettingsPanel({
    getProfile: () => profileManager!.profile,
    setProfile: (id) => profileManager!.setProfile(id),
    getManagedAlias: () => profileManager!.alias,
    setManagedAlias: async (id) => {
      await profileManager!.setAlias(id);
      await vscode.commands.executeCommand('founder.managedAi.select', id);
    },
    personalAiProfiles,
  });
  context.subscriptions.push(founderSettings);
  context.subscriptions.push(
    personalAiProfiles.onDidChange(() => {
      founderSettings?.refresh();
      founderHub?.refresh();
    }),
  );
  void personalAiProfiles.ready().then(() => {
    founderSettings?.refresh();
    registerOrNotify(context);
  });

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
    vscode.commands.registerCommand('founderOs.signOut', async () => {
      const signedOut = await founderAuthenticationProvider?.signOut();
      if (!signedOut) {
        void vscode.window.showInformationMessage('Founder: no account is signed in.');
      }
    }),
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
    vscode.commands.registerCommand('founderOs.openHub', () =>
      vscode.commands.executeCommand('workbench.view.extension.founderOs'),
    ),
    vscode.commands.registerCommand('founderOs.openCompanion', async () => {
      await vscode.workspace.getConfiguration('founderOs').update(
        'companion.enabled',
        true,
        vscode.ConfigurationTarget.Global,
      );
      founderCompanion?.syncEnabled();
    }),
    vscode.commands.registerCommand(
      'founderOs.companionState',
      (state: unknown, title: unknown, detail: unknown) => {
        const safeTitle = typeof title === 'string' ? title.slice(0, 96) : 'Founder Dragon';
        const safeDetail = typeof detail === 'string' ? detail.slice(0, 220) : '';
        switch (state) {
          case 'listening':
            founderCompanion?.setListening(safeTitle, safeDetail);
            break;
          case 'planning':
            founderCompanion?.setPlanning(safeTitle, safeDetail);
            break;
          case 'working':
            founderCompanion?.setWorking(safeTitle, safeDetail);
            break;
          case 'coordinating':
            founderCompanion?.setCoordinating(safeTitle, safeDetail);
            break;
          case 'verifying':
            founderCompanion?.setVerifying(safeTitle, safeDetail);
            break;
          case 'success':
            founderCompanion?.setSuccess(safeTitle, safeDetail);
            break;
          case 'attention':
            founderCompanion?.setAttention(safeTitle, safeDetail);
            break;
          case 'error':
            founderCompanion?.setError(safeTitle, safeDetail);
            break;
          case 'offline':
            founderCompanion?.setOffline(safeTitle, safeDetail);
            break;
          case 'update':
            founderCompanion?.setUpdate(safeTitle, safeDetail);
            break;
          default:
            founderCompanion?.setIdle();
        }
      },
    ),
    vscode.commands.registerCommand('founderOs.openAgents', async () => {
      await vscode.commands.executeCommand('workbench.view.extension.founderOs');
      await vscode.commands.executeCommand(`${FounderHubProvider.viewId}.focus`);
    }),
    vscode.commands.registerCommand('founderOs.openShip', () =>
      revealFounderView('founderOs', 'founderOs.ship'),
    ),
    vscode.commands.registerCommand('founderOs.openNodeView', () =>
      revealFounderView('founderOs', 'founderOs.node'),
    ),
    vscode.commands.registerCommand('founderOs.openConnectionsView', () =>
      revealFounderView('founderOs', 'founderOs.connections'),
    ),
    vscode.commands.registerCommand('founderOs.openRemoteView', () =>
      revealFounderView('founderOs', 'founderOs.remote'),
    ),
    vscode.commands.registerCommand('founderOs.openRemoteControl', () =>
      vscode.env.openExternal(
        vscode.Uri.parse('https://doxxedcrypto.digital/founder-den?onboard=sovereign'),
      ),
    ),
    vscode.commands.registerCommand('founderOs.openChat', async () => {
      try {
        await vscode.commands.executeCommand('void.sidebar.open');
      } catch {
        await vscode.commands.executeCommand('workbench.action.chat.open');
      }
    }),
    vscode.commands.registerCommand('founderOs.openConnections', () =>
      vscode.env.openExternal(
        vscode.Uri.parse('https://doxxedcrypto.digital/settings/builder'),
      ),
    ),
    vscode.commands.registerCommand('founderOs.openSettings', (tab?: unknown) =>
      founderSettings?.show(normalizeFounderSettingsTab(tab)),
    ),
    vscode.commands.registerCommand('founderOs.refreshHub', () =>
      founderHub?.refresh(),
    ),
    vscode.commands.registerCommand('founderOs.refreshShortcuts', () =>
      founderShortcuts?.refresh(),
    ),
    vscode.commands.registerCommand('founderOs.getWorkMode', () =>
      readFounderWorkMode(),
    ),
    vscode.commands.registerCommand('founderOs.setWorkMode', async (value: unknown) => {
      const workMode = normalizeFounderWorkMode(value);
      writeFounderWorkMode(workMode);
      await vscode.workspace
        .getConfiguration('founderOs')
        .update(
          'agentMode',
          workMode === 'team' ? 'team' : 'focus',
          vscode.ConfigurationTarget.Global,
        );
      founderHub?.refresh();
      return workMode;
    }),
    vscode.commands.registerCommand(
      'founderOs.transcribeVoice',
      (input: ManagedVoiceInput) => transcribeManagedVoice(input),
    ),
    vscode.commands.registerCommand(
      'founderOs.describeVisualAttachments',
      (input: ManagedVisualInput) => describeManagedVisuals(input),
    ),
    vscode.commands.registerCommand('founderOs.refreshProjectContext', async () => {
      await founderWorkspaceContext?.refresh(true);
      const summary = founderWorkspaceContext?.summary();
      void vscode.window.showInformationMessage(
        summary
          ? `Founder project map refreshed: ${summary.files} files, ${summary.symbols} symbols.`
          : 'Open a folder to build the Founder project map.',
      );
    }),
    vscode.commands.registerCommand('founderOs.openProjectBrief', openProjectBrief),
  );

  void applyFounderInterfaceMode(true);
  const navigationTimer = setTimeout(
    () => void applyFounderInterfaceMode(true),
    15_000,
  );
  context.subscriptions.push({
    dispose: () => clearTimeout(navigationTimer),
  });

  // First-pass registration (synchronous so the model picker populates fast).
  registerOrNotify(context);

  // Phase 3 — start the named-pipe IPC server so the embedded relay can
  // connect to this IDE instance. The server only starts if we have a config
  // with an installId (otherwise it's a no-op until the user signs in).
  // Best-effort: failure to start (e.g. another IDE instance already bound)
  // logs a warning but does not crash activation.
  if (vaultFileExists()) {
    // Legacy paired vaults predate install.json. Mint the local pipe identity
    // before starting the server so either the IDE or relay may launch
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
    founderAuthenticationProvider?.refresh();
    founderHub?.refresh();
    founderSettings?.refresh();
    founderShortcuts?.refresh();
  });

  // Re-resolve when relevant settings change.
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('founderOs')) {
        if (e.affectsConfiguration('founderOs.interfaceMode')) {
          void applyFounderInterfaceMode(false);
        }
        registerOrNotify(context);
        founderCompanion?.syncEnabled();
        founderHub?.refresh();
        founderSettings?.refresh();
        founderShortcuts?.refresh();
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
      founderAuthenticationProvider?.refresh();
      founderHub?.refresh();
      founderSettings?.refresh();
      founderShortcuts?.refresh();
    });
  });
}

async function applyFounderInterfaceMode(revealFounderHome: boolean): Promise<void> {
  // Apply the selected profile for every window. This avoids stale window
  // layout state silently reopening the old IDE rail in a new workspace.
  const founder = vscode.workspace.getConfiguration('founderOs');
  const mode = normalizeFounderInterfaceMode(founder.get<string>('interfaceMode'));
  const definition = founderInterfaceModeDefinition(mode);
  const workbench = vscode.workspace.getConfiguration('workbench');
  if (workbench.get<string>('activityBar.location') !== definition.activityBarLocation) {
    await workbench.update(
      'activityBar.location',
      definition.activityBarLocation,
      vscode.ConfigurationTarget.Global,
    );
  }
  const windowConfig = vscode.workspace.getConfiguration('window');
  if (windowConfig.get<string>('menuBarVisibility') !== definition.menuBarVisibility) {
    await windowConfig.update(
      'menuBarVisibility',
      definition.menuBarVisibility,
      vscode.ConfigurationTarget.Global,
    );
  }
  if (founder.get<boolean>('advancedIdeTools') !== definition.advancedIdeTools) {
    await founder.update(
      'advancedIdeTools',
      definition.advancedIdeTools,
      vscode.ConfigurationTarget.Global,
    );
  }
  if (mode === 'founder' && revealFounderHome) {
    await vscode.commands.executeCommand('workbench.view.extension.founderOs');
  }
}

async function revealFounderView(containerId: string, viewId: string): Promise<void> {
  await vscode.commands.executeCommand(`workbench.view.extension.${containerId}`);
  await vscode.commands.executeCommand(`${viewId}.focus`);
}

function startEmbeddedRelay(): void {
  // Stock VS Code/Cursor installs of this extension simply skip this step.
  const relayRuntime = { runtimeExecutable: process.execPath };
  const embeddedRelay = embeddedRelayExecutable(
    vscode.env.appRoot,
    process.platform,
    relayRuntime.runtimeExecutable,
  );
  const relayExists = Boolean(embeddedRelay && fs.existsSync(embeddedRelay));
  logRelayStartup(
    `candidate=${embeddedRelay ?? 'unsupported'} exists=${relayExists} appRoot=${vscode.env.appRoot} execPath=${process.execPath}`,
  );
  if (!relayExists) return;

  try {
    ensureInstallIdentity();
  } catch (error) {
    const message = error instanceof Error ? error.stack ?? error.message : String(error);
    logRelayStartup(`error=${message}`);
    console.warn('Founder IDE relay identity check failed:', error);
    return;
  }
  void launchEmbeddedRelay(
    vscode.env.appRoot,
    process.platform,
    relayRuntime,
  ).then((relay) => {
    logRelayStartup(`state=${relay.state} pid=${relay.pid ?? 'none'}`);
    console.log(`Founder IDE relay: ${relay.state}`);
  }).catch((error) => {
    const message = error instanceof Error ? error.stack ?? error.message : String(error);
    logRelayStartup(`error=${message}`);
    console.warn('Founder IDE relay failed to start:', error);
  });
}

export function deactivate(): void {
  registeredParticipant?.dispose();
  profileManager?.dispose();
  costTracker?.dispose();
  gatewayMetadataUi?.dispose();
  debugSquasherDisposable?.dispose();
  pairingStatusBar?.dispose();
  founderAuthenticationProvider?.dispose();
  founderHub?.dispose();
  founderSettings?.dispose();
  founderShortcuts?.dispose();
  dailyQualityReviewDisposable?.dispose();
  // Phase 3 — stop the named-pipe IPC server so we release the pipe name.
  stopIpcServer();
}

/** Register the chat participant if we have creds; otherwise show "not paired". */
function registerOrNotify(context: vscode.ExtensionContext): void {
  const creds = resolveCredentials();
  if (
    registeredParticipant
    && (
      (!currentCreds && !creds)
      || (currentCreds && creds
        && currentCreds.apiBaseUrl === creds.apiBaseUrl
        && currentCreds.nodeId === creds.nodeId
        && currentCreds.nodeToken === creds.nodeToken)
    )
  ) {
    pairingStatusBar?.refresh();
    founderShortcuts?.refresh();
    return;
  }

  // Credentials changed — re-register.
  registeredParticipant?.dispose();
  debugSquasherDisposable?.dispose();

  registeredParticipant = registerFounderOsChatParticipant(context, {
    creds: creds ?? undefined,
    profileManager: profileManager!,
    personalAiProfiles,
    costTracker: costTracker!,
    coordination: founderAgentAwareness,
    projectContext: founderWorkspaceContext,
    resultCache: founderSafeResultCache,
    solutionMemory: founderVerifiedSolutionMemory,
    projectActivity: founderProjectActivity,
    onRequestStart: (modelId) => {
      pairingStatusBar?.setRequestInFlight(modelId);
      founderCompanion?.setPlanning('Planning the route', modelId);
    },
    onMetadata: (meta) => {
      const tier = meta.tier ?? '?';
      const provider2 = meta.provider ?? '';
      const model = meta.model ?? '';
      pairingStatusBar?.setRouteDetails({
        tier,
        provider: provider2,
        model,
        cost: meta.ddollarCost,
      });
      costTracker?.record(meta);
      gatewayMetadataUi?.record(meta);
      founderCompanion?.setWorking(
        `Reaching ${provider2 || 'the selected provider'}`,
        model || tier,
      );
    },
    onCacheHit: (estimatedTokensAvoided) => {
      founderCompanion?.setSuccess(
        'Verified context reused',
        `Provider skipped; about ${estimatedTokensAvoided.toLocaleString()} tokens avoided`,
      );
    },
    onRequestEnd: (_modelId, ok, errorMessage) => {
      pairingStatusBar?.setRequestResult(ok, errorMessage);
      if (ok) {
        founderCompanion?.setSuccess('Response delivered', 'Founder AI reached the workspace');
      } else {
        founderCompanion?.setError('Founder AI was blocked', errorMessage || 'Open evidence for details');
      }
    },
  });

  // Debug Squasher status bar — polls /api/debug-squasher/latest every 2 min.
  if (creds) {
    debugSquasherDisposable = createDebugSquasherStatus(
      context,
      () => resolveCredentials(),
      { showStatusBar: false },
    );
    context.subscriptions.push(debugSquasherDisposable);
  } else {
    debugSquasherDisposable = undefined;
    void showPairPrompt(context);
  }

  currentCreds = creds;
  pairingStatusBar?.refresh();
  founderHub?.refresh();
  founderSettings?.refresh();
  founderShortcuts?.refresh();
}

let pairPromptShownThisSession = false;
async function showPairPrompt(context: vscode.ExtensionContext): Promise<void> {
  if (pairPromptShownThisSession) return;
  pairPromptShownThisSession = true;
  const hasOpenedHub = context.globalState.get<boolean>(
    'founderOs.hubOpened',
    false,
  );
  if (!hasOpenedHub) {
    await context.globalState.update('founderOs.hubOpened', true);
    await vscode.commands.executeCommand('workbench.view.extension.founderOs');
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
    const session = await vscode.authentication.getSession(
      FOUNDER_AUTH_PROVIDER_ID,
      ['founder'],
      { createIfNone: true },
    );
    if (!session) return;
    // Reload from the vault file so the chat provider picks up the new creds.
    await syncVaultIntoSettings();
    await startIpcServer();
    registerOrNotify(context);
    pairingStatusBar.setGatewayResult('ok');
    founderHub?.refresh();
    void vscode.window.showInformationMessage(
      `Founder: signed in as ${session.account.label}.`,
    );
  } catch (error) {
    if (!(error instanceof vscode.CancellationError)) {
      const message = error instanceof Error ? error.message : String(error);
      void vscode.window.showErrorMessage(`Founder sign-in failed: ${message}`);
    }
  } finally {
    pairingStatusBar.setPairingInProgress(false);
  }
}

async function manageConnection(context: vscode.ExtensionContext): Promise<void> {
  const items: (vscode.QuickPickItem & { action: () => unknown })[] = [
    {
      label: 'Sign in with X to Founder OS…',
      description: 'connect this Founder IDE and its background relay',
      action: () => signInWithFounderId(context),
    },
    {
      label: 'Load existing connection from vault…',
      description: vaultFileExists() ? 'vault file present — no pairing code needed' : 'vault file missing',
      action: () => pairWithFounderNode(context),
    },
    {
      label: 'Open settings (founderOs.*)',
      description: 'workspace mode and advanced connection settings',
      action: () => vscode.commands.executeCommand('founderOs.openSettings'),
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

  // Prefer vault auto-load — the embedded relay shares this connection file.
  if (vaultFileExists()) {
    const synced = await syncVaultIntoSettings();
    if (synced) {
      registerOrNotify(context);
      const open = await vscode.window.showInformationMessage(
        `Loaded Founder IDE connection from:\n${file}\n\napiBaseUrl=${synced.apiBaseUrl}\nnodeId=${synced.nodeId}`,
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

  const choice = await vscode.window.showInformationMessage(
    'Founder IDE is not connected yet. Sign in with X to connect this computer securely.',
    'Sign in with X',
    'Founder Settings',
    'Cancel',
  );
  if (choice === 'Sign in with X') {
    await signInWithFounderId(context);
  } else if (choice === 'Founder Settings') {
    void vscode.commands.executeCommand('founderOs.openSettings');
  }
}

function logRelayStartup(message: string): void {
  try {
    const logDir = path.join(os.homedir(), 'FounderVault', 'logs');
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(
      path.join(logDir, 'founder-ide-extension.log'),
      `[${new Date().toISOString()}] ${message}\n`,
      'utf8',
    );
  } catch {
    // Startup tracing is best-effort and never blocks the editor.
  }
}

async function openVaultConfig(): Promise<void> {
  const file = nodeConfigPath();
  if (!fs.existsSync(file)) {
    const choice = await vscode.window.showWarningMessage(
      `No Founder IDE connection exists at ${file}.`,
      'Sign in with X',
      'Cancel',
    );
    if (choice === 'Sign in with X') {
      void vscode.commands.executeCommand('founderOs.signIn');
    }
    return;
  }
  const doc = await vscode.workspace.openTextDocument(file);
  await vscode.window.showTextDocument(doc);
}

async function openProjectBrief(): Promise<void> {
  const workspaceId = founderWorkspaceContext?.workspaceIdValue();
  const workspaceName = vscode.workspace.workspaceFolders?.[0]?.name?.trim() || 'Founder project';
  if (!workspaceId || !founderProjectActivity) {
    void vscode.window.showInformationMessage('Open a project to view its Founder brief.');
    return;
  }
  const document = await vscode.workspace.openTextDocument({
    language: 'markdown',
    content: founderProjectActivity.dailyBrief(workspaceId, workspaceName),
  });
  await vscode.window.showTextDocument(document, { preview: true });
}

async function selectModelAlias(): Promise<void> {
  await personalAiProfiles?.ready();
  const activePersonalId = personalAiProfiles?.activeId();
  const items: Array<vscode.QuickPickItem & {
    routeKind: 'managed' | 'personal';
    id: string;
  }> = [
    ...FOUNDER_OS_MODELS.map((model) => ({
      label: `$(sparkle) ${model.name}`,
      description: model.id,
      detail: model.detail,
      picked: !activePersonalId && profileManager?.alias.id === model.id,
      routeKind: 'managed' as const,
      id: model.id,
    })),
    ...(personalAiProfiles?.list().filter((profile) => profile.enabled).map((profile) => ({
      label: `${profile.kind === 'ollama' ? '$(device-desktop)' : '$(key)'} ${profile.name}`,
      description: profile.kind === 'ollama' ? 'Local | outside managed quota' : 'Personal AI | outside managed quota',
      detail: `${profile.model} | ${profile.baseUrl}`,
      picked: activePersonalId === profile.id,
      routeKind: 'personal' as const,
      id: profile.id,
    })) ?? []),
  ];
  const picked = await vscode.window.showQuickPick(items, {
    title: 'Choose AI for Founder Chat',
    placeHolder: 'Founder managed, Personal AI, or local Ollama',
  });
  if (!picked) return;
  if (picked.routeKind === 'managed') {
    await personalAiProfiles?.select(null);
    const aliasId = picked.id as (typeof FOUNDER_OS_MODELS)[number]['id'];
    await profileManager?.setAlias(aliasId);
    await vscode.commands.executeCommand('founder.managedAi.select', aliasId);
  } else {
    await personalAiProfiles?.select(picked.id);
    void vscode.window.showInformationMessage(`${picked.label.replace(/^\$\([^)]+\)\s*/, '')} is now active.`);
  }
  founderSettings?.refresh();
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
