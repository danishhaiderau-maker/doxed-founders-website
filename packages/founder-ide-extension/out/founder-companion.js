"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.FounderCompanionViewProvider = void 0;
const node_crypto_1 = require("node:crypto");
const vscode = __importStar(require("vscode"));
const protocol_1 = require("./ipc/protocol");
const server_1 = require("./ipc/server");
const IDLE = {
    state: 'idle',
    title: 'Resting in the nest',
    detail: 'Watching this workspace and ready for the next mission.',
};
const MEDIA_BY_STATE = {
    idle: 'dragon-idle.png',
    working: 'dragon-working.png',
    success: 'dragon-success-v3.png',
    attention: 'dragon-attention.png',
    error: 'dragon-attention.png',
};
class FounderCompanionViewProvider {
    context;
    static viewId = 'founderOs.companion';
    view;
    snapshot = IDLE;
    settleTimer;
    constructor(context) {
        this.context = context;
        this.broadcast();
    }
    resolveWebviewView(view) {
        this.view = view;
        view.webview.options = {
            enableScripts: true,
            localResourceRoots: [
                vscode.Uri.joinPath(this.context.extensionUri, 'resources', 'dragon'),
            ],
        };
        view.webview.onDidReceiveMessage((message) => {
            if (message.type !== 'action')
                return;
            switch (message.action) {
                case 'openEvidence':
                    void vscode.commands.executeCommand('founderOs.recentGatewayMetadata');
                    break;
                case 'openUsage':
                    void vscode.commands.executeCommand('founderOs.openSettings');
                    break;
                case 'openSettings':
                    void vscode.commands.executeCommand('founderOs.openSettings');
                    break;
                case 'hide':
                    void vscode.workspace.getConfiguration('founderOs').update('companion.enabled', false, vscode.ConfigurationTarget.Global);
                    break;
                case 'signOut':
                    void vscode.commands.executeCommand('founderOs.signOut');
                    break;
            }
        });
        this.render();
    }
    setWorking(title, detail) {
        this.set({ state: 'working', title, detail });
    }
    setSuccess(title, detail) {
        this.set({ state: 'success', title, detail }, 5_000);
    }
    setAttention(title, detail) {
        this.set({ state: 'attention', title, detail });
    }
    setError(title, detail) {
        this.set({ state: 'error', title, detail }, 8_000);
    }
    setIdle() {
        this.set(IDLE);
    }
    syncEnabled() {
        this.broadcast();
    }
    dispose() {
        if (this.settleTimer)
            clearTimeout(this.settleTimer);
        this.settleTimer = undefined;
        this.view = undefined;
    }
    set(snapshot, settleAfterMs) {
        if (this.settleTimer)
            clearTimeout(this.settleTimer);
        this.snapshot = snapshot;
        this.render();
        this.broadcast();
        if (settleAfterMs) {
            this.settleTimer = setTimeout(() => {
                this.snapshot = IDLE;
                this.render();
                this.broadcast();
            }, settleAfterMs);
        }
    }
    broadcast() {
        (0, server_1.broadcastCompanionState)({
            type: 'companionState',
            nonce: (0, protocol_1.generateNonce)(),
            ts: new Date().toISOString(),
            visible: vscode.workspace.getConfiguration('founderOs').get('companion.enabled', true),
            ...this.snapshot,
        });
    }
    render() {
        if (!this.view)
            return;
        const nonce = (0, node_crypto_1.randomBytes)(16).toString('hex');
        const { state, title, detail } = this.snapshot;
        const mediaUri = this.view.webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'resources', 'dragon', MEDIA_BY_STATE[state]));
        this.view.description = labelFor(state);
        this.view.webview.html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'; img-src ${this.view.webview.cspSource};">
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
      --surface: color-mix(in srgb, var(--vscode-sideBar-background) 91%, var(--vscode-foreground) 9%);
      --tone: ${toneFor(state)};
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 8px 12px 12px;
      overflow: hidden;
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      font: 12px/1.4 var(--vscode-font-family);
      letter-spacing: 0;
    }
    button { color: inherit; font: inherit; letter-spacing: 0; }
    .companion {
      display: grid;
      grid-template-columns: minmax(116px, 42%) minmax(0, 1fr);
      align-items: center;
      min-height: 120px;
      gap: 12px;
      width: 100%;
      border: 0;
      padding: 0;
      text-align: left;
      background: transparent;
      cursor: pointer;
    }
    .stage {
      position: relative;
      width: 100%;
      min-width: 116px;
      aspect-ratio: 4 / 3;
      overflow: hidden;
      border-radius: 7px;
      background: transparent;
      box-shadow: none;
      isolation: isolate;
    }
    .stage::after {
      position: absolute;
      inset: 0;
      z-index: 2;
      display: none;
      content: '';
      pointer-events: none;
    }
    .dragon-image {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center;
      filter: drop-shadow(0 8px 10px rgba(0,0,0,.24));
      transform: scale(.96);
    }
    .idle .dragon-image { animation: nest-breathe 3.6s ease-in-out infinite; }
    .working .dragon-image { animation: flight-pulse 760ms ease-in-out infinite alternate; }
    .attention .dragon-image { animation: attention-pulse 1.2s ease-in-out infinite; }
    .error .dragon-image { filter: grayscale(.24) sepia(.2) hue-rotate(315deg) saturate(1.25); }
    .success .stage { filter: none; }
    .state-tabs {
      position: absolute;
      top: 5px;
      right: 5px;
      z-index: 3;
      display: flex;
      align-items: center;
      gap: 3px;
    }
    .state-tab {
      min-height: 18px;
      padding: 2px 5px;
      border: 1px solid rgba(255,255,255,.16);
      border-radius: 5px;
      color: rgba(255,255,255,.78);
      background: rgba(8,10,13,.76);
      backdrop-filter: blur(8px);
      font-size: 8px;
      font-weight: 700;
      line-height: 1;
      text-transform: uppercase;
    }
    .state-tab.live { color: #fff; background: color-mix(in srgb, var(--tone) 74%, rgba(8,10,13,.88)); }
    .media-fallback {
      display: none;
      position: absolute;
      inset: 0;
      z-index: 1;
      place-items: center;
      padding: 12px;
      color: rgba(255,255,255,.72);
      text-align: center;
      font-size: 10px;
    }
    .stage.media-error .dragon-image { display: none; }
    .stage.media-error .media-fallback { display: grid; }
    .copy { min-width: 0; }
    .eyebrow { color: var(--tone); font-size: 10px; font-weight: 650; text-transform: uppercase; }
    .title {
      margin-top: 3px;
      overflow: hidden;
      font-size: 13px;
      font-weight: 650;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .detail {
      display: -webkit-box;
      margin-top: 4px;
      overflow: hidden;
      color: var(--muted);
      font-size: 11px;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
    }
    .evidence { margin-top: 7px; color: var(--vscode-textLink-foreground); font-size: 10px; }
    .companion:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 3px; border-radius: 6px; }
    .pet-menu {
      display: none;
      margin: 5px 0 0;
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--surface);
    }
    .pet-menu.open { display: grid; }
    .menu-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      min-height: 34px;
      align-items: center;
      border: 0;
      border-top: 1px solid var(--border);
      padding: 6px 9px;
      background: transparent;
      cursor: pointer;
      text-align: left;
    }
    .menu-item:first-child { border-top: 0; }
    .menu-item:hover { background: var(--vscode-list-hoverBackground); }
    .menu-hint { color: var(--muted); font-size: 10px; }
    @keyframes flight-pulse {
      from { transform: scale(.93) translate3d(-2px, 3px, 0) rotate(-1deg); }
      to { transform: scale(1) translate3d(3px, -4px, 0) rotate(1deg); }
    }
    @keyframes nest-breathe { 0%, 100% { transform: scale(.95); } 50% { transform: scale(.985) translateY(-1px); } }
    @keyframes attention-pulse { 0%, 100% { transform: scale(.94) rotate(-1deg); } 50% { transform: scale(.99) rotate(1deg); } }
    @media (max-width: 280px) {
      .companion { grid-template-columns: 96px minmax(0, 1fr); gap: 8px; }
      .stage { min-width: 96px; }
      .state-tab:first-child { display: none; }
    }
    @media (prefers-reduced-motion: reduce) {
      .dragon-image { animation: none !important; }
    }
  </style>
</head>
<body>
  <button class="companion ${state}" type="button" aria-expanded="false" aria-controls="pet-menu" aria-label="${escapeHtml(title)}. ${escapeHtml(detail)}" data-toggle-menu>
    <span class="stage" data-stage aria-hidden="true">
      <img class="dragon-image" alt="" src="${mediaUri}">
      <span class="state-tabs"><span class="state-tab">Dragon</span><span class="state-tab live">${escapeHtml(labelFor(state))}</span></span>
      <span class="media-fallback">Founder Dragon is waking up</span>
    </span>
    <span class="copy">
      <span class="eyebrow">${escapeHtml(labelFor(state))}</span>
      <span class="title">${escapeHtml(title)}</span>
      <span class="detail">${escapeHtml(detail)}</span>
      <span class="evidence">Open companion controls</span>
    </span>
  </button>
  <div class="pet-menu" id="pet-menu">
    <button class="menu-item" type="button" data-action="openUsage"><span>Plan and usage</span><span class="menu-hint">Limits and routing</span></button>
    <button class="menu-item" type="button" data-action="openEvidence"><span>Task evidence</span><span class="menu-hint">Recent requests</span></button>
    <button class="menu-item" type="button" data-action="openSettings"><span>Settings</span><span class="menu-hint">Founder IDE</span></button>
    <button class="menu-item" type="button" data-action="hide"><span>Hide Dragon</span><span class="menu-hint">Restore from Founder</span></button>
    <button class="menu-item" type="button" data-action="signOut"><span>Sign out</span><span class="menu-hint">Founder account</span></button>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const companion = document.querySelector('[data-toggle-menu]');
    const menu = document.querySelector('#pet-menu');
    const stage = document.querySelector('[data-stage]');
    const image = document.querySelector('img');
    image?.addEventListener('error', () => stage?.classList.add('media-error'));
    companion?.addEventListener('click', () => {
      const open = menu?.classList.toggle('open') ?? false;
      companion.setAttribute('aria-expanded', String(open));
    });
    for (const button of document.querySelectorAll('[data-action]')) {
      button.addEventListener('click', () => {
        vscode.postMessage({ type: 'action', action: button.dataset.action });
      });
    }
  </script>
</body>
</html>`;
    }
}
exports.FounderCompanionViewProvider = FounderCompanionViewProvider;
function labelFor(state) {
    switch (state) {
        case 'working': return 'In flight';
        case 'success': return 'Delivered';
        case 'attention': return 'Needs you';
        case 'error': return 'Blocked';
        case 'idle': return 'At rest';
    }
}
function toneFor(state) {
    switch (state) {
        case 'working': return 'var(--blue)';
        case 'success': return 'var(--green)';
        case 'attention': return 'var(--amber)';
        case 'error': return 'var(--red)';
        case 'idle': return 'var(--muted)';
    }
}
function escapeHtml(value) {
    return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}
//# sourceMappingURL=founder-companion.js.map