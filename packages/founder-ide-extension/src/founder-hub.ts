import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import { readVaultConfig, resolveCredentials } from './credentials';
import {
  FOUNDER_WORKSPACE_MODES,
  normalizeWorkspaceMode,
  workspaceModeDefinition,
  type FounderWorkspaceMode,
} from './founder-hub-state';

type FounderHubAction =
  | 'signIn'
  | 'signOut'
  | 'manageConnection'
  | 'openChat'
  | 'openConnections'
  | 'openSettings'
  | 'openNodeConfig';

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
      case 'openChat':
        await vscode.commands.executeCommand('founderOs.openChat');
        break;
      case 'openConnections':
        await vscode.commands.executeCommand('founderOs.openConnections');
        break;
      case 'openSettings':
        await vscode.commands.executeCommand('founderOs.openSettings');
        break;
      case 'openNodeConfig':
        await vscode.commands.executeCommand('founderOs.openVaultConfig');
        break;
    }
    this.refresh();
  }

  private renderHtml(): string {
    const nonce = randomBytes(16).toString('hex');
    const config = vscode.workspace.getConfiguration('founderOs');
    const mode = normalizeWorkspaceMode(config.get<string>('workspaceMode'));
    const modeDefinition = workspaceModeDefinition(mode);
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
      --surface: color-mix(in srgb, var(--vscode-sideBar-background) 88%, var(--vscode-foreground) 12%);
      --border: color-mix(in srgb, var(--vscode-sideBar-foreground) 18%, transparent);
      --muted: color-mix(in srgb, var(--vscode-sideBar-foreground) 62%, transparent);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--vscode-sideBar-foreground);
      background: var(--vscode-sideBar-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      letter-spacing: 0;
    }

    button {
      min-width: 0;
      color: inherit;
      font: inherit;
      letter-spacing: 0;
    }

    button:focus-visible {
      outline: 1px solid var(--vscode-focusBorder);
      outline-offset: 2px;
    }

    .shell { padding: 12px 14px 20px; }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 0 15px;
    }

    .brand-mark {
      display: grid;
      width: 30px;
      height: 30px;
      place-items: center;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--surface);
      color: var(--founder-green);
      font-size: 17px;
      font-weight: 650;
    }

    h1, h2, p { margin: 0; }
    h1 { font-size: 15px; font-weight: 650; }
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

    .section {
      padding: 14px 0;
      border-top: 1px solid var(--border);
    }

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
  </style>
</head>
<body>
  <main class="shell">
    <header class="brand">
      <div class="brand-mark" aria-hidden="true">F</div>
      <div>
        <h1>Founder</h1>
        <p class="brand-subtitle">Workspace control</p>
      </div>
    </header>

    <section class="section">
      <h2>Infrastructure</h2>
      <div class="mode-switch" role="group" aria-label="Infrastructure mode">
        ${modeButtons}
      </div>
      <div class="mode-summary">
        <strong>${escapeHtml(modeDefinition.summary)}</strong>
        <span>${escapeHtml(modeDefinition.services)}</span>
      </div>
    </section>

    <section class="section">
      <h2>Identity and Node</h2>
      <div class="status-row">
        <span class="status-dot ${connectionClass}" aria-hidden="true"></span>
        <div class="status-copy">
          <strong>${escapeHtml(accountLabel)}</strong>
          <span class="status-detail">${escapeHtml(connectionLabel)}</span>
        </div>
        <button class="text-button" type="button" data-action="${connected ? 'signOut' : 'signIn'}">
          ${connected ? 'Sign out' : 'Sign in'}
        </button>
      </div>
      <div class="status-row">
        <span class="status-dot ${connectionClass}" aria-hidden="true"></span>
        <div class="status-copy">
          <strong>Founder Node</strong>
          <span class="status-detail">${escapeHtml(nodeLabel)}</span>
        </div>
        <button class="text-button" type="button" data-action="manageConnection">Manage</button>
      </div>
    </section>

    <section class="section">
      <h2>Workspace</h2>
      <div class="actions">
        <button class="command primary" type="button" data-action="openChat">Open Founder Chat</button>
        <button class="command" type="button" data-action="openConnections">Connections</button>
        <button class="command" type="button" data-action="openSettings">Founder Settings</button>
      </div>
    </section>

    <section class="section">
      <h2>Available services</h2>
      <p class="service-line">${escapeHtml(modeDefinition.services)}</p>
    </section>
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
