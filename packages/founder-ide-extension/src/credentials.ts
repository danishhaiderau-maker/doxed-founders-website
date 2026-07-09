/**
 * Credential discovery for the Founder OS chat provider.
 *
 * Resolution order (first non-empty wins per field):
 *   1. `founderOs.apiBaseUrl` / `founderOs.nodeId` / `founderOs.nodeToken` settings
 *   2. `~/FounderVault/node-config.json`  (written by Founder Node on pair)
 *
 * The bearer token format is `fos_{nodeId}:{nodeToken}` — exactly what the
 * API's `FounderNodeGuard` accepts, and what `connect-ide.ts` writes into
 * Cursor's settings.json. See `apps/founder-node/src/connect-ide.ts`.
 */
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as vscode from 'vscode';

export interface FounderOsCredentials {
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
  /** Where the credentials came from — surfaced in the status bar tooltip. */
  source: 'settings' | 'vault' | 'mixed';
}

export interface NodeConfigFile {
  version: 1;
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
  label?: string;
  pairedAt?: string;
}

/** Path to the Founder Node vault config — `~/FounderVault/node-config.json`. */
export function nodeConfigPath(): string {
  return path.join(os.homedir(), 'FounderVault', 'node-config.json');
}

function readVaultConfig(): NodeConfigFile | null {
  const file = nodeConfigPath();
  try {
    if (!fs.existsSync(file)) return null;
    const raw = fs.readFileSync(file, 'utf8');
    const parsed = JSON.parse(raw) as NodeConfigFile;
    if (
      typeof parsed.apiBaseUrl !== 'string' ||
      typeof parsed.nodeId !== 'string' ||
      typeof parsed.nodeToken !== 'string'
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function nonEmpty(s: string | undefined): s is string {
  return typeof s === 'string' && s.trim().length > 0;
}

/**
 * Resolve credentials from settings first, then the vault file.
 * Returns `null` if we don't have enough to authenticate a request.
 */
export function resolveCredentials(): FounderOsCredentials | null {
  const cfg = vscode.workspace.getConfiguration('founderOs');
  const settingsUrl = cfg.get<string>('apiBaseUrl');
  const settingsNodeId = cfg.get<string>('nodeId');
  const settingsNodeToken = cfg.get<string>('nodeToken');

  const vault = readVaultConfig();

  const apiBaseUrl = nonEmpty(settingsUrl) ? settingsUrl! : vault?.apiBaseUrl ?? '';
  const nodeId = nonEmpty(settingsNodeId) ? settingsNodeId! : vault?.nodeId ?? '';
  const nodeToken = nonEmpty(settingsNodeToken) ? settingsNodeToken! : vault?.nodeToken ?? '';

  if (!apiBaseUrl || !nodeId || !nodeToken) return null;

  const usedSettings = nonEmpty(settingsUrl) || nonEmpty(settingsNodeId) || nonEmpty(settingsNodeToken);
  const usedVault = vault !== null;
  const source: FounderOsCredentials['source'] = usedSettings && usedVault ? 'mixed' : usedSettings ? 'settings' : 'vault';

  return { apiBaseUrl, nodeId, nodeToken, source };
}

/** Builds the OpenAI-compat proxy base URL: `{apiBaseUrl}/api/v1`. */
export function proxyBaseUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/$/, '')}/api/v1`;
}

/** Builds the `Authorization: Bearer fos_{nodeId}:{nodeToken}` header value. */
export function bearerFromCredentials(creds: FounderOsCredentials): string {
  return `fos_${creds.nodeId}:${creds.nodeToken}`;
}

/** True if the vault file is present on disk (regardless of whether it parses). */
export function vaultFileExists(): boolean {
  try {
    return fs.existsSync(nodeConfigPath());
  } catch {
    return false;
  }
}
