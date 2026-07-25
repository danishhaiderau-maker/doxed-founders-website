/**
 * Phase 2 — device-code sign-in flow for the VS Code extension.
 *
 * The IDE side mirrors the tray side: it talks to the API directly when the
 * user triggers `founderOs.signIn`, displays the userCode in a notification
 * with an "Open browser" button, polls until authorized, then writes the
 * resulting credentials to ~/FounderVault/node-config.json and refreshes
 * the extension's chat provider.
 *
 * The flow is designed to fail-closed: any error → no creds written. The
 * user can retry from the command palette.
 */
import * as vscode from 'vscode';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { randomUUID, randomBytes } from 'node:crypto';

/** RFC 8628 grant shape returned by POST /api/founder-node/device-code. */
export interface DeviceCodeGrant {
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete?: string;
  expiresAt: string;
  interval: number;
}

/** Normalized poll outcome. */
export type PollOutcome =
  | { kind: 'pending'; waitMs: number }
  | { kind: 'slow_down'; waitMs: number }
  | { kind: 'authorized'; creds: WrittenCredentials }
  | { kind: 'expired'; message: string }
  | { kind: 'denied'; message: string }
  | { kind: 'error'; message: string };

export interface WrittenCredentials {
  apiBaseUrl: string;
  founderId: string;
  nodeId: string;
  nodeToken: string;
  tokenExpiresAt?: string;
  installId?: string;
}

/** Path to install.json sidecar that holds installId + ipcSecret. */
function installSidecarPath(): string {
  return path.join(os.homedir(), 'FounderVault', 'install.json');
}

/** Path to node-config.json (shared with the tray). */
export function nodeConfigPath(): string {
  return path.join(os.homedir(), 'FounderVault', 'node-config.json');
}

/**
 * Ensure an install identity exists in install.json. The tray writes the same
 * file; if it's already there we reuse it, otherwise we mint fresh values.
 * Returns { installId, ipcSecret } for the device-code request body.
 */
export function ensureInstallIdentity(): { installId: string; ipcSecret: string } {
  const file = installSidecarPath();
  let install: { installId?: string; ipcSecret?: string } = {};
  try {
    if (fs.existsSync(file)) {
      install = JSON.parse(fs.readFileSync(file, 'utf8'));
    }
  } catch {
    install = {};
  }
  if (!install.installId || !install.ipcSecret) {
    install.installId = install.installId ?? randomUUID();
    install.ipcSecret = install.ipcSecret ?? randomBytes(32).toString('hex');
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, JSON.stringify(install, null, 2), 'utf8');
    } catch {
      // best-effort — the device-code flow still works without persistence
      // (the tray will re-mint on its next start).
    }
  }
  return { installId: install.installId, ipcSecret: install.ipcSecret };
}

/** Read the apiBaseUrl from settings or the existing node-config.json. */
function resolveApiBaseUrl(): string {
  const cfg = vscode.workspace.getConfiguration('founderOs');
  const fromSettings = cfg.get<string>('apiBaseUrl')?.trim();
  if (fromSettings) return fromSettings.replace(/\/$/, '');
  try {
    const configPath = nodeConfigPath();
    if (fs.existsSync(configPath)) {
      const parsed = JSON.parse(fs.readFileSync(configPath, 'utf8')) as { apiBaseUrl?: string };
      if (parsed.apiBaseUrl) return parsed.apiBaseUrl.replace(/\/$/, '');
    }
  } catch {
    /* ignore */
  }
  return 'https://doxxedcrypto.digital';
}

function apiBase(apiBaseUrl: string, p: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${p.startsWith('/') ? p : `/${p}`}`;
}

/**
 * POST /api/founder-node/device-code. Anonymous. Includes the install's
 * installId so authorize() on the server pairs a node bound to this
 * install's IPC pipe name.
 */
export async function requestDeviceCode(
  apiBaseUrl: string,
  installId: string,
  ipcSecret: string,
): Promise<DeviceCodeGrant> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/device-code'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ installId, ipcSecret }),
  });
  const body = (await res.json().catch(() => null)) as (DeviceCodeGrant & {
    message?: string | string[];
  }) | null;
  if (!res.ok || !body) {
    const msg = Array.isArray(body?.message) ? body.message.join(', ') : body?.message;
    throw new Error(msg ?? `Failed to start device-code flow (${res.status})`);
  }
  return body;
}

/**
 * Poll once. Returns a normalized outcome so callers can drive a loop
 * without re-implementing the RFC 8628 HTTP status mapping.
 */
export async function pollDeviceCode(
  apiBaseUrl: string,
  deviceCode: string,
  install: { installId: string; ipcSecret: string },
): Promise<PollOutcome> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/device-code/poll'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ deviceCode }),
  });

  const body = (await res.json().catch(() => null)) as
    | ({
        status: 'pending' | 'authorized' | 'expired' | 'denied' | 'slow_down';
        interval?: number;
        error?: string;
        founderId?: string;
        nodeId?: string;
        nodeToken?: string;
        tokenExpiresAt?: string;
        installId?: string;
      })
    | null;

  if (!body) return { kind: 'error', message: `Empty response (${res.status})` };

  if (body.status === 'pending') {
    return { kind: 'pending', waitMs: (body.interval ?? 5) * 1000 };
  }
  if (body.status === 'slow_down') {
    const retryAfter = Number(res.headers.get('retry-after') ?? 0);
    const interval = body.interval ?? (Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 10);
    return { kind: 'slow_down', waitMs: interval * 1000 };
  }
  if (body.status === 'expired') {
    return { kind: 'expired', message: body.error ?? 'Device code expired' };
  }
  if (body.status === 'denied') {
    return { kind: 'denied', message: body.error ?? 'Authorization denied' };
  }
  if (body.status === 'authorized') {
    if (!body.founderId || !body.nodeId || !body.nodeToken) {
      return { kind: 'expired', message: 'Authorized grant has no token (already consumed)' };
    }
    const creds = writeAuthorizedConfig(apiBaseUrl, {
      founderId: body.founderId,
      nodeId: body.nodeId,
      nodeToken: body.nodeToken,
      tokenExpiresAt: body.tokenExpiresAt,
      installId: body.installId ?? install.installId,
    }, install);
    return { kind: 'authorized', creds };
  }
  return { kind: 'error', message: `Unknown status: ${body.status}` };
}

/**
 * Write the authorized credentials to ~/FounderVault/node-config.json. The
 * tray + chat provider both read this file. Returns the WrittenCredentials
 * for callers to refresh extension state.
 */
export function writeAuthorizedConfig(
  apiBaseUrl: string,
  authorized: {
    founderId: string;
    nodeId: string;
    nodeToken: string;
    tokenExpiresAt?: string;
    installId?: string;
  },
  install: { installId: string; ipcSecret: string },
): WrittenCredentials {
  const file = nodeConfigPath();
  // Preserve any existing fields (ollama config, founderCloud, etc).
  let existing: Record<string, unknown> = {};
  try {
    if (fs.existsSync(file)) {
      existing = JSON.parse(fs.readFileSync(file, 'utf8')) as Record<string, unknown>;
    }
  } catch {
    existing = {};
  }
  const label =
    typeof existing.label === 'string' && existing.label
      ? existing.label
      : `${os.hostname()} Founder IDE`;
  const merged = {
    ...existing,
    version: 1 as const,
    apiBaseUrl,
    nodeId: authorized.nodeId,
    nodeToken: authorized.nodeToken,
    label,
    pairedAt: new Date().toISOString(),
    founderId: authorized.founderId,
    installId: authorized.installId ?? install.installId,
    ipcSecret: install.ipcSecret,
    tokenRotatedAt: new Date().toISOString(),
    ...(authorized.tokenExpiresAt ? { tokenExpiresAt: authorized.tokenExpiresAt } : {}),
  };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(merged, null, 2), 'utf8');
  return {
    apiBaseUrl,
    founderId: authorized.founderId,
    nodeId: authorized.nodeId,
    nodeToken: authorized.nodeToken,
    tokenExpiresAt: authorized.tokenExpiresAt,
    installId: authorized.installId ?? install.installId,
  };
}

/**
 * Drive the full device-code flow interactively:
 *   1. Show a QuickPick confirming "Sign in with Founder ID".
 *   2. Request a grant + show userCode in a notification with "Open browser".
 *   3. Poll until authorized / expired / denied / cancelled.
 *   4. On success: write config + return creds (caller refreshes state).
 *
 * Returns the WrittenCredentials on success, or null if the user cancelled
 * or the flow failed (an error notification is shown in that case).
 */
export async function runDeviceCodeSignIn(
  options: {
    requestTimeoutMs?: number;
    onProgress?: (msg: string) => void;
  } = {},
): Promise<WrittenCredentials | null> {
  const confirm = await vscode.window.showQuickPick(
    [
      { label: 'Continue with X', description: 'authorize this IDE on Founder OS' },
      { label: 'Cancel', description: 'do not pair' },
    ],
    { placeHolder: 'Founder OS — sign in' },
  );
  if (!confirm || confirm.label === 'Cancel') return null;

  const apiBaseUrl = resolveApiBaseUrl();
  const install = ensureInstallIdentity();

  let grant: DeviceCodeGrant;
  try {
    options.onProgress?.('Requesting device code…');
    grant = await requestDeviceCode(
      apiBaseUrl,
      install.installId,
      install.ipcSecret,
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    void vscode.window.showErrorMessage(`Founder OS sign-in failed: ${msg}`);
    return null;
  }

  // Show the user code prominently + offer the open-browser action.
  const open = await vscode.window.showInformationMessage(
    `Founder OS sign-in code: ${grant.userCode}`,
    'Open browser',
    'Copy code',
  );
  if (open === 'Open browser' && grant.verificationUriComplete) {
    void vscode.env.openExternal(vscode.Uri.parse(grant.verificationUriComplete));
  } else if (open === 'Copy code') {
    void vscode.env.clipboard.writeText(grant.userCode);
  }

  // Drive the polling loop with a cancellable progress notification.
  return vscode.window.withProgress<WrittenCredentials | null>(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Founder OS sign-in (${grant.userCode})`,
      cancellable: true,
    },
    async (progress, token) => {
      const totalMs = options.requestTimeoutMs ?? 15 * 60 * 1000; // RFC 8628 TTL
      const startedAt = Date.now();
      let waitMs = grant.interval * 1000;

      while (Date.now() - startedAt < totalMs) {
        if (token.isCancellationRequested) return null;
        progress.report({ message: 'Waiting for browser authorization…' });
        options.onProgress?.('Waiting for browser authorization…');
        await sleep(Math.min(waitMs, 5000), token);
        if (token.isCancellationRequested) return null;

        let outcome: PollOutcome;
        try {
          outcome = await pollDeviceCode(apiBaseUrl, grant.deviceCode, install);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          progress.report({ message: `error: ${msg}` });
          await sleep(5000, token);
          continue;
        }

        switch (outcome.kind) {
          case 'pending':
            waitMs = outcome.waitMs;
            continue;
          case 'slow_down':
            waitMs = outcome.waitMs;
            progress.report({ message: 'slowing down per server…' });
            continue;
          case 'authorized':
            return outcome.creds;
          case 'expired':
            void vscode.window.showWarningMessage(
              `Founder OS sign-in expired: ${outcome.message}`,
            );
            return null;
          case 'denied':
            void vscode.window.showWarningMessage(
              `Founder OS sign-in denied: ${outcome.message}`,
            );
            return null;
          case 'error':
            progress.report({ message: outcome.message });
            await sleep(5000, token);
            continue;
        }
      }
      void vscode.window.showWarningMessage('Founder OS sign-in timed out.');
      return null;
    },
  );
}

function sleep(ms: number, token?: vscode.CancellationToken): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    token?.onCancellationRequested(() => {
      clearTimeout(t);
      resolve();
    });
  });
}
