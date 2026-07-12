/**
 * Credential discovery for the Founder OS chat provider.
 *
 * Resolution order (first non-empty wins per field):
 *   1. `founderOs.apiBaseUrl` / `founderOs.nodeId` / `founderOs.nodeToken` settings
 *   2. `~/FounderVault/node-config.json`  (written by Founder Node on pair)
 *
 * The bearer token format is `fos_{nodeId}:{nodeToken}` — exactly what the
 * API's `FounderNodeGuard` accepts, and what `connect-ide.ts` writes into
 * Cursor / Founder IDE settings.json. See `apps/founder-node/src/connect-ide.ts`.
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

export function readVaultConfig(): NodeConfigFile | null {
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

  const rawUrl = nonEmpty(settingsUrl) ? settingsUrl! : vault?.apiBaseUrl ?? '';
  const apiBaseUrl = rawUrl ? normalizeApiBaseUrl(rawUrl) : '';
  const nodeId = nonEmpty(settingsNodeId) ? settingsNodeId! : vault?.nodeId ?? '';
  const nodeToken = nonEmpty(settingsNodeToken) ? settingsNodeToken! : vault?.nodeToken ?? '';

  if (!apiBaseUrl || !nodeId || !nodeToken) return null;

  const usedSettings = nonEmpty(settingsUrl) || nonEmpty(settingsNodeId) || nonEmpty(settingsNodeToken);
  const usedVault = vault !== null;
  const source: FounderOsCredentials['source'] = usedSettings && usedVault ? 'mixed' : usedSettings ? 'settings' : 'vault';

  return { apiBaseUrl, nodeId, nodeToken, source };
}

/**
 * Normalize vault / marketing host to the API host used by the gateway.
 * `https://doxxedcrypto.digital` → `https://api.doxxedcrypto.digital`
 */
export function normalizeApiBaseUrl(url: string): string {
  const trimmed = url.replace(/\/$/, '');
  try {
    const u = new URL(trimmed);
    if (u.hostname === 'doxxedcrypto.digital' || u.hostname === 'www.doxxedcrypto.digital') {
      u.hostname = 'api.doxxedcrypto.digital';
      return u.origin;
    }
  } catch {
    // fall through
  }
  return trimmed;
}

/**
 * Copy vault credentials into `founderOs.*` (+ OpenAI-compat) User settings so
 * pairing survives vault moves and Skycode/OpenAI providers can reuse the same
 * Node bearer without a pairing-code prompt. Best-effort.
 */
export async function syncVaultIntoSettings(): Promise<FounderOsCredentials | null> {
  const vault = readVaultConfig();
  if (!vault) return null;

  const apiBaseUrl = normalizeApiBaseUrl(vault.apiBaseUrl);
  const bearer = `fos_${vault.nodeId}:${vault.nodeToken}`;
  const openaiBase = `${apiBaseUrl}/v1`;

  const cfg = vscode.workspace.getConfiguration('founderOs');
  try {
    await cfg.update('apiBaseUrl', apiBaseUrl, vscode.ConfigurationTarget.Global);
    await cfg.update('nodeId', vault.nodeId, vscode.ConfigurationTarget.Global);
    await cfg.update('nodeToken', vault.nodeToken, vscode.ConfigurationTarget.Global);
  } catch {
    // Settings write can fail in restricted / remote hosts — vault still works.
  }

  // Best-effort OpenAI-compat keys for Skycode / other providers in Founder IDE.
  try {
    const root = vscode.workspace.getConfiguration();
    await root.update('openAICompatible.apiUrl', openaiBase, vscode.ConfigurationTarget.Global);
    await root.update('openAICompatible.apiKey', bearer, vscode.ConfigurationTarget.Global);
  } catch {
    // optional
  }

  return {
    apiBaseUrl,
    nodeId: vault.nodeId,
    nodeToken: vault.nodeToken,
    source: 'settings',
  };
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
