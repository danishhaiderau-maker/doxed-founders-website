import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import { readVaultConfig, resolveCredentials } from './credentials';
import type { FounderAgentAwarenessSummary } from './agent-awareness';
import {
  FOUNDER_AGENT_MODES,
  founderAgentModeDefinition,
  normalizeFounderAgentMode,
  writeFounderAgentMode,
  type FounderAgentMode,
} from './founder-agent-mode';
import {
  normalizeFounderInterfaceMode,
} from './founder-interface-mode';
import {
  createFounderGoalAmendmentDecision,
  createFounderHousekeepingDecision,
  enqueueFounderDecision,
  formatFounderGoalBytes,
  initialFounderGoalState,
  normalizeFounderGoalState,
  pendingFounderGoalDecisions,
  resolveFounderGoalUiDecision,
  type FounderGoalUiDecision,
  type FounderGoalUiState,
} from './founder-goal-state';

type FounderHubAction =
  | 'signIn'
  | 'signOut'
  | 'manageConnection'
  | 'newChat'
  | 'openProjects'
  | 'openChats'
  | 'openAgents'
  | 'openPreview'
  | 'openDeploy'
  | 'openFiles'
  | 'openSearch'
  | 'openSourceControl'
  | 'openTerminal'
  | 'runTask'
  | 'openExtensions'
  | 'openChat'
  | 'openConnections'
  | 'openRemote'
  | 'openSettings'
  | 'openNodeConfig'
  | 'showUsage'
  | 'openProjectBrief'
  | 'toggleAdvancedTools'
  | 'toggleCompanion';

interface FounderHubMessage {
  type:
    | 'action'
    | 'selectAgentMode'
    | 'editGoal'
    | 'resolveDecision';
  action?: FounderHubAction;
  agentMode?: FounderAgentMode;
  decisionId?: string;
  optionId?: string;
  selectedCandidateIds?: string[];
}

export class FounderHubProvider
  implements vscode.WebviewViewProvider, vscode.Disposable
{
  static readonly viewId = 'founderOs.hub';

  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  private agentAwareness: FounderAgentAwarenessSummary = {
    activeCount: 0,
    conflictCount: 0,
    tasks: [],
  };
  private goalState: FounderGoalUiState;

  constructor(private readonly context: vscode.ExtensionContext) {
    const workspaceName =
      vscode.workspace.workspaceFolders?.[0]?.name?.trim() || 'this project';
    this.goalState = normalizeFounderGoalState(
      context.workspaceState.get<unknown>('founder.goalState'),
      workspaceName,
    );
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [],
    };
    this.disposables.push(
      view.webview.onDidReceiveMessage((message: FounderHubMessage) =>
        this.handleMessage(message),
      ),
    );
    this.refresh();
  }

  refresh(): void {
    if (!this.view) return;
    this.view.webview.html = this.renderHtml();
  }

  setAgentAwareness(summary: FounderAgentAwarenessSummary): void {
    this.agentAwareness = summary;
    this.refresh();
  }

  async queueDecision(decision: unknown): Promise<void> {
    this.goalState = enqueueFounderDecision(this.goalState, decision);
    await this.persistGoalState();
    this.refresh();
  }

  dispose(): void {
    for (const disposable of this.disposables) disposable.dispose();
    this.disposables.length = 0;
  }

  private async handleMessage(message: FounderHubMessage): Promise<void> {
    if (message.type === 'editGoal') {
      const objective = await vscode.window.showInputBox({
        title: 'Edit pursuing goal',
        prompt: 'Describe the outcome Founder AI must keep pursuing.',
        value: this.goalState.objective,
        validateInput: (value) =>
          value.trim().length < 8 ? 'Use at least 8 characters.' : null,
      });
      if (objective) {
        this.goalState = enqueueFounderDecision(
          this.goalState,
          createFounderGoalAmendmentDecision(this.goalState, objective),
        );
        await this.persistGoalState();
        this.refresh();
      }
      return;
    }

    if (
      message.type === 'resolveDecision'
      && message.decisionId
      && message.optionId
    ) {
      const decision = this.goalState.decisions.find(
        (item) => item.id === message.decisionId && item.status === 'pending',
      );
      if (!decision) return;
      let customAnswer: string | undefined;
      let selectedOptionId: string | undefined = message.optionId;
      if (message.optionId === '__custom__') {
        selectedOptionId = undefined;
        customAnswer = await vscode.window.showInputBox({
          title: decision.title,
          prompt: decision.question,
          placeHolder: 'Write your answer',
          validateInput: (value) =>
            value.trim().length < 2 ? 'Write an answer or cancel.' : null,
        });
        if (!customAnswer) return;
      }
      try {
        this.goalState = resolveFounderGoalUiDecision(this.goalState, {
          decisionId: decision.id,
          selectedOptionId,
          customAnswer,
          selectedCandidateIds: message.selectedCandidateIds,
        });
        await this.persistGoalState();
        this.refresh();
      } catch (error) {
        void vscode.window.showErrorMessage(
          error instanceof Error ? error.message : String(error),
        );
      }
      return;
    }

    if (message.type === 'selectAgentMode' && message.agentMode) {
      const agentMode = normalizeFounderAgentMode(message.agentMode);
      await vscode.workspace
        .getConfiguration('founderOs')
        .update('agentMode', agentMode, vscode.ConfigurationTarget.Global);
      writeFounderAgentMode(agentMode);
      this.refresh();
      return;
    }

    if (message.type !== 'action' || !message.action) return;
    switch (message.action) {
      case 'signIn':
        await vscode.commands.executeCommand('founderOs.signIn');
        break;
      case 'signOut':
        await vscode.commands.executeCommand('founderOs.signOut');
        break;
      case 'manageConnection':
        await vscode.commands.executeCommand('founderOs.manage');
        break;
      case 'newChat': {
        const commands = new Set(await vscode.commands.getCommands(true));
        if (commands.has('void.cmdShiftL')) {
          await vscode.commands.executeCommand('void.cmdShiftL');
        } else if (commands.has('workbench.action.chat.newChat')) {
          await vscode.commands.executeCommand('workbench.action.chat.newChat');
        }
        await vscode.commands.executeCommand('founderOs.openChat');
        break;
      }
      case 'openProjects':
        await vscode.commands.executeCommand('founderOs.openProjects');
        break;
      case 'openChats': {
        await vscode.commands.executeCommand('founderOs.openChat');
        const commands = new Set(await vscode.commands.getCommands(true));
        if (commands.has('void.historyAction')) {
          await vscode.commands.executeCommand('void.historyAction');
        }
        break;
      }
      case 'openAgents':
        await vscode.commands.executeCommand('founderOs.openAgents');
        break;
      case 'openPreview': {
        const previewUrl = vscode.workspace
          .getConfiguration('founderOs')
          .get<string>('previewUrl', 'http://localhost:3000')
          .trim();
        try {
          await vscode.commands.executeCommand(
            'simpleBrowser.api.open',
            vscode.Uri.parse(previewUrl),
            {
              viewColumn: vscode.ViewColumn.Active,
              preserveFocus: false,
            },
          );
          await vscode.commands.executeCommand(
            'workbench.action.focusActiveEditorGroup',
          );
        } catch {
          await vscode.env.openExternal(vscode.Uri.parse(previewUrl));
        }
        break;
      }
      case 'openDeploy':
        await vscode.commands.executeCommand('founderOs.openConnectionsView');
        break;
      case 'openFiles':
        await vscode.commands.executeCommand('workbench.view.explorer');
        break;
      case 'openSearch':
        await vscode.commands.executeCommand('workbench.view.search');
        break;
      case 'openSourceControl':
        await vscode.commands.executeCommand('workbench.view.scm');
        break;
      case 'openTerminal':
        await vscode.commands.executeCommand('workbench.action.terminal.toggleTerminal');
        break;
      case 'runTask':
        await vscode.commands.executeCommand('workbench.action.tasks.runTask');
        break;
      case 'openExtensions':
        await vscode.commands.executeCommand('workbench.view.extensions');
        break;
      case 'openChat':
        await vscode.commands.executeCommand('founderOs.openChat');
        break;
      case 'openConnections':
        await vscode.commands.executeCommand('founderOs.openSettings', 'connections');
        break;
      case 'openRemote':
        await vscode.commands.executeCommand('founderOs.openRemoteControl');
        break;
      case 'openSettings':
        await vscode.commands.executeCommand('founderOs.openSettings', 'account');
        break;
      case 'openNodeConfig':
        await vscode.commands.executeCommand('founderOs.openVaultConfig');
        break;
      case 'showUsage':
        await vscode.commands.executeCommand('founderOs.openSettings', 'account');
        break;
      case 'openProjectBrief':
        await vscode.commands.executeCommand('founderOs.openProjectBrief');
        break;
      case 'toggleAdvancedTools': {
        await vscode.commands.executeCommand('founderOs.toggleInterfaceMode');
        break;
      }
      case 'toggleCompanion': {
        const founder = vscode.workspace.getConfiguration('founderOs');
        const enabled = founder.get<boolean>('companion.enabled', true);
        await founder.update(
          'companion.enabled',
          !enabled,
          vscode.ConfigurationTarget.Global,
        );
        break;
      }
    }
    this.refresh();
  }

  async queueHousekeepingReview(
    candidates: unknown[],
  ): Promise<void> {
    this.goalState = enqueueFounderDecision(
      this.goalState,
      createFounderHousekeepingDecision(this.goalState, candidates),
    );
    await this.persistGoalState();
    this.refresh();
  }

  goalSnapshot(): FounderGoalUiState {
    return structuredClone(this.goalState);
  }

  private async persistGoalState(): Promise<void> {
    await this.context.workspaceState.update(
      'founder.goalState',
      this.goalState,
    );
  }

  private renderHtml(): string {
    const nonce = randomBytes(16).toString('hex');
    const config = vscode.workspace.getConfiguration('founderOs');
    const agentMode = normalizeFounderAgentMode(config.get<string>('agentMode'));
    const agentModeDefinition = founderAgentModeDefinition(agentMode);
    const companionEnabled = config.get<boolean>('companion.enabled', true);
    const interfaceMode = normalizeFounderInterfaceMode(config.get<string>('interfaceMode'));
    const workspaceLabel =
      vscode.workspace.workspaceFolders?.[0]?.name?.trim() || 'Open a project';
    const credentials = resolveCredentials();
    const vault = readVaultConfig();
    const connected = Boolean(credentials);
    const accountLabel = connected
      ? vault?.founderId?.trim() || 'Doxxed account'
      : 'Not signed in';
    const nodeLabel = connected
      ? vault?.label?.trim() || 'This computer'
      : 'Not connected';
    const connectionClass = connected ? 'online' : 'offline';
    const connectionLabel = connected ? 'Connected' : 'Sign in required';
    const agentLabel = this.agentAwareness.activeCount === 0
      ? 'No active tasks'
      : `${this.agentAwareness.activeCount} active${this.agentAwareness.conflictCount > 0 ? ` | ${this.agentAwareness.conflictCount} coordinating` : ''}`;
    const pendingDecisions = pendingFounderGoalDecisions(this.goalState);
    const decision = pendingDecisions[0];
    const goalStatus = this.goalState.status[0]!.toUpperCase()
      + this.goalState.status.slice(1);
    const decisionOptions = decision
      ? decision.options
        .filter((option): option is NonNullable<typeof option> => Boolean(option))
        .map((option) => `
          <button
            class="decision-option ${option.recommended ? 'recommended' : ''}"
            type="button"
            data-decision-id="${escapeHtml(decision.id)}"
            data-option-id="${escapeHtml(option.id)}"
          >
            <strong>${escapeHtml(option.label)}${option.recommended ? ' · Recommended' : ''}</strong>
            <span>${escapeHtml(option.description)}</span>
          </button>
        `)
        .join('')
      : '';
    const decisionEvidence = decision?.evidence.length
      ? `
        <ul class="decision-evidence">
          ${decision.evidence.slice(0, 3).map((item) =>
            `<li>${escapeHtml(item)}</li>`).join('')}
        </ul>
      `
      : '';
    const housekeepingCandidates = decision?.kind === 'housekeeping'
      ? (decision.housekeepingCandidates ?? []).map((candidate) => `
        <label class="housekeeping-candidate">
          <input
            type="checkbox"
            data-housekeeping-candidate="${escapeHtml(candidate.id)}"
            ${candidate.recommendedAction !== 'delete' ? 'disabled' : ''}
            ${candidate.recommendedAction === 'delete' && candidate.reversible ? 'checked' : ''}
          />
          <span class="housekeeping-copy">
            <strong>${escapeHtml(candidate.path)}</strong>
            <span>${escapeHtml(candidate.category.replaceAll('_', ' '))} | ${escapeHtml(formatFounderGoalBytes(candidate.sizeBytes))} | ${candidate.reversible ? 'restorable' : 'not automatically restorable'}</span>
            ${candidate.evidence.slice(0, 2).map((item) =>
              `<small>${escapeHtml(item)}</small>`).join('')}
          </span>
        </label>
      `).join('')
      : '';
    const agentRows = this.agentAwareness.tasks.map((task) => `
      <div class="agent-row ${task.conflict ? 'conflict' : ''}">
        <span class="agent-signal" aria-hidden="true"></span>
        <span class="agent-copy"><strong>${escapeHtml(task.title)}</strong><span>${escapeHtml(task.branch ?? 'No branch')} | ${escapeHtml(task.files.slice(0, 2).join(', ') || 'Reading workspace')}</span></span>
        <span class="agent-state">${task.conflict ? 'Coordinate' : escapeHtml(task.status[0]!.toUpperCase() + task.status.slice(1))}</span>
      </div>
    `).join('');

    const agentModeButtons = FOUNDER_AGENT_MODES.map(
      (candidate) => `
        <button
          class="mode-button ${candidate.id === agentMode ? 'selected' : ''}"
          type="button"
          data-agent-mode="${candidate.id}"
          aria-pressed="${candidate.id === agentMode}"
        >${escapeHtml(candidate.label)}</button>`,
    ).join('');
    const developerTools = interfaceMode === 'developer'
      ? `
        <section class="section">
          <details open>
            <summary>Developer tools <span class="summary-value">${escapeHtml(workspaceLabel)}</span></summary>
            <div class="tool-list">
              <button class="tool-item" type="button" data-action="openFiles"><strong>Files</strong><span>Workspace</span></button>
              <button class="tool-item" type="button" data-action="openSearch"><strong>Search</strong><span>Code and text</span></button>
              <button class="tool-item" type="button" data-action="openTerminal"><strong>Terminal</strong><span>Commands</span></button>
              <button class="tool-item" type="button" data-action="runTask"><strong>Run task</strong><span>Build or test</span></button>
              <button class="tool-item" type="button" data-action="openExtensions"><strong>Extensions</strong><span>Advanced</span></button>
              <button class="tool-item" type="button" data-action="toggleAdvancedTools"><strong>Founder mode</strong><span>Return to the focused AI workspace</span></button>
            </div>
          </details>
        </section>`
      : '';

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta
    http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';"
  >
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style nonce="${nonce}">
    :root {
      color-scheme: light dark;
      --founder-green: #35b779;
      --founder-amber: #e4a853;
      --foreground: var(--vscode-sideBar-foreground, var(--vscode-foreground, #f2f2f2));
      --surface: color-mix(in srgb, var(--vscode-sideBar-background) 88%, var(--foreground) 12%);
      --border: color-mix(in srgb, var(--foreground) 18%, transparent);
      --muted: color-mix(in srgb, var(--foreground) 62%, transparent);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      padding: 0;
      overflow-x: hidden;
      color: var(--foreground);
      background: var(--vscode-sideBar-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      letter-spacing: 0;
    }

    button {
      min-width: 0;
      color: var(--foreground);
      font: inherit;
      letter-spacing: 0;
    }

    button:focus-visible {
      outline: 1px solid var(--vscode-focusBorder);
      outline-offset: 2px;
    }

    .shell {
      display: flex;
      min-height: 100vh;
      flex-direction: column;
      padding: 8px 10px 10px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 0 7px;
    }

    .brand-mark {
      display: grid;
      width: 24px;
      height: 24px;
      place-items: center;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--surface);
      color: var(--founder-green);
      font-size: 14px;
      font-weight: 650;
    }

    h1, h2, p { margin: 0; }
    h1 { font-size: 14px; font-weight: 650; }
    h2 {
      margin-bottom: 9px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }

    .brand-subtitle, .mode-summary span, .status-detail {
      color: var(--muted);
      font-size: 11px;
    }
    .brand-subtitle { display: none; }

    .section {
      padding: 14px 0;
      border-top: 1px solid var(--border);
    }

    .navigation {
      display: grid;
      gap: 2px;
      padding-bottom: 10px;
    }

    .nav-item {
      display: grid;
      grid-template-columns: 26px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      width: 100%;
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 5px 7px;
      background: transparent;
      cursor: pointer;
      text-align: left;
    }

    .nav-item:hover {
      border-color: var(--border);
      background: var(--vscode-list-hoverBackground);
    }
    .nav-item.primary {
      margin-bottom: 7px;
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      font-weight: 600;
    }
    .nav-item.primary:hover { background: var(--vscode-button-hoverBackground); }
    .nav-icon {
      display: grid;
      width: 24px;
      height: 24px;
      place-items: center;
      border-radius: 6px;
      background: var(--surface);
      color: var(--muted);
      font-size: 11px;
      font-weight: 650;
    }
    .primary .nav-icon {
      background: color-mix(in srgb, currentColor 14%, transparent);
      color: currentColor;
    }
    .nav-copy { display: block; min-width: 0; }
    .nav-copy strong {
      display: block;
      overflow: hidden;
      font-size: 12px;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .nav-copy span {
      display: block;
      overflow: hidden;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.35;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .primary .nav-copy span { color: color-mix(in srgb, currentColor 72%, transparent); }
    .nav-arrow { color: var(--muted); font-size: 13px; }
    .primary .nav-arrow { color: currentColor; }

    .tool-list {
      display: grid;
      gap: 1px;
      padding: 2px 0 6px;
    }

    .tool-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      min-height: 31px;
      border: 0;
      border-radius: 5px;
      padding: 4px 7px;
      background: transparent;
      cursor: pointer;
      text-align: left;
      color: var(--foreground);
    }

    .tool-item:hover { background: var(--vscode-list-hoverBackground); }
    .tool-item span { color: var(--muted); font-size: 10px; }

    details { width: 100%; }
    summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 30px;
      cursor: pointer;
      font-size: 11px;
      font-weight: 600;
      list-style: none;
      color: var(--foreground);
    }
    summary::-webkit-details-marker { display: none; }
    .summary-value { color: var(--muted); font-weight: 400; }

    .mode-switch {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 3px;
      padding: 3px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--surface);
    }

    .mode-button {
      min-height: 28px;
      overflow: hidden;
      border: 0;
      border-radius: 5px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .mode-button.selected {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      font-weight: 600;
    }

    .mode-summary {
      display: grid;
      gap: 3px;
      padding-top: 10px;
    }

    .mode-summary strong { font-size: 12px; font-weight: 600; }

    .status-row {
      display: grid;
      grid-template-columns: 8px minmax(0, 1fr) auto;
      align-items: center;
      gap: 9px;
      min-height: 38px;
    }

    .status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--founder-amber);
    }

    .status-dot.online { background: var(--founder-green); }

    .status-copy {
      display: grid;
      min-width: 0;
      gap: 2px;
    }

    .status-copy strong {
      overflow: hidden;
      font-size: 12px;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .text-button {
      border: 0;
      background: transparent;
      color: var(--vscode-textLink-foreground);
      cursor: pointer;
      font-size: 11px;
    }

    .actions {
      display: grid;
      gap: 7px;
      padding-top: 4px;
    }

    .command {
      min-height: 32px;
      border: 1px solid var(--vscode-button-border, transparent);
      border-radius: 6px;
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      cursor: pointer;
      font-weight: 600;
    }

    .command:hover { background: var(--vscode-button-secondaryHoverBackground); }

    .command.primary {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }

    .command.primary:hover { background: var(--vscode-button-hoverBackground); }

    .service-line {
      overflow-wrap: anywhere;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.55;
    }

    .agent-awareness {
      padding: 3px 0 10px;
      border-bottom: 1px solid var(--border);
    }
    .agent-mode {
      display: grid;
      gap: 7px;
      padding: 4px 0 11px;
      border-bottom: 1px solid var(--border);
    }
    .agent-mode-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
      font-size: 10px;
    }
    .agent-mode-head strong { color: var(--foreground); font-size: 11px; }
    .agent-mode .mode-switch { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .agent-mode-copy { color: var(--muted); font-size: 10px; line-height: 1.4; }
    .agent-awareness header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 28px;
      color: var(--muted);
      font-size: 10px;
      text-transform: uppercase;
    }
    .agent-awareness header strong { color: var(--foreground); font-size: 11px; }
    .agent-list { display: grid; gap: 1px; }
    .agent-row {
      display: grid;
      grid-template-columns: 8px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 4px 2px;
    }
    .agent-signal { width: 7px; height: 7px; border-radius: 50%; background: var(--founder-green); }
    .agent-row.conflict .agent-signal { background: var(--founder-amber); }
    .agent-copy { min-width: 0; }
    .agent-copy strong, .agent-copy span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .agent-copy strong { font-size: 11px; font-weight: 600; }
    .agent-copy span, .agent-state { color: var(--muted); font-size: 9px; }
    .agent-row.conflict .agent-state { color: var(--founder-amber); }

    .goal-panel {
      display: grid;
      gap: 7px;
      padding: 12px 2px;
      border-top: 1px solid var(--border);
    }
    .goal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .goal-label {
      color: var(--muted);
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .goal-meta {
      color: var(--muted);
      font-size: 9px;
    }
    .goal-objective {
      overflow-wrap: anywhere;
      font-size: 12px;
      font-weight: 600;
      line-height: 1.45;
    }
    .goal-edit {
      border: 0;
      background: transparent;
      color: var(--vscode-textLink-foreground);
      cursor: pointer;
      font-size: 10px;
    }
    .decision-panel {
      display: grid;
      gap: 7px;
      padding: 12px 2px;
      border-top: 1px solid var(--border);
    }
    .decision-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-size: 11px;
    }
    .decision-count {
      color: var(--founder-amber);
      font-size: 9px;
    }
    .decision-question {
      color: var(--muted);
      font-size: 10px;
      line-height: 1.45;
    }
    .decision-context {
      display: flex;
      gap: 6px;
      align-items: center;
      color: var(--muted);
      font-size: 9px;
    }
    .decision-risk {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 5px;
    }
    .decision-evidence {
      display: grid;
      gap: 3px;
      margin: 0;
      padding-left: 16px;
      color: var(--muted);
      font-size: 9px;
      line-height: 1.35;
    }
    .housekeeping-list {
      display: grid;
      gap: 5px;
      max-height: 190px;
      overflow: auto;
      padding-right: 2px;
    }
    .housekeeping-candidate {
      display: grid;
      grid-template-columns: 16px minmax(0, 1fr);
      gap: 6px;
      align-items: start;
      padding: 7px;
      border: 1px solid var(--border);
      border-radius: 6px;
      cursor: pointer;
    }
    .housekeeping-candidate input {
      margin: 2px 0 0;
    }
    .housekeeping-copy {
      display: grid;
      gap: 2px;
      min-width: 0;
      font-size: 9px;
      line-height: 1.35;
    }
    .housekeeping-copy strong {
      overflow-wrap: anywhere;
    }
    .housekeeping-copy span,
    .housekeeping-copy small {
      color: var(--muted);
    }
    .decision-options {
      display: grid;
      gap: 4px;
    }
    .decision-option {
      display: grid;
      gap: 2px;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px;
      background: transparent;
      cursor: pointer;
      text-align: left;
    }
    .decision-option:hover {
      background: var(--vscode-list-hoverBackground);
    }
    .decision-option.recommended {
      border-color: color-mix(in srgb, var(--founder-green) 55%, var(--border));
    }
    .decision-option strong { font-size: 10px; }
    .decision-option span { color: var(--muted); font-size: 9px; line-height: 1.35; }
    .decision-footnote { color: var(--muted); font-size: 9px; line-height: 1.4; }

    .account-footer {
      margin-top: auto;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }
    .account-main {
      display: grid;
      grid-template-columns: 8px minmax(0, 1fr) auto;
      align-items: center;
      gap: 9px;
    }
    .account-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 5px 12px;
      padding-top: 10px;
    }
    .account-actions .text-button { padding: 0; }

    @media (max-width: 180px) {
      .nav-copy span,
      .tool-item span,
      .summary-value {
        display: none;
      }

      .nav-item {
        min-height: 34px;
      }

      .tool-item {
        grid-template-columns: minmax(0, 1fr);
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <nav class="navigation" aria-label="Founder navigation">
      <button class="nav-item primary" type="button" data-action="newChat">
        <span class="nav-icon" aria-hidden="true">+</span>
        <span class="nav-copy"><strong>New chat</strong><span>Start with Founder AI</span></span>
      </button>
      <button class="nav-item" type="button" data-action="openProjects">
        <span class="nav-icon" aria-hidden="true">P</span>
        <span class="nav-copy"><strong>Projects</strong><span>${escapeHtml(workspaceLabel)}</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openChats">
        <span class="nav-icon" aria-hidden="true">C</span>
        <span class="nav-copy"><strong>Chats</strong><span>Continue recent chats</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openAgents">
        <span class="nav-icon" aria-hidden="true">A</span>
        <span class="nav-copy"><strong>Agents</strong><span>${escapeHtml(agentLabel)}</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openPreview">
        <span class="nav-icon" aria-hidden="true">B</span>
        <span class="nav-copy"><strong>Browser</strong><span>Preview what Founder AI is building</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openSourceControl">
        <span class="nav-icon" aria-hidden="true">C</span>
        <span class="nav-copy"><strong>Changes</strong><span>Visual graph and AI review</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openDeploy">
        <span class="nav-icon" aria-hidden="true">D</span>
        <span class="nav-copy"><strong>Deploy</strong><span>Ship through connected services</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openRemote">
        <span class="nav-icon" aria-hidden="true">R</span>
        <span class="nav-copy"><strong>Remote</strong><span>${connected ? 'This computer is connected' : 'Pair this computer'}</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
      <button class="nav-item" type="button" data-action="openConnections">
        <span class="nav-icon" aria-hidden="true">C</span>
        <span class="nav-copy"><strong>Connect</strong><span>Services and infrastructure</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
    </nav>

    <section class="goal-panel" aria-label="Pursuing goal">
      <div class="goal-head">
        <span class="goal-label">Pursuing goal</span>
        <button class="goal-edit" type="button" data-edit-goal>Edit</button>
      </div>
      <p class="goal-objective">${escapeHtml(this.goalState.objective)}</p>
      <span class="goal-meta">${escapeHtml(goalStatus)} · Version ${this.goalState.version}</span>
    </section>

    ${decision ? `
      <section class="decision-panel" aria-label="Needs your decision">
        <div class="decision-title">
          <strong>Needs your decision</strong>
          <span class="decision-count">${pendingDecisions.length} waiting</span>
        </div>
        <div class="decision-context">
          <strong>${escapeHtml(decision.title)}</strong>
          <span class="decision-risk">${escapeHtml(decision.risk.replaceAll('_', ' '))}</span>
        </div>
        <p class="decision-question">${escapeHtml(decision.question)}</p>
        ${decisionEvidence}
        ${housekeepingCandidates ? `
          <div class="housekeeping-list" aria-label="Housekeeping candidates">
            ${housekeepingCandidates}
          </div>
        ` : ''}
        <div class="decision-options">${decisionOptions}</div>
        ${decision.allowCustomAnswer ? `
          <button
            class="decision-option"
            type="button"
            data-decision-id="${escapeHtml(decision.id)}"
            data-option-id="__custom__"
          ><strong>Write another answer</strong><span>Give Founder different instructions.</span></button>
        ` : ''}
        <p class="decision-footnote">${
          decision.independentWorkMayContinue
            ? 'Independent work continues while this waits.'
            : 'Dependent work is paused at a safe boundary.'
        }</p>
      </section>
    ` : ''}

    <section class="agent-mode" aria-label="Agent mode">
      <div class="agent-mode-head"><strong>Agent mode</strong><span>${escapeHtml(agentModeDefinition.label)}</span></div>
      <div class="mode-switch" role="group" aria-label="Founder agent mode">${agentModeButtons}</div>
      <p class="agent-mode-copy">${escapeHtml(agentModeDefinition.summary)}</p>
    </section>

    ${this.agentAwareness.activeCount > 0 ? `
      <section class="agent-awareness" aria-label="Active agent work">
        <header><strong>Active work</strong><span>${escapeHtml(agentLabel)}</span></header>
        <div class="agent-list">${agentRows}</div>
      </section>
    ` : ''}

    ${developerTools}

    <footer class="account-footer">
      <div class="account-main">
        <span class="status-dot ${connectionClass}" aria-hidden="true"></span>
        <div class="status-copy">
          <strong>${escapeHtml(accountLabel)}</strong>
          <span class="status-detail">${escapeHtml(connectionLabel)} | ${escapeHtml(nodeLabel)}</span>
        </div>
        <button class="text-button" type="button" data-action="${connected ? 'signOut' : 'signIn'}">${connected ? 'Sign out' : 'Sign in'}</button>
      </div>
      <div class="account-actions">
        <button class="text-button" type="button" data-action="showUsage">Usage</button>
        <button class="text-button" type="button" data-action="toggleCompanion">${companionEnabled ? 'Hide Dragon' : 'Show Dragon'}</button>
        <button class="text-button" type="button" data-action="openSettings">Settings</button>
        ${interfaceMode === 'founder' ? '<button class="text-button" type="button" data-action="toggleAdvancedTools">Developer mode</button>' : ''}
      </div>
    </footer>
  </main>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    for (const button of document.querySelectorAll('[data-action]')) {
      button.addEventListener('click', () => {
        vscode.postMessage({ type: 'action', action: button.dataset.action });
      });
    }
    for (const button of document.querySelectorAll('[data-agent-mode]')) {
      button.addEventListener('click', () => {
        vscode.postMessage({ type: 'selectAgentMode', agentMode: button.dataset.agentMode });
      });
    }
    const editGoal = document.querySelector('[data-edit-goal]');
    editGoal?.addEventListener('click', () => {
      vscode.postMessage({ type: 'editGoal' });
    });
    for (const button of document.querySelectorAll('[data-decision-id]')) {
      button.addEventListener('click', () => {
        const selectedCandidateIds = Array.from(
          document.querySelectorAll('[data-housekeeping-candidate]:checked'),
        ).map((input) => input.dataset.housekeepingCandidate);
        vscode.postMessage({
          type: 'resolveDecision',
          decisionId: button.dataset.decisionId,
          optionId: button.dataset.optionId,
          selectedCandidateIds,
        });
      });
    }
  </script>
</body>
</html>`;
  }
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
