import type { DesktopBridgeInput, DeviceMemoryPayload, FounderOsTasksFile } from '@dcf/utils';

export const FOUNDER_VAULT_SCHEMA_VERSION = 1 as const;
export type FounderVaultMeta = {
  version: typeof FOUNDER_VAULT_SCHEMA_VERSION;
  createdAt: string;
  updatedAt: string;
  projectName?: string;
  nodeId?: string;
  pairedAt?: string | null;
};

export type FounderNodeConfig = {
  version: 1;
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
  label: string;
  pairedAt: string;
  ollama?: FounderNodeOllamaConfig;
  /** Phase 5 — Founder Cloud local stack on this machine. */
  founderCloud?: FounderCloudMode;
  /**
   * Phase 2 — user-level identity that owns this node. A Founder ID is a
   * revocable user identity issued by the Founder OS API; a node is a device
   * authorized by that Founder ID. Older configs written before Phase 2 omit
   * this field — callers must treat missing `founderId` as "legacy paired
   * node, upgrade path is to re-pair via device-code flow". Read access is
   * "optional but expected" — never throw on absence, but surface a "re-pair
   * to claim your Founder ID" prompt when it's absent.
   */
  founderId?: string;
  /**
   * Phase 3 — per-install IPC secret + install ID. Generated at pair time,
   * stored alongside the node identity. The install ID names the Windows
   * named pipe (\\.\pipe\founder-ide-{installId}); the IPC secret is a
   * 32+ byte random value the IDE extension must present during the
   * handshake before the adapter dispatches commands. Absent on legacy
   * configs — the IDE IPC server falls back to a fresh install ID on
   * first launch and the adapter reports isConnected() === false until
   * the user re-pairs.
   */
  installId?: string;
  ipcSecret?: string;
  /**
   * Phase 2 — ISO timestamp when the nodeToken expires server-side. Founder
   * Node auto-rotates when within ROTATION_WINDOW_MS of this time. Absent on
   * legacy tokens (treated as "no expiry, rotate on 401").
   */
  tokenExpiresAt?: string;
  /** Phase 2 — ISO timestamp of the last successful token rotation. */
  tokenRotatedAt?: string;
};

export type FounderCloudMode = {
  enabled: boolean;
  repoPath?: string;
  stackRunning?: boolean;
  webUrl?: string;
  apiUrl?: string;
  lastStartedAt?: string;
  lastError?: string;
};

export type FounderNodeHeartbeat = {
  nodeId: string;
  label: string;
  platform: string;
  appVersion: string;
  ramGb?: number;
  storageGb?: number;
  storageFreeGb?: number;
  vaultHealthy: boolean;
  vaultPath?: string;
  ollamaEnabled?: boolean;
  ollamaBaseUrl?: string;
  ollamaModel?: string;
  /** Metadata-only IDE context (branch, file names, task label — no file contents). */
  desktopBridge?: DesktopBridgeInput;
  /** Phase 5 — local Founder Cloud stack status from tray. */
  founderCloud?: FounderCloudMode;
};

export type FounderNodeOllamaConfig = {
  enabled: boolean;
  baseUrl: string;
  model: string;
};

export type FounderNodePairRequest = {
  code: string;
  nodeId: string;
  label: string;
  platform?: string;
  appVersion?: string;
  /**
   * Phase 3 — install ID for the named-pipe IPC channel. Generated client-side
   * at first launch and stored in node-config.json; the API records it so the
   * FounderIdeAdapter can look up the pipe path by nodeId. Optional on the
   * pair request for backwards compatibility (legacy nodes have no IPC).
   */
  installId?: string;
};

export type FounderNodePairResponse = {
  nodeToken: string;
  nodeId: string;
  userId: string;
  /**
   * Phase 2 — the Founder ID that owns this node. Equal to userId in the
   * current implementation (one founder per user) but kept as a distinct
   * field so we can later separate "user" (login identity) from "founder"
   * (the revocable device-authorization identity). Legacy clients that
   * don't read this field still pair successfully.
   */
  founderId: string;
  /**
   * Phase 2 — ISO timestamp when the nodeToken expires. Founder Node
   * auto-rotates within 7 days of this time. Absent if the deployment
   * doesn't expire tokens (some self-hosted setups).
   */
  tokenExpiresAt?: string;
  /** Phase 3 — echoed install ID so the client can confirm registration. */
  installId?: string;
};

export type FounderNodeStatusRow = {
  id: string;
  nodeId: string;
  label: string;
  status: 'online' | 'offline';
  lastSeenAt: string | null;
  ramGb: number | null;
  storageGb: number | null;
  storageFreeGb: number | null;
  vaultHealthy: boolean;
  platform: string | null;
  appVersion: string | null;
};

export type FounderNodePairingCodeResponse = {
  code: string;
  expiresAt: string;
};

export type FounderVaultSnapshot = DeviceMemoryPayload & {
  vaultHealthy: boolean;
  tasksRemaining?: number;
};

export function emptyTasksFile(currentGoal: string): FounderOsTasksFile {
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    currentGoal,
    tasks: [],
  };
}

export function parseTasksJson(raw: string): FounderOsTasksFile | null {
  try {
    const parsed = JSON.parse(raw) as FounderOsTasksFile;
    if (parsed?.version !== 1 || !Array.isArray(parsed.tasks)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function founderNodeAuthHeader(nodeId: string, nodeToken: string): string {
  return `FounderNode ${nodeId}:${nodeToken}`;
}

// ---------------------------------------------------------------------------
// Phase 2 — device-code (RFC 8628) first-run flow
// ---------------------------------------------------------------------------

/**
 * RFC 8628 device-authorization grant response shape. Returned by
 * POST /api/founder-node/device-code. The Founder Node tray displays
 * `userCode` + `verificationUri` and polls /device-code/poll with
 * `deviceCode` until status === 'authorized'.
 */
export type DeviceCodeGrant = {
  deviceCode: string;
  userCode: string;
  /** Browser URL the user visits to enter the userCode. */
  verificationUri: string;
  /** Full URL with ?user_code= appended (RFC 8628 §3.3.1) — ready to open. */
  verificationUriComplete?: string;
  expiresAt: string;
  /** Polling interval in seconds — clients must wait this long between polls. */
  interval: number;
};

export type DeviceCodePollStatus = 'pending' | 'expired' | 'denied' | 'authorized' | 'slow_down';

export type DeviceCodePollResponse =
  | { status: 'pending' | 'slow_down'; interval: number }
  | { status: 'expired'; error: string }
  | { status: 'denied'; error: string }
  | {
      status: 'authorized';
      founderId: string;
      nodeId: string;
      nodeToken: string;
      tokenExpiresAt?: string;
      installId?: string;
    };

// ---------------------------------------------------------------------------
// Phase 2 — explicit pairing state (tray + extension)
// ---------------------------------------------------------------------------

export type PairingState =
  | 'not_paired'
  | 'pairing'
  | 'paired_gateway_unreachable'
  | 'connected'
  | 'token_expired'
  | 'revoked';

// ---------------------------------------------------------------------------
// Phase 2 — token lifecycle endpoints
// ---------------------------------------------------------------------------

export type RotateTokenResponse = {
  nodeId: string;
  nodeToken: string;
  founderId: string;
  tokenExpiresAt?: string;
  tokenRotatedAt: string;
};

export type RevokeNodeResponse = {
  nodeId: string;
  founderId: string;
  revokedAt: string;
};

export type LogoutResponse = {
  nodeId: string;
  founderId: string;
  loggedOutAt: string;
  /** Server-side identity remains revocable separately. */
  serverSideRevocable: true;
};

/**
 * Parse Founder Node credentials from an Authorization header.
 *
 * Accepted forms (docs + IDE clients use both):
 *   - `FounderNode {nodeId}:{nodeToken}`  (Node tray / sync clients)
 *   - `Bearer fos_{nodeId}:{nodeToken}`   (OpenAI-compat IDE / extension)
 */
export function parseFounderNodeAuthHeader(
  header: string | undefined,
): { nodeId: string; nodeToken: string } | null {
  if (!header) return null;
  const trimmed = header.trim();

  let creds: string | null = null;
  if (trimmed.startsWith('FounderNode ')) {
    creds = trimmed.slice('FounderNode '.length).trim();
  } else if (/^Bearer\s+/i.test(trimmed)) {
    const token = trimmed.replace(/^Bearer\s+/i, '').trim();
    if (token.startsWith('fos_')) {
      creds = token.slice('fos_'.length);
    }
  }

  if (!creds) return null;
  const colon = creds.indexOf(':');
  if (colon <= 0) return null;
  const nodeId = creds.slice(0, colon);
  const nodeToken = creds.slice(colon + 1);
  if (!nodeId || !nodeToken) return null;
  return { nodeId, nodeToken };
}
