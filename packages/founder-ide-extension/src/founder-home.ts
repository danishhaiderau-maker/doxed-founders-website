import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import type { FounderAgentAwarenessSummary } from './agent-awareness';
import type { FounderGoalUiState } from './founder-goal-state';

type FounderHomeAction =
  | 'newChat'
  | 'openProject'
  | 'openPreview'
  | 'openChanges'
  | 'openDeploy'
  | 'openRemote'
  | 'openFounder'
  | 'developerMode';

interface FounderHomeMessage {
  type: 'action';
  action?: FounderHomeAction;
}

export interface FounderHomeDependencies {
  goal(): FounderGoalUiState | null;
  awareness(): FounderAgentAwarenessSummary;
}

export class FounderHomePanel implements vscode.Disposable {
  static readonly viewType = 'founderOs.home';

  private panel: vscode.WebviewPanel | undefined;
  private panelDisposables: vscode.Disposable[] = [];

  constructor(private readonly dependencies: FounderHomeDependencies) {}

  show(options: { preserveFocus?: boolean } = {}): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Active, Boolean(options.preserveFocus));
      this.refresh();
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      FounderHomePanel.viewType,
      'Founder Home',
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
      panel.webview.onDidReceiveMessage((message: FounderHomeMessage) =>
        this.handleMessage(message),
      ),
    );
    this.refresh();
  }

  showWhenWorkspaceIsIdle(): void {
    if (vscode.workspace.getConfiguration('founderOs').get<string>('interfaceMode') !== 'founder') {
      return;
    }
    if (vscode.window.activeTextEditor) return;
    this.show({ preserveFocus: false });
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

  private async handleMessage(message: FounderHomeMessage): Promise<void> {
    if (message.type !== 'action' || !message.action) return;
    switch (message.action) {
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
      case 'openProject':
        await vscode.commands.executeCommand('founderOs.openProjects');
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
            { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
          );
        } catch {
          await vscode.env.openExternal(vscode.Uri.parse(previewUrl));
        }
        break;
      }
      case 'openChanges':
        await vscode.commands.executeCommand('workbench.view.scm');
        break;
      case 'openDeploy':
        await vscode.commands.executeCommand('founderOs.openConnectionsView');
        break;
      case 'openRemote':
        await vscode.commands.executeCommand('founderOs.openRemoteView');
        break;
      case 'openFounder':
        await vscode.commands.executeCommand('founderOs.openHub');
        break;
      case 'developerMode':
        await vscode.workspace.getConfiguration('founderOs').update(
          'interfaceMode',
          'developer',
          vscode.ConfigurationTarget.Global,
        );
        break;
    }
  }

  private renderHtml(): string {
    const nonce = randomBytes(16).toString('hex');
    const goal = this.dependencies.goal();
    const awareness = this.dependencies.awareness();
    const project = vscode.workspace.workspaceFolders?.[0]?.name?.trim();
    const objective = goal?.objective || (project ? `Build and ship ${project}` : 'Choose a project and tell Founder what to build');
    const agentLabel = awareness.activeCount === 0
      ? 'Ready'
      : `${awareness.activeCount} active task${awareness.activeCount === 1 ? '' : 's'}`;
    const agentDetail = awareness.conflictCount > 0
      ? `${awareness.conflictCount} overlap${awareness.conflictCount === 1 ? '' : 's'} coordinating`
      : 'No workspace conflicts';
    const csp = [
      "default-src 'none'",
      `style-src 'nonce-${nonce}'`,
      `script-src 'nonce-${nonce}'`,
    ].join('; ');

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style nonce="${nonce}">
    :root {
      color-scheme: dark;
      --bg: #151617;
      --band: #1c1e20;
      --surface: #24272a;
      --surface-hover: #2b2f32;
      --border: #34383c;
      --text: #f2f4f5;
      --muted: #a7adb2;
      --accent: #38b879;
      --accent-strong: #56d993;
      --focus: #69a7ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    button { font: inherit; letter-spacing: 0; }
    .shell {
      width: min(920px, 100%);
      margin: 0 auto;
      padding: clamp(28px, 7vh, 72px) clamp(20px, 6vw, 64px) 44px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 42px;
    }
    .mark {
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border: 1px solid #426d58;
      border-radius: 8px;
      color: var(--accent-strong);
      font-weight: 700;
      background: #18241e;
    }
    .brand strong { display: block; font-size: 15px; }
    .brand span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
    .eyebrow {
      margin: 0 0 9px;
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
    }
    h1 {
      max-width: 760px;
      margin: 0;
      font-size: clamp(30px, 5vw, 48px);
      line-height: 1.08;
      font-weight: 650;
    }
    .goal-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 18px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 7px;
    }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent);
    }
    .primary {
      margin-top: 34px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .primary button, .footer button {
      min-height: 40px;
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 0 16px;
      color: var(--text);
      background: var(--surface);
      cursor: pointer;
    }
    .primary button:first-child {
      border-color: #27784f;
      background: #1d6f49;
    }
    .primary button:hover, .footer button:hover { background: var(--surface-hover); }
    .primary button:first-child:hover { background: #247f55; }
    button:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
    .tools {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin-top: 46px;
    }
    .tool {
      min-height: 112px;
      padding: 17px;
      border: 1px solid var(--border);
      border-radius: 8px;
      text-align: left;
      color: var(--text);
      background: var(--band);
      cursor: pointer;
    }
    .tool:hover { border-color: #535a60; background: var(--surface); }
    .tool strong { display: block; margin-bottom: 7px; font-size: 14px; }
    .tool span { display: block; color: var(--muted); line-height: 1.45; font-size: 12px; }
    .footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-top: 36px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
      color: var(--muted);
      font-size: 12px;
    }
    .footer button { min-height: 34px; padding: 0 12px; color: var(--muted); background: transparent; }
    @media (max-width: 520px) {
      .shell { padding-inline: 20px; }
      .tools { grid-template-columns: 1fr; }
      .tool { min-height: 84px; }
      .footer { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="brand">
      <div class="mark" aria-hidden="true">F</div>
      <div>
        <strong>Founder</strong>
        <span>${escapeHtml(project || 'Your AI workspace')}</span>
      </div>
    </header>

    <section aria-labelledby="goal-title">
      <p class="eyebrow">Pursuing goal</p>
      <h1 id="goal-title">${escapeHtml(objective)}</h1>
      <div class="goal-meta">
        <span class="status"><span class="dot"></span>${escapeHtml(agentLabel)}</span>
        <span>${escapeHtml(agentDetail)}</span>
        <span>${goal ? `Goal v${goal.version}` : 'New workspace'}</span>
      </div>
      <div class="primary">
        <button type="button" data-action="newChat">New chat</button>
        <button type="button" data-action="openProject">${project ? 'Switch project' : 'Open project'}</button>
        <button type="button" data-action="openFounder">Open Founder</button>
      </div>
    </section>

    <section class="tools" aria-label="Founder workspace">
      <button class="tool" type="button" data-action="openPreview">
        <strong>Preview</strong>
        <span>Open the product beside the work.</span>
      </button>
      <button class="tool" type="button" data-action="openChanges">
        <strong>Changes</strong>
        <span>Review what Founder changed before shipping.</span>
      </button>
      <button class="tool" type="button" data-action="openDeploy">
        <strong>Deploy</strong>
        <span>Open connected services and release controls.</span>
      </button>
      <button class="tool" type="button" data-action="openRemote">
        <strong>Remote</strong>
        <span>Review this computer and web control sessions.</span>
      </button>
    </section>

    <footer class="footer">
      <span>Founder mode keeps legacy IDE tools out of the way.</span>
      <button type="button" data-action="developerMode">Switch to Developer mode</button>
    </footer>
  </main>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.addEventListener('click', (event) => {
      const target = event.target instanceof Element
        ? event.target.closest('[data-action]')
        : null;
      const action = target && target.getAttribute('data-action');
      if (action) vscode.postMessage({ type: 'action', action });
    });
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
