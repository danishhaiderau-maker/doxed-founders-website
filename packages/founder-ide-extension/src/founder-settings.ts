import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import { readVaultConfig, resolveCredentials } from './credentials';
import {
  EXECUTION_PROFILES,
  type ExecutionProfile,
  type ExecutionProfileId,
} from './profile-manager';
import {
  FOUNDER_WORKSPACE_MODES,
  normalizeWorkspaceMode,
  workspaceModeDefinition,
  type FounderWorkspaceMode,
} from './founder-hub-state';

type FounderSettingsAction =
  | 'openChat'
  | 'openConnections'
  | 'selectModel'
  | 'openAdvancedSettings'
  | 'openNodeConfig'
  | 'manageConnection'
  | 'signIn'
  | 'signOut';

interface FounderSettingsMessage {
  type: 'action' | 'selectMode' | 'selectProfile';
  action?: FounderSettingsAction;
  mode?: FounderWorkspaceMode;
  profile?: ExecutionProfileId;
}

export interface FounderSettingsDependencies {
  getProfile(): ExecutionProfile;
  setProfile(id: ExecutionProfileId): Promise<void>;
}

export class FounderSettingsPanel implements vscode.Disposable {
  static readonly viewType = 'founderOs.settings';

  private panel: vscode.WebviewPanel | undefined;
  private panelDisposables: vscode.Disposable[] = [];

  constructor(private readonly dependencies: FounderSettingsDependencies) {}

  show(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Active);
      this.refresh();
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      FounderSettingsPanel.viewType,
      'Founder Settings',
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [],
      },
    );
    this.panel = panel;
    this.panelDisposables.push(
      panel.onDidDispose(() => this.disposePanel()),
      panel.webview.onDidReceiveMessage((message: FounderSettingsMessage) =>
        this.handleMessage(message),
      ),
    );
    this.refresh();
  }

  refresh(): void {
    if (!this.panel) return;
    this.panel.webview.html = this.renderHtml();
  }

  dispose(): void {
    this.panel?.dispose();
    this.disposePanel();
  }

  private disposePanel(): void {
    this.panel = undefined;
    for (const disposable of this.panelDisposables) disposable.dispose();
    this.panelDisposables = [];
  }

  private async handleMessage(message: FounderSettingsMessage): Promise<void> {
    if (message.type === 'selectMode' && message.mode) {
      const mode = normalizeWorkspaceMode(message.mode);
      await vscode.workspace
        .getConfiguration('founderOs')
        .update('workspaceMode', mode, vscode.ConfigurationTarget.Global);
      this.refresh();
      return;
    }

    if (message.type === 'selectProfile' && message.profile) {
      await this.dependencies.setProfile(message.profile);
      this.refresh();
      return;
    }

    if (message.type !== 'action' || !message.action) return;
    switch (message.action) {
      case 'openChat':
        await vscode.commands.executeCommand('founderOs.openChat');
        break;
      case 'openConnections':
        await vscode.commands.executeCommand('founderOs.openConnections');
        break;
      case 'selectModel':
        await vscode.commands.executeCommand('founderOs.selectModel');
        break;
      case 'openAdvancedSettings':
        await vscode.commands.executeCommand(
          'workbench.action.openSettings',
          '@ext:doxxedcrypto.founder-ide-extension',
        );
        break;
      case 'openNodeConfig':
        await vscode.commands.executeCommand('founderOs.openVaultConfig');
        break;
      case 'manageConnection':
        await vscode.commands.executeCommand('founderOs.manage');
        break;
      case 'signIn':
        await vscode.commands.executeCommand('founderOs.signIn');
        break;
      case 'signOut':
        await vscode.commands.executeCommand('founderOs.signOut');
        break;
    }
    this.refresh();
  }

  private renderHtml(): string {
    const nonce = randomBytes(16).toString('hex');
    const config = vscode.workspace.getConfiguration('founderOs');
    const mode = normalizeWorkspaceMode(config.get<string>('workspaceMode'));
    const modeDefinition = workspaceModeDefinition(mode);
    const profile = this.dependencies.getProfile();
    const credentials = resolveCredentials();
    const vault = readVaultConfig();
    const connected = Boolean(credentials);
    const accountLabel = connected
      ? vault?.founderId?.trim() || 'Doxxed account'
      : 'Not signed in';
    const nodeLabel = connected
      ? vault?.label?.trim() || 'This computer'
      : 'Not connected';

    const modeButtons = FOUNDER_WORKSPACE_MODES.map(
      (candidate) => `
        <button class="segment ${candidate.id === mode ? 'selected' : ''}" type="button"
          data-mode="${candidate.id}" aria-pressed="${candidate.id === mode}">
          ${escapeHtml(candidate.label)}
        </button>`,
    ).join('');

    const profileButtons = EXECUTION_PROFILES.map(
      (candidate) => `
        <button class="choice ${candidate.id === profile.id ? 'selected' : ''}" type="button"
          data-profile="${candidate.id}" aria-pressed="${candidate.id === profile.id}">
          <span class="choice-title">${escapeHtml(candidate.label)}</span>
          <span>${escapeHtml(profileSummary(candidate.id))}</span>
        </button>`,
    ).join('');

    const connectionRows = [
      ['GitHub', 'Source, pull requests and reviews'],
      ['Vercel', 'Web previews and production deploys'],
      ['Railway', 'Services, workers and live logs'],
      ['Neon', 'Postgres databases and migrations'],
    ]
      .map(
        ([name, detail]) => `
          <div class="connection-row">
            <div><strong>${name}</strong><span>${detail}</span></div>
            <button class="link-button" type="button" data-action="openConnections">Manage</button>
          </div>`,
      )
      .join('');

    const providerRows = [
      ['OpenAI', 'GPT and o-series models'],
      ['Anthropic', 'Claude models'],
      ['Google', 'Gemini models'],
      ['DeepSeek', 'Chat and reasoning models'],
      ['OpenRouter', 'One connection for many providers'],
      ['Ollama', 'Models running on this computer'],
    ]
      .map(
        ([name, detail]) => `
          <div class="connection-row">
            <div><strong>${name}</strong><span>${detail}</span></div>
            <button class="link-button" type="button" data-action="openConnections">Connect</button>
          </div>`,
      )
      .join('');

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style nonce="${nonce}">
    :root {
      color-scheme: light dark;
      --accent: #2f80ed;
      --positive: #35b779;
      --warning: #e4a853;
      --border: color-mix(in srgb, var(--vscode-foreground) 16%, transparent);
      --muted: color-mix(in srgb, var(--vscode-foreground) 62%, transparent);
      --surface: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-foreground) 8%);
      --surface-hover: color-mix(in srgb, var(--vscode-editor-background) 86%, var(--vscode-foreground) 14%);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      letter-spacing: 0;
    }
    button { color: inherit; font: inherit; letter-spacing: 0; }
    button:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 2px; }
    .page { width: min(860px, calc(100% - 40px)); margin: 0 auto; padding: 30px 0 64px; }
    .header { display: grid; gap: 18px; padding-bottom: 24px; }
    .identity { display: flex; align-items: center; gap: 13px; min-width: 0; }
    .mark {
      display: grid; width: 38px; height: 38px; place-items: center;
      border: 1px solid var(--border); border-radius: 8px; background: var(--surface);
      color: var(--positive); font-size: 20px; font-weight: 650;
    }
    h1, h2, p { margin: 0; }
    h1 { font-size: 22px; font-weight: 650; }
    .subtitle, .section-copy, .choice span, .connection-row span, .note { color: var(--muted); }
    .subtitle { padding-top: 4px; font-size: 12px; }
    .tabs {
      display: flex; gap: 3px; padding: 3px; border: 1px solid var(--border);
      width: 100%; border-radius: 7px; background: var(--surface); overflow: hidden;
    }
    .tab, .segment {
      min-height: 30px; border: 0; border-radius: 5px; background: transparent;
      flex: 1 1 0; min-width: 0; color: var(--muted); cursor: pointer;
      padding: 0 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .tab.selected, .segment.selected { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .panel { display: none; }
    .panel.selected { display: block; animation: panel-in 160ms ease-out; }
    .section { padding: 28px 0; border-top: 1px solid var(--border); }
    .section:first-child { border-top: 0; }
    h2 { font-size: 15px; font-weight: 650; }
    .section-copy { padding-top: 6px; max-width: 620px; line-height: 1.55; }
    .status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding-top: 18px; }
    .status-item, .connection-row {
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      min-width: 0; padding: 14px 16px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface);
    }
    .status-copy, .connection-row > div { display: grid; min-width: 0; gap: 4px; }
    .status-copy strong, .connection-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .status-copy span, .connection-row span { font-size: 11px; line-height: 1.4; }
    .dot { width: 8px; height: 8px; flex: 0 0 auto; border-radius: 50%; background: var(--warning); }
    .dot.online { background: var(--positive); }
    .status-lead { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .segments { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3px; max-width: 520px; margin-top: 18px; padding: 3px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); }
    .mode-detail { display: grid; gap: 4px; padding-top: 14px; }
    .mode-detail span { color: var(--muted); }
    .choices { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding-top: 18px; }
    .choice { display: grid; min-height: 82px; align-content: center; gap: 6px; padding: 14px 16px; text-align: left; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); cursor: pointer; }
    .choice:hover, .connection-row:hover { background: var(--surface-hover); }
    .choice.selected { border-color: var(--vscode-focusBorder); box-shadow: inset 3px 0 0 var(--accent); }
    .choice-title { color: var(--vscode-editor-foreground) !important; font-weight: 650; }
    .connections { display: grid; gap: 8px; padding-top: 18px; }
    .button-row { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 18px; }
    .primary, .secondary, .link-button {
      min-height: 32px; border-radius: 6px; cursor: pointer; padding: 0 13px;
    }
    .primary { border: 1px solid var(--vscode-button-border, transparent); background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .primary:hover { background: var(--vscode-button-hoverBackground); }
    .secondary { border: 1px solid var(--border); background: var(--surface); }
    .secondary:hover { background: var(--surface-hover); }
    .link-button { border: 0; background: transparent; color: var(--vscode-textLink-foreground); }
    .note { padding-top: 14px; font-size: 11px; line-height: 1.55; }
    .managed-row {
      display: flex; align-items: center; justify-content: space-between; gap: 20px;
      margin-top: 18px; padding: 16px; border: 1px solid var(--border); border-radius: 7px;
      background: var(--surface);
    }
    .managed-row > div { display: grid; gap: 4px; }
    .managed-row span { color: var(--muted); font-size: 11px; line-height: 1.45; }
    @keyframes panel-in {
      from { opacity: 0; transform: translateY(3px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (prefers-reduced-motion: reduce) {
      .panel.selected { animation: none; }
    }
    @media (max-width: 680px) {
      .page { width: min(100% - 28px, 860px); padding-top: 24px; }
      .status-grid, .choices { grid-template-columns: 1fr; }
      .tabs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .tab { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header class="header">
      <div class="identity">
        <div class="mark" aria-hidden="true">F</div>
        <div><h1>Founder Settings</h1><p class="subtitle">One workspace. Local when you want it, connected when you need it.</p></div>
      </div>
      <nav class="tabs" aria-label="Founder Settings sections">
        <button class="tab selected" type="button" data-tab="account">Account</button>
        <button class="tab" type="button" data-tab="ai">AI</button>
        <button class="tab" type="button" data-tab="infrastructure">Local &amp; Cloud</button>
        <button class="tab" type="button" data-tab="connections">Connections</button>
        <button class="tab" type="button" data-tab="advanced">Advanced</button>
      </nav>
    </header>

    <div class="panel selected" data-panel="account">
      <section class="section">
        <h2>Identity and Node</h2>
        <p class="section-copy">Your Doxxed account and the background Founder Node share one secure connection.</p>
        <div class="status-grid">
          <div class="status-item">
            <div class="status-lead"><span class="dot ${connected ? 'online' : ''}"></span><div class="status-copy"><strong>${escapeHtml(accountLabel)}</strong><span>${connected ? 'Account connected' : 'Sign in to connect this workspace'}</span></div></div>
            <button class="link-button" type="button" data-action="${connected ? 'signOut' : 'signIn'}">${connected ? 'Sign out' : 'Sign in'}</button>
          </div>
          <div class="status-item">
            <div class="status-lead"><span class="dot ${connected ? 'online' : ''}"></span><div class="status-copy"><strong>Founder Node</strong><span>${escapeHtml(nodeLabel)}</span></div></div>
            <button class="link-button" type="button" data-action="manageConnection">Manage</button>
          </div>
        </div>
      </section>
      <section class="section">
        <h2>Workspace</h2>
        <p class="section-copy">Open Founder Chat or continue configuring the services used by this workspace.</p>
        <div class="button-row"><button class="primary" type="button" data-action="openChat">Open Founder Chat</button><button class="secondary" type="button" data-action="openConnections">Manage connections</button></div>
      </section>
    </div>

    <div class="panel" data-panel="ai">
      <section class="section">
        <h2>Founder AI</h2>
        <p class="section-copy">Managed routing is the default and stays available when no personal provider is selected.</p>
        <div class="managed-row">
          <div><strong>Founder managed</strong><span>${escapeHtml(profile.label)} &middot; ${escapeHtml(profile.aliasId)}</span></div>
          <button class="secondary" type="button" data-action="selectModel">Choose route</button>
        </div>
        <div class="choices">${profileButtons}</div>
      </section>
      <section class="section">
        <h2>Bring your own key</h2>
        <p class="section-copy">Personal providers are encrypted in the Founder Provider Vault and remain separate from the managed allowance.</p>
        <div class="connections">${providerRows}</div>
      </section>
    </div>

    <div class="panel" data-panel="infrastructure">
      <section class="section">
        <h2>Infrastructure mode</h2>
        <p class="section-copy">Choose where work runs and which connected services this workspace may use.</p>
        <div class="segments" role="group" aria-label="Infrastructure mode">${modeButtons}</div>
        <div class="mode-detail"><strong>${escapeHtml(modeDefinition.summary)}</strong><span>${escapeHtml(modeDefinition.services)}</span></div>
      </section>
      <section class="section">
        <h2>Local models</h2>
        <p class="section-copy">Ollama can run through the embedded Founder Node without sending prompts to a cloud model.</p>
        <div class="button-row"><button class="primary" type="button" data-action="openConnections">Manage local AI</button></div>
      </section>
    </div>

    <div class="panel" data-panel="connections">
      <section class="section">
        <h2>Services</h2>
        <p class="section-copy">Connect the tools Founder can use to build and ship. Credentials remain managed by Founder OS rather than exposed in editor settings.</p>
        <div class="connections">${connectionRows}</div>
        <div class="button-row"><button class="primary" type="button" data-action="openConnections">Open connection center</button></div>
      </section>
    </div>

    <div class="panel" data-panel="advanced">
      <section class="section">
        <h2>Advanced controls</h2>
        <p class="section-copy">Editor preferences, raw connection overrides, and the local Node configuration.</p>
        <div class="button-row"><button class="secondary" type="button" data-action="openAdvancedSettings">Advanced settings</button><button class="secondary" type="button" data-action="openNodeConfig">Node configuration</button></div>
      </section>
    </div>
  </main>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const tabs = [...document.querySelectorAll('[data-tab]')];
    const panels = [...document.querySelectorAll('[data-panel]')];
    for (const tab of tabs) {
      tab.addEventListener('click', () => {
        for (const item of tabs) item.classList.toggle('selected', item === tab);
        for (const panel of panels) panel.classList.toggle('selected', panel.dataset.panel === tab.dataset.tab);
      });
    }
    for (const button of document.querySelectorAll('[data-action]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'action', action: button.dataset.action }));
    }
    for (const button of document.querySelectorAll('[data-mode]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'selectMode', mode: button.dataset.mode }));
    }
    for (const button of document.querySelectorAll('[data-profile]')) {
      button.addEventListener('click', () => vscode.postMessage({ type: 'selectProfile', profile: button.dataset.profile }));
    }
  </script>
</body>
</html>`;
  }
}

function profileSummary(id: ExecutionProfileId): string {
  switch (id) {
    case 'turbo':
      return 'Fast answers and focused edits';
    case 'balanced':
      return 'Best default for everyday building';
    case 'architect':
      return 'Deeper reasoning for complex systems';
    case 'autonomous':
      return 'Longer multi-step agent work';
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
