/**
 * Phase 2 — pairing-state status bar for the Founder OS extension.
 *
 * Owns a single StatusBarItem that reflects the 6 canonical pairing states.
 * Refreshes on:
 *   - extension activation
 *   - node-config.json change (FileSystemWatcher)
 *   - gateway 401 responses (setLastUnauthorized)
 *   - heartbeat interval (15s tick)
 *
 * Click → opens the existing founderOs.manage command palette so the user
 * can sign in / re-pair / logout / revoke.
 */
import * as vscode from 'vscode';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  computeIdePairingState,
  pairingStateStatusBar,
  type IdePairingInputs,
} from './pairing-state';

/** Resolve apiBaseUrl + node-config.json fields, returning null if absent. */
function readConfigSummary(): {
  hasConfig: boolean;
  tokenExpiresAt: Date | null;
} {
  const file = path.join(os.homedir(), 'FounderVault', 'node-config.json');
  try {
    if (!fs.existsSync(file)) return { hasConfig: false, tokenExpiresAt: null };
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as {
      nodeId?: string;
      nodeToken?: string;
      tokenExpiresAt?: string;
    };
    if (typeof parsed.nodeId !== 'string' || typeof parsed.nodeToken !== 'string') {
      return { hasConfig: false, tokenExpiresAt: null };
    }
    let tokenExpiresAt: Date | null = null;
    if (typeof parsed.tokenExpiresAt === 'string') {
      const d = new Date(parsed.tokenExpiresAt);
      tokenExpiresAt = Number.isNaN(d.getTime()) ? null : d;
    }
    return { hasConfig: true, tokenExpiresAt };
  } catch {
    return { hasConfig: false, tokenExpiresAt: null };
  }
}

export class PairingStatusBar {
  /** ms between heartbeat refreshes. */
  private static readonly HEARTBEAT_INTERVAL_MS = 15_000;

  private readonly item: vscode.StatusBarItem;
  private readonly disposables: vscode.Disposable[] = [];
  private pairingInProgress = false;
  private lastOkAt: Date | null = null;
  private lastWasUnauthorized = false;
  private heartbeatTimer: NodeJS.Timeout | null = null;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
    this.item.command = 'founderOs.manage';
    this.disposables.push(this.item);

    // Watch node-config.json so pairing done by the tray (or another
    // extension window) reflects here immediately.
    try {
      const dir = path.join(os.homedir(), 'FounderVault');
      const pattern = new vscode.RelativePattern(vscode.Uri.file(dir), 'node-config.json');
      const watcher = vscode.workspace.createFileSystemWatcher(pattern, false, false, false);
      this.disposables.push(watcher);
      this.disposables.push(watcher.onDidCreate(() => this.refresh()));
      this.disposables.push(watcher.onDidChange(() => this.refresh()));
      this.disposables.push(watcher.onDidDelete(() => this.refresh()));
    } catch {
      /* filesystem watching can fail on some platforms — non-fatal */
    }
  }

  /** Begin the heartbeat refresh loop. */
  start(): void {
    this.refresh();
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => this.refresh(), PairingStatusBar.HEARTBEAT_INTERVAL_MS);
  }

  /** Mark that a sign-in flow is in flight (toggles the "Signing in…" state). */
  setPairingInProgress(inProgress: boolean): void {
    this.pairingInProgress = inProgress;
    this.refresh();
  }

  /** Mark the most recent gateway call result (ok / unauthorized). */
  setGatewayResult(kind: 'ok' | 'unauthorized'): void {
    if (kind === 'ok') {
      this.lastOkAt = new Date();
      this.lastWasUnauthorized = false;
    } else {
      this.lastWasUnauthorized = true;
    }
    this.refresh();
  }

  /** Recompute the state and update the status bar text/tooltip. */
  refresh(): void {
    const summary = readConfigSummary();
    const inputs: IdePairingInputs = {
      pairingInProgress: this.pairingInProgress,
      hasConfig: summary.hasConfig,
      tokenExpiresAt: summary.tokenExpiresAt,
      lastOkAt: this.lastOkAt,
      lastWasUnauthorized: this.lastWasUnauthorized,
    };
    const state = computeIdePairingState(inputs);
    const { text, tooltip } = pairingStateStatusBar(state);
    this.item.text = text;
    this.item.tooltip = tooltip;
    this.item.show();
  }

  dispose(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    for (const d of this.disposables) d.dispose();
    this.item.dispose();
  }
}
