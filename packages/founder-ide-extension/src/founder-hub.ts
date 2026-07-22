import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import { readVaultConfig, resolveCredentials } from './credentials';
import {
  FOUNDER_WORKSPACE_MODES,
  normalizeWorkspaceMode,
  workspaceModeDefinition,
  type FounderWorkspaceMode,
} from './founder-hub-state';
import type { FounderAgentAwarenessSummary } from './agent-awareness';

type FounderHubAction =
  | 'signIn'
  | 'signOut'
  | 'manageConnection'
  | 'newChat'
  | 'openProjects'
  | 'openChats'
  | 'openAgents'
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
  | 'toggleAdvancedTools'
  | 'toggleCompanion';

interface FounderHubMessage {
  type: 'action' | 'selectMode';
  action?: FounderHubAction;
  mode?: FounderWorkspaceMode;
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

  constructor(private readonly context: vscode.ExtensionContext) {}

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

  dispose(): void {
    for (const disposable of this.disposables) disposable.dispose();
    this.disposables.length = 0;
  }

  private async handleMessage(message: FounderHubMessage): Promise<void> {
    if (message.type === 'selectMode' && message.mode) {
      const mode = normalizeWorkspaceMode(message.mode);
      await vscode.workspace
        .getConfiguration('founderOs')
        .update('workspaceMode', mode, vscode.ConfigurationTarget.Global);
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
        await vscode.commands.executeCommand('workbench.action.openRecent');
        break;
      case 'openChats':
        await vscode.commands.executeCommand('founderOs.openChat');
        break;
      case 'openAgents':
        await vscode.commands.executeCommand('founderOs.openAgents');
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
        await vscode.commands.executeCommand('founderOs.openConnections');
        break;
      case 'openRemote':
        await vscode.commands.executeCommand('founderOs.openRemoteControl');
        break;
      case 'openSettings':
        await vscode.commands.executeCommand('founderOs.openSettings');
        break;
      case 'openNodeConfig':
        await vscode.commands.executeCommand('founderOs.openVaultConfig');
        break;
      case 'showUsage':
        await vscode.commands.executeCommand('founderOs.openSettings');
        break;
      case 'toggleAdvancedTools': {
        const workbench = vscode.workspace.getConfiguration('workbench');
        const location = workbench.get<string>('activityBar.location', 'default');
        const show = location === 'hidden';
        await workbench.update(
          'activityBar.location',
          show ? 'default' : 'hidden',
          vscode.ConfigurationTarget.Global,
        );
        await vscode.workspace
          .getConfiguration('founderOs')
          .update('advancedIdeTools', show, vscode.ConfigurationTarget.Global);
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

  private renderHtml(): string {
    const nonce = randomBytes(16).toString('hex');
    const config = vscode.workspace.getConfiguration('founderOs');
    const mode = normalizeWorkspaceMode(config.get<string>('workspaceMode'));
    const modeDefinition = workspaceModeDefinition(mode);
    const companionEnabled = config.get<boolean>('companion.enabled', true);
    const advancedToolsVisible =
      vscode.workspace
        .getConfiguration('workbench')
        .get<string>('activityBar.location', 'default') !== 'hidden';
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
    const agentRows = this.agentAwareness.tasks.map((task) => `
      <div class="agent-row ${task.conflict ? 'conflict' : ''}">
        <span class="agent-signal" aria-hidden="true"></span>
        <span class="agent-copy"><strong>${escapeHtml(task.title)}</strong><span>${escapeHtml(task.branch ?? 'No branch')} | ${escapeHtml(task.files.slice(0, 2).join(', ') || 'Reading workspace')}</span></span>
        <span class="agent-state">${task.conflict ? 'Coordinate' : escapeHtml(task.status[0]!.toUpperCase() + task.status.slice(1))}</span>
      </div>
    `).join('');

    const modeButtons = FOUNDER_WORKSPACE_MODES.map(
      (candidate) => `
        <button
          class="mode-button ${candidate.id === mode ? 'selected' : ''}"
          type="button"
          data-mode="${candidate.id}"
          aria-pressed="${candidate.id === mode}"
        >${escapeHtml(candidate.label)}</button>`,
    ).join('');

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
      <button class="nav-item" type="button" data-action="openSourceControl">
        <span class="nav-icon" aria-hidden="true">G</span>
        <span class="nav-copy"><strong>Graph</strong><span>Changes, commits, and history</span></span>
        <span class="nav-arrow" aria-hidden="true">&gt;</span>
      </button>
    </nav>

    ${this.agentAwareness.activeCount > 0 ? `
      <section class="agent-awareness" aria-label="Active agent work">
        <header><strong>Active work</strong><span>${escapeHtml(agentLabel)}</span></header>
        <div class="agent-list">${agentRows}</div>
      </section>
    ` : ''}

    <section class="section">
      <details open>
        <summary>Build and ship <span class="summary-value">${escapeHtml(workspaceLabel)}</span></summary>
        <div class="tool-list">
          <button class="tool-item" type="button" data-action="openFiles"><strong>Files</strong><span>Workspace</span></button>
          <button class="tool-item" type="button" data-action="openSearch"><strong>Search</strong><span>Code and text</span></button>
          <button class="tool-item" type="button" data-action="openSourceControl"><strong>Changes</strong><span>Review and commit</span></button>
          <button class="tool-item" type="button" data-action="openTerminal"><strong>Terminal</strong><span>Commands</span></button>
          <button class="tool-item" type="button" data-action="runTask"><strong>Run task</strong><span>Build or test</span></button>
          <button class="tool-item" type="button" data-action="openExtensions"><strong>Extensions</strong><span>Advanced</span></button>
          <button class="tool-item" type="button" data-action="toggleAdvancedTools"><strong>${advancedToolsVisible ? 'Hide' : 'Show'} IDE rail</strong><span>Advanced tools</span></button>
        </div>
      </details>
    </section>

    <section class="section">
      <details>
        <summary>Connect <span class="summary-value">${escapeHtml(connectionLabel)}</span></summary>
        <div class="tool-list">
          <button class="tool-item" type="button" data-action="manageConnection"><strong>Founder Node</strong><span>${escapeHtml(nodeLabel)}</span></button>
          <button class="tool-item" type="button" data-action="openConnections"><strong>Services</strong><span>GitHub, Vercel, Railway, Neon</span></button>
          <button class="tool-item" type="button" data-action="openRemote"><strong>Remote control</strong><span>This computer</span></button>
        </div>
      </details>
    </section>

    <section class="section">
      <details>
        <summary>Infrastructure <span class="summary-value">${escapeHtml(modeDefinition.label)}</span></summary>
        <div class="mode-switch" role="group" aria-label="Infrastructure mode">${modeButtons}</div>
        <div class="mode-summary">
          <strong>${escapeHtml(modeDefinition.summary)}</strong>
          <span>${escapeHtml(modeDefinition.services)}</span>
        </div>
      </details>
    </section>

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
    for (const button of document.querySelectorAll('[data-mode]')) {
      button.addEventListener('click', () => {
        vscode.postMessage({ type: 'selectMode', mode: button.dataset.mode });
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
