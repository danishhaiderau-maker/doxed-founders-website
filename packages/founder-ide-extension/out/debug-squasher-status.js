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
exports.createDebugSquasherStatus = createDebugSquasherStatus;
/**
 * Debug Squasher status bar integration for the Founder IDE extension.
 *
 * Polls GET /api/debug-squasher/latest every 2 minutes (and on activation)
 * and updates a status bar item to show either:
 *   - "$(check) Founder OS: Platform healthy"
 *   - "$(warning) Founder OS: N bugs found — click for diagnosis"
 *
 * Clicking the item opens the admin panel in the user's browser.
 *
 * Also surfaces the consent MCQ as a VS Code information message if the
 * connected Founder Node reports consent === 'unset'. The user picks
 * Yes / No / Later; the choice is POSTed to /api/debug-squasher/consent.
 *
 * Wired in extension.ts:activate() via createDebugSquasherStatus().
 */
const vscode = __importStar(require("vscode"));
const POLL_INTERVAL_MS = 2 * 60 * 1000;
let consentPromptShownThisSession = false;
/**
 * Create + start the status bar poller. Returns a Disposable that cleans up
 * the status bar item + interval timer.
 */
function createDebugSquasherStatus(context, getCredentials) {
    const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
    status.command = 'founderOs.openDebugSquasher';
    status.tooltip = 'Founder OS Debug Squasher — platform health check';
    context.subscriptions.push(status);
    const interval = setInterval(() => {
        void poll(getCredentials, status, context);
    }, POLL_INTERVAL_MS);
    // First poll immediately so the bar shows real data on activation.
    void poll(getCredentials, status, context);
    const commandDisposable = vscode.commands.registerCommand('founderOs.openDebugSquasher', () => openInBrowser(getCredentials));
    context.subscriptions.push(commandDisposable);
    return new vscode.Disposable(() => {
        clearInterval(interval);
        status.dispose();
        commandDisposable.dispose();
    });
}
async function poll(getCredentials, status, context) {
    const creds = getCredentials();
    if (!creds) {
        status.hide();
        return;
    }
    try {
        const latest = await fetchLatest(creds);
        if (latest) {
            renderStatus(status, latest);
            // One-shot consent prompt per session.
            if (!consentPromptShownThisSession) {
                consentPromptShownThisSession = true;
                void maybePromptConsent(creds, context);
            }
        }
        else {
            status.text = '$(question) Founder OS: No health check yet';
            status.show();
        }
    }
    catch {
        status.text = '$(circle-slash) Founder OS: health check unavailable';
        status.tooltip = 'Could not reach /api/debug-squasher/latest';
        status.show();
    }
}
function renderStatus(status, run) {
    if (run.overall === 'PASS') {
        status.text = '$(check) Founder OS: Platform healthy';
        status.tooltip = `Debug Squasher: all pillars green (score ${run.readinessScore}). Last run ${new Date(run.startedAt).toLocaleString()}.`;
        status.backgroundColor = undefined;
    }
    else {
        const failed = run.totals.checksFailed;
        status.text = `$(warning) Founder OS: ${failed} bug${failed === 1 ? '' : 's'} found`;
        status.tooltip = `Debug Squasher: ${run.overall} — ${failed} check(s) failed. Click to open diagnoses.`;
        status.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    }
    status.show();
}
async function fetchLatest(creds) {
    const url = `${creds.apiBaseUrl.replace(/\/$/, '')}/api/debug-squasher/latest`;
    const res = await fetch(url, {
        headers: { Authorization: `Bearer fos_${creds.nodeId}:${creds.nodeToken}` },
    });
    if (!res.ok)
        return null;
    const data = (await res.json());
    return data.run;
}
function openInBrowser(getCredentials) {
    const creds = getCredentials();
    if (!creds) {
        void vscode.window.showWarningMessage('Founder OS chat: pair a Founder Node first to view Debug Squasher results.');
        return;
    }
    // Open the platform dashboard rather than a node-local URL — diagnoses live
    // behind the JWT-guarded /admin/debug-squasher page.
    const base = creds.apiBaseUrl.replace(/\/api$/, '').replace(/\/$/, '');
    void vscode.env.openExternal(vscode.Uri.parse(`${base}/admin/debug-squasher`));
}
/**
 * One-time MCQ consent prompt. Mirrors the web pop-up: shows Yes / No / Later
 * buttons and POSTs the choice to /api/debug-squasher/consent.
 */
async function maybePromptConsent(creds, _context) {
    try {
        const consentUrl = `${creds.apiBaseUrl.replace(/\/$/, '')}/api/debug-squasher/consent`;
        const headers = {
            Authorization: `Bearer fos_${creds.nodeId}:${creds.nodeToken}`,
        };
        const checkRes = await fetch(consentUrl, { headers });
        if (!checkRes.ok)
            return;
        const state = (await checkRes.json());
        if (state.consent && state.consent !== 'unset')
            return;
        const choice = await vscode.window.showInformationMessage('Founder OS can run a daily health check that finds bugs before you do. Enable Debug Squasher?', 'Yes, enable', 'No, skip daily runs', 'Later');
        const choiceMap = {
            'Yes, enable': 'accepted',
            'No, skip daily runs': 'declined',
            Later: 'later',
        };
        const value = choice ? choiceMap[choice] : undefined;
        if (!value)
            return;
        await fetch(consentUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...headers },
            body: JSON.stringify({ choice: value }),
        });
    }
    catch {
        // Non-fatal: the web dashboard can also collect consent.
    }
}
//# sourceMappingURL=debug-squasher-status.js.map