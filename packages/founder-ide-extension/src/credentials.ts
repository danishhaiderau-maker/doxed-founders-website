/**
 * Credential discovery for the Founder OS chat provider.
 *
 * The vault is authoritative for identity and credentials. Non-secret settings
 * may override the API URL; legacy node settings are accepted only when no
 * vault exists so older development installs can still migrate.
 *
 * The bearer token format is `fos_{nodeId}:{nodeToken}` (OpenAI-compat) or
 * `FounderNode {nodeId}:{nodeToken}` (preferred for gateway calls). See
 * `parseFounderNodeAuthHeader` in `@dcf/founder-vault` and `connect-ide.ts`.
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
  founderId?: string;
  installId?: string;
  ipcSecret?: string;
  tokenExpiresAt?: string;
  tokenRotatedAt?: string;
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
 * Resolve credentials from the vault, with a legacy settings fallback.
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
  const nodeId = vault?.nodeId
    ?? (nonEmpty(settingsNodeId) ? settingsNodeId : '');
  const nodeToken = vault?.nodeToken
    ?? (nonEmpty(settingsNodeToken) ? settingsNodeToken : '');

  if (!apiBaseUrl || !nodeId || !nodeToken) return null;

  const usedSettings = nonEmpty(settingsUrl)
    || (!vault && (nonEmpty(settingsNodeId) || nonEmpty(settingsNodeToken)));
  const usedVault = Boolean(vault);
  const source: FounderOsCredentials['source'] = usedSettings && usedVault ? 'mixed' : usedSettings ? 'settings' : 'vault';

  return { apiBaseUrl, nodeId, nodeToken, source };
}

/**
 * Normalize vault / marketing host to the API origin used by the gateway.
 *
 * Production serves the Nest API on the apex host (`https://doxxedcrypto.digital/api/...`).
 * Do **not** rewrite to `api.doxxedcrypto.digital` — that hostname has no DNS record.
 */
export function normalizeApiBaseUrl(url: string): string {
  const trimmed = url.replace(/\/$/, '');
  try {
    const u = new URL(trimmed);
    if (u.hostname === 'www.doxxedcrypto.digital') {
      u.hostname = 'doxxedcrypto.digital';
      return u.origin;
    }
    if (u.hostname === 'api.doxxedcrypto.digital') {
      // Legacy / mistaken settings — map back to the live apex API.
      u.hostname = 'doxxedcrypto.digital';
      return u.origin;
    }
  } catch {
    // fall through
  }
  return trimmed;
}

/**
 * Synchronize non-secret connection metadata and remove credentials written by
 * older builds. The vault remains the single local source of the node token.
 */
export async function syncVaultIntoSettings(): Promise<FounderOsCredentials | null> {
  const vault = readVaultConfig();
  if (!vault) return null;

  const apiBaseUrl = normalizeApiBaseUrl(vault.apiBaseUrl);

  const cfg = vscode.workspace.getConfiguration('founderOs');
  try {
    await cfg.update('apiBaseUrl', apiBaseUrl, vscode.ConfigurationTarget.Global);
    await cfg.update('nodeId', vault.nodeId, vscode.ConfigurationTarget.Global);
    const legacyToken = cfg.get<string>('nodeToken');
    if (legacyToken === vault.nodeToken || legacyToken?.startsWith('fn_')) {
      await cfg.update('nodeToken', undefined, vscode.ConfigurationTarget.Global);
    }
  } catch {
    // Settings writes can fail in restricted hosts. The vault remains authoritative.
  }

  // Older installers copied the Founder bearer into provider settings. The
  // native Founder route now reads the vault directly, so remove those copies.
  try {
    const root = vscode.workspace.getConfiguration();
    for (const key of ['openAICompatible.apiKey', 'openai.apiKey']) {
      const value = root.get<string>(key);
      if (value?.startsWith('fos_')) {
        await root.update(key, undefined, vscode.ConfigurationTarget.Global);
      }
    }
  } catch {
    // Best-effort migration. No new secret is written to settings.
  }

  return {
    apiBaseUrl,
    nodeId: vault.nodeId,
    nodeToken: vault.nodeToken,
    source: 'vault',
  };
}

/** Builds the OpenAI-compat proxy base URL: `{apiBaseUrl}/api/v1`. */
export function proxyBaseUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/$/, '')}/api/v1`;
}

/** OpenAI-compat apiKey value — clients send `Authorization: Bearer <this>`. */
export function bearerFromCredentials(creds: FounderOsCredentials): string {
  return `fos_${creds.nodeId}:${creds.nodeToken}`;
}

/** Full Authorization header for the Nest FounderNodeGuard (preferred). */
export function authorizationHeaderFromCredentials(
  creds: FounderOsCredentials,
): string {
  return `FounderNode ${creds.nodeId}:${creds.nodeToken}`;
}

/** True if the vault file is present on disk (regardless of whether it parses). */
export function vaultFileExists(): boolean {
  try {
    return fs.existsSync(nodeConfigPath());
  } catch {
    return false;
  }
}

/**
 * Remove the local Founder session while preserving unrelated local runtime
 * preferences that may share node-config.json.
 */
export async function clearFounderSession(): Promise<void> {
  const file = nodeConfigPath();
  try {
    if (fs.existsSync(file)) {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as Record<string, unknown>;
      const credentialKeys = [
        'apiBaseUrl',
        'nodeId',
        'nodeToken',
        'founderId',
        'installId',
        'ipcSecret',
        'pairedAt',
        'tokenExpiresAt',
        'tokenRotatedAt',
      ];
      for (const key of credentialKeys) delete parsed[key];

      const meaningfulKeys = Object.keys(parsed).filter(
        (key) => key !== 'version' && key !== 'label',
      );
      if (meaningfulKeys.length === 0) {
        fs.unlinkSync(file);
      } else {
        fs.writeFileSync(file, JSON.stringify(parsed, null, 2), 'utf8');
      }
    }
  } catch {
    // Local sign-out continues even if the optional vault cleanup fails.
  }

  const cfg = vscode.workspace.getConfiguration('founderOs');
  for (const key of ['apiBaseUrl', 'nodeId', 'nodeToken']) {
    try {
      await cfg.update(key, undefined, vscode.ConfigurationTarget.Global);
    } catch {
      // Restricted hosts can reject settings writes. The vault is authoritative.
    }
  }

  try {
    const root = vscode.workspace.getConfiguration();
    for (const [key, url] of [
      ['openAICompatible.apiKey', 'openAICompatible.apiUrl'],
      ['openai.apiKey', 'openai.apiBase'],
    ] as const) {
      const currentKey = root.get<string>(key);
      if (currentKey?.startsWith('fos_')) {
        await root.update(key, undefined, vscode.ConfigurationTarget.Global);
        await root.update(url, undefined, vscode.ConfigurationTarget.Global);
      }
    }
  } catch {
    // Best-effort cleanup for the compatibility provider.
  }
}
