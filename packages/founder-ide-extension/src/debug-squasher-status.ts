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
import * as vscode from 'vscode';
import type { FounderOsCredentials } from './credentials';
import { authorizationHeaderFromCredentials } from './credentials';

type SuggestedFix = {
  title: string;
  fix: string;
  severity: 'low' | 'medium' | 'high';
  files: string[];
};

type PillarSummary = {
  pillar: string;
  status: string;
  summary: string;
  diagnosis: string | null;
  suggestedFixes: SuggestedFix[];
  runDurationMs: number;
};

type LatestRun = {
  runId: string;
  triggeredBy: 'cron' | 'manual' | 'startup';
  startedAt: string;
  durationMs: number;
  overall: 'PASS' | 'FAIL' | 'DEGRADED';
  readinessScore: number;
  totals: { checksRun: number; checksPassed: number; checksFailed: number };
  pillars: PillarSummary[];
};

const POLL_INTERVAL_MS = 2 * 60 * 1000;
let consentPromptShownThisSession = false;

/**
 * Create + start the status bar poller. Returns a Disposable that cleans up
 * the status bar item + interval timer.
 */
export function createDebugSquasherStatus(
  context: vscode.ExtensionContext,
  getCredentials: () => FounderOsCredentials | null,
): vscode.Disposable {
  const status = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    90,
  );
  status.command = 'founderOs.openDebugSquasher';
  status.tooltip = 'Founder OS Debug Squasher — platform health check';
  context.subscriptions.push(status);

  const interval = setInterval(() => {
    void poll(getCredentials, status, context);
  }, POLL_INTERVAL_MS);

  // First poll immediately so the bar shows real data on activation.
  void poll(getCredentials, status, context);

  const commandDisposable = vscode.commands.registerCommand(
    'founderOs.openDebugSquasher',
    () => openInBrowser(getCredentials),
  );
  context.subscriptions.push(commandDisposable);

  return new vscode.Disposable(() => {
    clearInterval(interval);
    status.dispose();
    commandDisposable.dispose();
  });
}

async function poll(
  getCredentials: () => FounderOsCredentials | null,
  status: vscode.StatusBarItem,
  context: vscode.ExtensionContext,
): Promise<void> {
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
    } else {
      status.text = '$(question) Founder OS: No health check yet';
      status.show();
    }
  } catch {
    status.text = '$(circle-slash) Founder OS: health check unavailable';
    status.tooltip = 'Could not reach /api/debug-squasher/latest';
    status.show();
  }
}

function renderStatus(status: vscode.StatusBarItem, run: LatestRun): void {
  if (run.overall === 'PASS') {
    status.text = '$(check) Founder OS: Platform healthy';
    status.tooltip = `Debug Squasher: all pillars green (score ${run.readinessScore}). Last run ${new Date(run.startedAt).toLocaleString()}.`;
    status.backgroundColor = undefined;
  } else {
    const failed = run.totals.checksFailed;
    status.text = `$(warning) Founder OS: ${failed} bug${failed === 1 ? '' : 's'} found`;
    status.tooltip = `Debug Squasher: ${run.overall} — ${failed} check(s) failed. Click to open diagnoses.`;
    status.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
  }
  status.show();
}

async function fetchLatest(creds: FounderOsCredentials): Promise<LatestRun | null> {
  const url = `${creds.apiBaseUrl.replace(/\/$/, '')}/api/debug-squasher/latest`;
  const res = await fetch(url, {
    headers: { Authorization: authorizationHeaderFromCredentials(creds) },
  });
  if (!res.ok) return null;
  const data = (await res.json()) as { run: LatestRun | null };
  return data.run;
}

function openInBrowser(getCredentials: () => FounderOsCredentials | null): void {
  const creds = getCredentials();
  if (!creds) {
    void vscode.window.showWarningMessage(
      'Connect Founder IDE to Founder OS before viewing Debug Squasher results.',
    );
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
async function maybePromptConsent(
  creds: FounderOsCredentials,
  _context: vscode.ExtensionContext,
): Promise<void> {
  try {
    const consentUrl = `${creds.apiBaseUrl.replace(/\/$/, '')}/api/debug-squasher/consent`;
    const headers = {
      Authorization: authorizationHeaderFromCredentials(creds),
    };
    const checkRes = await fetch(consentUrl, { headers });
    if (!checkRes.ok) return;
    const state = (await checkRes.json()) as { consent?: string };
    if (state.consent && state.consent !== 'unset') return;

    const choice = await vscode.window.showInformationMessage(
      'Founder OS can run a daily health check that finds bugs before you do. Enable Debug Squasher?',
      'Yes, enable',
      'No, skip daily runs',
      'Later',
    );

    const choiceMap: Record<string, 'accepted' | 'declined' | 'later' | undefined> = {
      'Yes, enable': 'accepted',
      'No, skip daily runs': 'declined',
      Later: 'later',
    };
    const value = choice ? choiceMap[choice] : undefined;
    if (!value) return;

    await fetch(consentUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({ choice: value }),
    });
  } catch {
    // Non-fatal: the web dashboard can also collect consent.
  }
}
