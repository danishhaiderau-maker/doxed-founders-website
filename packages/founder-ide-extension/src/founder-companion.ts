import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';

export type FounderCompanionState = 'idle' | 'working' | 'success' | 'attention' | 'error';

type CompanionSnapshot = {
  state: FounderCompanionState;
  title: string;
  detail: string;
};

const IDLE: CompanionSnapshot = {
  state: 'idle',
  title: 'Ready',
  detail: 'Founder Dragon is watching this workspace.',
};

export class FounderCompanionViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  static readonly viewId = 'founderOs.companion';

  private view: vscode.WebviewView | undefined;
  private snapshot: CompanionSnapshot = IDLE;
  private settleTimer: NodeJS.Timeout | undefined;

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [] };
    view.webview.onDidReceiveMessage((message: { type?: string }) => {
      if (message.type === 'openEvidence') {
        void vscode.commands.executeCommand('founderOs.recentGatewayMetadata');
      }
    });
    this.render();
  }

  setWorking(title: string, detail: string): void {
    this.set({ state: 'working', title, detail });
  }

  setSuccess(title: string, detail: string): void {
    this.set({ state: 'success', title, detail }, 5_000);
  }

  setAttention(title: string, detail: string): void {
    this.set({ state: 'attention', title, detail });
  }

  setError(title: string, detail: string): void {
    this.set({ state: 'error', title, detail }, 8_000);
  }

  setIdle(): void {
    this.set(IDLE);
  }

  dispose(): void {
    if (this.settleTimer) clearTimeout(this.settleTimer);
    this.settleTimer = undefined;
    this.view = undefined;
  }

  private set(snapshot: CompanionSnapshot, settleAfterMs?: number): void {
    if (this.settleTimer) clearTimeout(this.settleTimer);
    this.snapshot = snapshot;
    this.render();
    if (settleAfterMs) {
      this.settleTimer = setTimeout(() => {
        this.snapshot = IDLE;
        this.render();
      }, settleAfterMs);
    }
  }

  private render(): void {
    if (!this.view) return;
    const nonce = randomBytes(16).toString('hex');
    const { state, title, detail } = this.snapshot;
    this.view.description = title;
    this.view.webview.html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style nonce="${nonce}">
    :root {
      color-scheme: light dark;
      --blue: #2f80ed;
      --green: #35b779;
      --amber: #e4a853;
      --red: #e45b5b;
      --muted: color-mix(in srgb, var(--vscode-foreground) 62%, transparent);
      --border: color-mix(in srgb, var(--vscode-foreground) 16%, transparent);
      --surface: color-mix(in srgb, var(--vscode-sideBar-background) 90%, var(--vscode-foreground) 10%);
      --tone: ${toneFor(state)};
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 10px 12px 12px; overflow: hidden;
      color: var(--vscode-foreground); background: var(--vscode-sideBar-background);
      font: 12px/1.4 var(--vscode-font-family); letter-spacing: 0;
    }
    button { color: inherit; font: inherit; letter-spacing: 0; }
    .companion {
      display: grid; grid-template-columns: 88px minmax(0, 1fr); align-items: center;
      min-height: 100px; gap: 10px; width: 100%; border: 0; padding: 0;
      text-align: left; background: transparent; cursor: pointer;
    }
    .stage { position: relative; width: 88px; height: 78px; display: grid; place-items: center; }
    .dragon { width: 72px; height: 72px; color: var(--tone); transform-origin: 50% 58%; }
    .wing { transform-box: fill-box; transform-origin: right center; }
    .eye { fill: currentColor; stroke: none; }
    .fire, .smoke, .trail { opacity: 0; }
    .working .dragon { animation: fly 900ms ease-in-out infinite; }
    .working .wing { animation: flap 360ms ease-in-out infinite alternate; }
    .working .trail { opacity: .55; animation: trail 900ms linear infinite; }
    .success .dragon { animation: land 520ms ease-out both; }
    .success .fire { opacity: 1; animation: fire 520ms ease-in-out 3 alternate; }
    .attention .dragon { animation: hover 1.25s ease-in-out infinite; }
    .error .dragon { transform: translateY(4px) rotate(-3deg); }
    .error .smoke { opacity: .65; animation: smoke 1.4s ease-out infinite; }
    .idle .dragon { animation: breathe 2.8s ease-in-out infinite; }
    .copy { min-width: 0; }
    .eyebrow { color: var(--tone); font-size: 10px; font-weight: 650; text-transform: uppercase; }
    .title { margin-top: 3px; font-size: 13px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .detail { margin-top: 4px; color: var(--muted); font-size: 11px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .evidence { margin-top: 7px; color: var(--vscode-textLink-foreground); font-size: 10px; }
    .companion:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 3px; border-radius: 6px; }
    @keyframes fly { 0%,100% { transform: translate(0,2px) rotate(-1deg); } 50% { transform: translate(5px,-4px) rotate(2deg); } }
    @keyframes flap { from { transform: rotate(8deg); } to { transform: rotate(-18deg); } }
    @keyframes trail { from { transform: translateX(5px); opacity: .65; } to { transform: translateX(-8px); opacity: 0; } }
    @keyframes land { from { transform: translateY(-7px) rotate(3deg); } to { transform: translateY(2px) rotate(0); } }
    @keyframes fire { from { transform: scaleX(.55); opacity: .5; } to { transform: scaleX(1); opacity: 1; } }
    @keyframes hover { 0%,100% { transform: translateY(2px); } 50% { transform: translateY(-3px); } }
    @keyframes smoke { from { transform: translateY(2px); opacity: .6; } to { transform: translateY(-8px); opacity: 0; } }
    @keyframes breathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.025); } }
    @media (max-width: 250px) { .companion { grid-template-columns: 64px minmax(0, 1fr); } .stage { width: 64px; } .dragon { width: 58px; } }
    @media (prefers-reduced-motion: reduce) { .dragon, .wing, .fire, .smoke, .trail { animation: none !important; } }
  </style>
</head>
<body>
  <button class="companion ${state}" type="button" aria-label="${escapeHtml(title)}. ${escapeHtml(detail)}" data-action="evidence">
    <span class="stage" aria-hidden="true">
      <svg class="dragon" viewBox="0 0 120 96" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">
        <path class="trail" d="M38 58H8m25 10H16"/>
        <path class="wing" d="M62 49 27 18l7 29-22 8 32 15"/>
        <path d="M54 47c0-20 13-31 31-29l7-12 8 15 14 4-12 10c7 6 10 15 8 24-3 15-17 24-34 20l-19-5"/>
        <path d="M67 73c-6 18-25 22-43 10 15 1 23-6 29-18"/>
        <path class="fire" d="M107 43c12 1 18-4 22-12-1 10 3 16 11 20-9 2-16 0-21-4"/>
        <path class="smoke" d="M106 34c8-6 14-2 12-11 8 5 2 12 10 14"/>
        <circle class="eye" cx="89" cy="38" r="2.6"/>
      </svg>
    </span>
    <span class="copy">
      <span class="eyebrow">${labelFor(state)}</span>
      <span class="title">${escapeHtml(title)}</span>
      <span class="detail">${escapeHtml(detail)}</span>
      <span class="evidence">Open evidence</span>
    </span>
  </button>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.querySelector('[data-action="evidence"]')?.addEventListener('click', () => {
      vscode.postMessage({ type: 'openEvidence' });
    });
  </script>
</body>
</html>`;
  }
}

function labelFor(state: FounderCompanionState): string {
  switch (state) {
    case 'working': return 'In flight';
    case 'success': return 'Delivered';
    case 'attention': return 'Needs you';
    case 'error': return 'Blocked';
    case 'idle': return 'Founder companion';
  }
}

function toneFor(state: FounderCompanionState): string {
  switch (state) {
    case 'working': return 'var(--blue)';
    case 'success': return 'var(--green)';
    case 'attention': return 'var(--amber)';
    case 'error': return 'var(--red)';
    case 'idle': return 'var(--muted)';
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
