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
const IDLE = {
    state: 'idle',
    title: 'Ready',
    detail: 'Founder Dragon is watching this workspace.',
};
class FounderCompanionViewProvider {
    static viewId = 'founderOs.companion';
    view;
    snapshot = IDLE;
    settleTimer;
    resolveWebviewView(view) {
        this.view = view;
        view.webview.options = { enableScripts: true, localResourceRoots: [] };
        view.webview.onDidReceiveMessage((message) => {
            if (message.type !== 'action')
                return;
            switch (message.action) {
                case 'openEvidence':
                    void vscode.commands.executeCommand('founderOs.recentGatewayMetadata');
                    break;
                case 'openUsage':
                    void vscode.commands.executeCommand('founderOs.showCostBreakdown');
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
        if (settleAfterMs) {
            this.settleTimer = setTimeout(() => {
                this.snapshot = IDLE;
                this.render();
            }, settleAfterMs);
        }
    }
    render() {
        if (!this.view)
            return;
        const nonce = (0, node_crypto_1.randomBytes)(16).toString('hex');
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
      --dragon-blue: #5f91f3;
      --dragon-green: #44c59a;
      --dragon-fire: #ff8a47;
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
      display: grid; grid-template-columns: 104px minmax(0, 1fr); align-items: center;
      min-height: 112px; gap: 10px; width: 100%; border: 0; padding: 0;
      text-align: left; background: transparent; cursor: pointer;
    }
    .stage { position: relative; width: 104px; height: 96px; display: grid; place-items: center; }
    .dragon { width: 100px; height: 88px; overflow: visible; color: var(--dragon-blue); transform-origin: 50% 62%; }
    .creature { transform-origin: center; }
    .wing { transform-box: fill-box; transform-origin: bottom center; }
    .wing-left { transform-origin: right bottom; }
    .wing-right { transform-origin: left bottom; }
    .tail { transform-box: fill-box; transform-origin: left center; }
    .eye { fill: currentColor; stroke: none; }
    .body-fill { fill: var(--dragon-blue); opacity: .3; }
    .wing-fill { fill: color-mix(in srgb, var(--dragon-blue) 26%, transparent); }
    .scale { color: var(--dragon-green); }
    .belly { color: var(--dragon-green); opacity: .8; }
    .fire, .spark { color: var(--dragon-fire); }
    .fire, .smoke, .trail, .spark { opacity: 0; }
    .working .creature { animation: fly 900ms ease-in-out infinite; }
    .working .wing-left { animation: flap-left 300ms ease-in-out infinite alternate; }
    .working .wing-right { animation: flap-right 300ms ease-in-out infinite alternate; }
    .working .trail { opacity: .55; animation: trail 900ms linear infinite; }
    .success .creature { animation: land 520ms ease-out both; }
    .success .fire { opacity: 1; animation: fire 520ms ease-in-out 3 alternate; }
    .success .spark { opacity: 1; animation: spark 850ms ease-out 2; }
    .attention .creature { animation: hover 1.25s ease-in-out infinite; }
    .error .creature { transform: translateY(4px) rotate(-3deg); }
    .error .dragon { color: var(--red); }
    .attention .dragon { color: var(--amber); }
    .error .smoke { opacity: .65; animation: smoke 1.4s ease-out infinite; }
    .idle .creature { animation: breathe 2.8s ease-in-out infinite; }
    .idle .tail { animation: tail 2.4s ease-in-out infinite alternate; }
    .idle .fire { animation: ember-breath 5.2s ease-in-out infinite; transform-origin: left center; }
    .idle .spark { animation: ember-spark 5.2s ease-out infinite; }
    .copy { min-width: 0; }
    .eyebrow { color: var(--tone); font-size: 10px; font-weight: 650; text-transform: uppercase; }
    .title { margin-top: 3px; font-size: 13px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .detail { margin-top: 4px; color: var(--muted); font-size: 11px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .evidence { margin-top: 7px; color: var(--vscode-textLink-foreground); font-size: 10px; }
    .companion:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 3px; border-radius: 6px; }
    .pet-menu {
      display: none;
      margin: 0 0 4px;
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
    @keyframes fly { 0%,100% { transform: translate(0,2px) rotate(-1deg); } 50% { transform: translate(5px,-4px) rotate(2deg); } }
    @keyframes flap-left { from { transform: rotate(5deg); } to { transform: rotate(-18deg); } }
    @keyframes flap-right { from { transform: rotate(-5deg); } to { transform: rotate(18deg); } }
    @keyframes trail { from { transform: translateX(5px); opacity: .65; } to { transform: translateX(-8px); opacity: 0; } }
    @keyframes land { from { transform: translateY(-7px) rotate(3deg); } to { transform: translateY(2px) rotate(0); } }
    @keyframes fire { from { transform: scaleX(.55); opacity: .5; } to { transform: scaleX(1); opacity: 1; } }
    @keyframes spark { from { transform: translate(0,0) scale(1); opacity: 1; } to { transform: translate(10px,-10px) scale(.2); opacity: 0; } }
    @keyframes hover { 0%,100% { transform: translateY(2px); } 50% { transform: translateY(-3px); } }
    @keyframes smoke { from { transform: translateY(2px); opacity: .6; } to { transform: translateY(-8px); opacity: 0; } }
    @keyframes breathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.025); } }
    @keyframes tail { from { transform: rotate(-2deg); } to { transform: rotate(5deg); } }
    @keyframes ember-breath {
      0%, 72%, 100% { transform: scale(.15); opacity: 0; }
      78% { transform: scale(.45); opacity: .38; }
      85% { transform: scale(.7); opacity: .62; }
      92% { transform: scale(.25); opacity: 0; }
    }
    @keyframes ember-spark {
      0%, 80%, 100% { transform: translate(0,0) scale(.2); opacity: 0; }
      86% { transform: translate(5px,-4px) scale(.75); opacity: .65; }
      94% { transform: translate(12px,-9px) scale(.2); opacity: 0; }
    }
    @media (max-width: 250px) { .companion { grid-template-columns: 76px minmax(0, 1fr); } .stage { width: 76px; } .dragon { width: 74px; } }
    @media (prefers-reduced-motion: reduce) { .creature, .wing, .tail, .fire, .smoke, .trail, .spark { animation: none !important; } }
  </style>
</head>
<body>
  <button class="companion ${state}" type="button" aria-expanded="false" aria-controls="pet-menu" aria-label="${escapeHtml(title)}. ${escapeHtml(detail)}" data-toggle-menu>
    <span class="stage" aria-hidden="true">
      <svg class="dragon" viewBox="0 0 160 120" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
        <path class="trail" d="M45 69H7m33 12H18m19 11H25"/>
        <g class="creature">
          <path class="wing wing-left wing-fill" d="M69 66C50 43 31 38 15 14l6 33-7 8 17 1-5 12 26 13 20-4"/>
          <path class="wing wing-left" d="M69 66C50 43 31 38 15 14l6 33-7 8 17 1-5 12 26 13m-29-48 26 16M31 56l25 9"/>
          <path class="wing wing-right wing-fill" d="M91 66c19-23 38-28 54-52l-6 33 7 8-17 1 5 12-26 13-20-4"/>
          <path class="wing wing-right" d="M91 66c19-23 38-28 54-52l-6 33 7 8-17 1 5 12-26 13m29-48-26 16m18 7-25 9"/>
          <path class="tail" d="M95 91c27 19 57 10 52-11-2-10-13-14-20-7 11-1 14 11 5 15"/>
          <ellipse class="body-fill" cx="80" cy="79" rx="31" ry="33"/>
          <path d="M53 91c-7-21-3-40 11-48m43 48c7-21 3-40-11-48"/>
          <path class="belly" d="M68 65c7 5 17 5 24 0m-27 13c10 6 20 6 30 0m-27 13c8 5 16 5 24 0"/>
          <path class="body-fill" d="M58 46c0-22 10-35 22-35s22 13 22 35c0 13-9 22-22 22s-22-9-22-22Z"/>
          <path d="M58 46c0-22 10-35 22-35s22 13 22 35c0 13-9 22-22 22s-22-9-22-22Z"/>
          <path class="scale" d="m55 43-9-6 10-4-5-9 12 3m42 16 9-6-10-4 5-9-12 3"/>
          <path d="M66 18C57 14 50 7 55 2c5 7 11 7 19 12m20 4c9-4 16-11 11-16-5 7-11 7-19 12"/>
          <path class="scale" d="m71 13 3-10 6 8 6-8 3 10"/>
          <path d="M64 39c5-6 11-6 16-1 5-5 11-5 16 1"/>
          <path d="M69 52c7 5 15 5 22 0l5 6c-10 9-22 9-32 0Z"/>
          <circle class="eye" cx="70" cy="37" r="2.4"/>
          <circle class="eye" cx="90" cy="37" r="2.4"/>
          <circle class="eye" cx="73" cy="52" r="1.3"/>
          <circle class="eye" cx="87" cy="52" r="1.3"/>
          <path d="M57 99c-5 10-2 15 7 15m39-15c5 10 2 15-7 15m-39-4-7 4m53-4 7 4"/>
          <path class="fire" d="M96 56c14-9 24-4 31-18 0 11 6 15 16 16-9 5-11 11-9 20-10-8-21-7-33-7Z"/>
          <path class="spark" d="m136 43 7-8m-2 18 10-2"/>
          <path class="smoke" d="M103 49c9-8 15-2 14-13 8 6 2 14 11 18"/>
        </g>
      </svg>
    </span>
    <span class="copy">
      <span class="eyebrow">${labelFor(state)}</span>
      <span class="title">${escapeHtml(title)}</span>
      <span class="detail">${escapeHtml(detail)}</span>
      <span class="evidence">Click for options</span>
    </span>
  </button>
  <div class="pet-menu" id="pet-menu">
    <button class="menu-item" type="button" data-action="openUsage"><span>Usage</span><span class="menu-hint">Session details</span></button>
    <button class="menu-item" type="button" data-action="openEvidence"><span>Task evidence</span><span class="menu-hint">Recent requests</span></button>
    <button class="menu-item" type="button" data-action="openSettings"><span>Settings</span><span class="menu-hint">Founder IDE</span></button>
    <button class="menu-item" type="button" data-action="hide"><span>Hide Dragon</span><span class="menu-hint">Show again in Founder</span></button>
    <button class="menu-item" type="button" data-action="signOut"><span>Sign out</span><span class="menu-hint">Founder account</span></button>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const companion = document.querySelector('[data-toggle-menu]');
    const menu = document.querySelector('#pet-menu');
    companion?.addEventListener('click', () => {
      const open = menu?.classList.toggle('open') ?? false;
      companion.setAttribute('aria-expanded', String(open));
    });
    for (const button of document.querySelectorAll('[data-action]')) {
      button.addEventListener('click', () => {
        vscode.postMessage({ type: 'action', action: button.dataset.action });
      });
    });
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
        case 'idle': return 'Founder companion';
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