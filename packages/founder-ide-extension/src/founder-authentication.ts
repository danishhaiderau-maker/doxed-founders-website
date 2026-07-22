import * as vscode from 'vscode';
import {
  authorizationHeaderFromCredentials,
  clearFounderSession,
  readVaultConfig,
  resolveCredentials,
  syncVaultIntoSettings,
} from './credentials';
import { runDeviceCodeSignIn } from './device-code-sign-in';

export const FOUNDER_AUTH_PROVIDER_ID = 'founderOs';

export interface FounderAuthenticationHooks {
  onDidSignIn(): Promise<void> | void;
  onDidSignOut(): Promise<void> | void;
}

export class FounderAuthenticationProvider
  implements vscode.AuthenticationProvider, vscode.Disposable
{
  private readonly sessionChanges =
    new vscode.EventEmitter<vscode.AuthenticationProviderAuthenticationSessionsChangeEvent>();
  private lastSession: vscode.AuthenticationSession | null = null;

  readonly onDidChangeSessions = this.sessionChanges.event;

  constructor(private readonly hooks: FounderAuthenticationHooks) {
    this.lastSession = this.readSession();
  }

  async getSessions(
    _scopes: readonly string[] | undefined,
    _options: vscode.AuthenticationProviderSessionOptions,
  ): Promise<vscode.AuthenticationSession[]> {
    const session = this.readSession();
    this.lastSession = session;
    return session ? [session] : [];
  }

  async createSession(
    _scopes: readonly string[],
  ): Promise<vscode.AuthenticationSession> {
    const previous = this.readSession();
    const credentials = await runDeviceCodeSignIn();
    if (!credentials) throw new vscode.CancellationError();

    await syncVaultIntoSettings();
    const session = this.readSession();
    if (!session) {
      throw new Error('Founder sign-in completed without a local session.');
    }

    this.lastSession = session;
    this.sessionChanges.fire(
      previous
        ? { added: [], removed: [], changed: [session] }
        : { added: [session], removed: [], changed: [] },
    );
    await this.hooks.onDidSignIn();
    return session;
  }

  async removeSession(sessionId: string): Promise<void> {
    const session = this.readSession();
    if (!session || session.id !== sessionId) return;

    const credentials = resolveCredentials();
    if (credentials) {
      try {
        await fetch(
          `${credentials.apiBaseUrl.replace(/\/$/, '')}/api/founder-node/logout`,
          {
            method: 'POST',
            headers: {
              Authorization: authorizationHeaderFromCredentials(credentials),
            },
          },
        );
      } catch {
        // Sign-out is local-first. The website can still revoke the node later.
      }
    }

    await clearFounderSession();
    this.lastSession = null;
    this.sessionChanges.fire({
      added: [],
      removed: [session],
      changed: [],
    });
    await this.hooks.onDidSignOut();
  }

  refresh(): void {
    const next = this.readSession();
    const previous = this.lastSession;
    if (previous?.id === next?.id && previous?.accessToken === next?.accessToken) {
      return;
    }

    this.lastSession = next;
    this.sessionChanges.fire({
      added: next && !previous ? [next] : [],
      removed: previous && !next ? [previous] : [],
      changed: next && previous ? [next] : [],
    });
  }

  async signOut(): Promise<boolean> {
    const session = this.readSession();
    if (!session) return false;
    await this.removeSession(session.id);
    return true;
  }

  dispose(): void {
    this.sessionChanges.dispose();
  }

  private readSession(): vscode.AuthenticationSession | null {
    const config = readVaultConfig();
    if (!config) return null;

    const accountId = config.founderId?.trim() || config.nodeId;
    const accountLabel = config.founderId?.trim() || 'Doxxed account';
    return {
      id: `founder:${config.nodeId}`,
      accessToken: config.nodeToken,
      account: {
        id: accountId,
        label: accountLabel,
      },
      scopes: ['founder'],
    };
  }
}
