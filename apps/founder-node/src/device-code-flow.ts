/**
 * Phase 2 device-code (RFC 8628) client for the Founder Node tray.
 *
 * Talks to the Founder OS API:
 *   POST /api/founder-node/device-code         (anonymous grant)
 *   POST /api/founder-node/device-code/poll    (status: pending/authorized/
 *                                              expired/denied/slow_down)
 *
 * The renderer (pair.js) drives the polling loop via the preload bridge.
 * This module is the single place that knows the HTTP shape, so tests can
 * exercise it with a stubbed fetch and the renderer doesn't have to.
 *
 * On `authorized`, callers receive an `AuthorizedPair` to write into
 * node-config.json alongside the installId / ipcSecret that were presented
 * in the original grant request.
 */
import { randomBytes, randomUUID } from 'node:crypto';

/** RFC 8628 grant shape returned by POST /device-code. */
export interface DeviceCodeGrant {
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete?: string;
  expiresAt: string;
  interval: number;
}

/**
 * The shape the renderer wants: the fields it needs to display the userCode
 * + drive the polling loop. The `deviceCode` is the secret the main process
 * uses when calling /poll — it is NOT exposed to the renderer.
 */
export interface DeviceCodeRendererGrant {
  userCode: string;
  verificationUri: string;
  verificationUriComplete: string;
  expiresAt: string;
  interval: number;
  /** Echoed so the renderer can show the install id it's bound to. */
  installId: string;
}

/** Normalized poll result surfaced to the renderer. */
export type DeviceCodePollRendererResult =
  | { status: 'pending'; interval: number }
  | { status: 'slow_down'; interval: number }
  | { status: 'authorized' }
  | { status: 'expired'; error: string }
  | { status: 'denied'; error: string };

/** On authorized, the API returns these fields; main writes them to disk. */
export interface AuthorizedPair {
  founderId: string;
  nodeId: string;
  nodeToken: string;
  tokenExpiresAt?: string;
  installId?: string;
}

function apiBase(apiBaseUrl: string, p: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${p.startsWith('/') ? p : `/${p}`}`;
}

/** Generate a fresh install ID — UUIDv4. */
export function newInstallId(): string {
  // randomUUID is available on Node 14.17+ and Electron ≥ 14.
  return randomUUID();
}

/** Generate a fresh per-install IPC secret — 32 random bytes hex. */
export function newIpcSecret(): string {
  return randomBytes(32).toString('hex');
}

/**
 * Start a device-code grant. Calls POST /api/founder-node/device-code with
 * the install's installId (so authorize can pair a node bound to this
 * install's named-pipe IPC name).
 */
export async function requestDeviceCode(
  apiBaseUrl: string,
  installId: string,
): Promise<{ grant: DeviceCodeGrant; installId: string }> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/device-code'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ installId }),
  });
  const body = (await res.json().catch(() => null)) as (DeviceCodeGrant & {
    message?: string | string[];
  }) | null;
  if (!res.ok || !body) {
    const msg = Array.isArray(body?.message) ? body.message.join(', ') : body?.message;
    throw new Error(msg ?? `Failed to start device-code flow (${res.status})`);
  }
  return { grant: body, installId };
}

/**
 * Poll a device-code grant. Returns a normalized result so callers don't
 * have to interpret HTTP codes themselves. Translates:
 *   200 + status='authorized' → authorized (with token fields)
 *   202 + status='pending'     → pending
 *   400 + status='expired'     → expired
 *   403 + status='denied'      → denied
 *   429 + status='slow_down'   → slow_down (interval picked up from body or
 *                                Retry-After header)
 *
 * Returns either the normalized renderer-shape OR — when authorized — the
 * full AuthorizedPair plus a renderer-shape. Callers fan-out accordingly.
 */
export async function pollDeviceCode(
  apiBaseUrl: string,
  deviceCode: string,
): Promise<
  | { kind: 'renderer'; result: DeviceCodePollRendererResult }
  | { kind: 'authorized'; result: DeviceCodePollRendererResult; pair: AuthorizedPair }
> {
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

  if (!body) {
    return {
      kind: 'renderer',
      result: { status: 'expired', error: `Empty response from server (${res.status})` },
    };
  }

  // slow_down: prefer the server-provided interval (already bumped per
  // RFC 8628 §3.5); fall back to Retry-After header + 5s, then 10s default.
  if (body.status === 'slow_down') {
    const retryAfter = Number(res.headers.get('retry-after') ?? 0);
    const interval = body.interval ?? (Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 10);
    return { kind: 'renderer', result: { status: 'slow_down', interval } };
  }

  if (body.status === 'pending') {
    const interval = body.interval ?? 5;
    return { kind: 'renderer', result: { status: 'pending', interval } };
  }

  if (body.status === 'expired') {
    return {
      kind: 'renderer',
      result: { status: 'expired', error: body.error ?? 'Device code expired' },
    };
  }

  if (body.status === 'denied') {
    return {
      kind: 'renderer',
      result: { status: 'denied', error: body.error ?? 'Authorization denied' },
    };
  }

  // authorized — must have all four identity fields. If the server returned
  // 200 but body is missing them (already-consumed grant), treat as expired.
  if (body.status === 'authorized') {
    if (!body.founderId || !body.nodeId || !body.nodeToken) {
      return {
        kind: 'renderer',
        result: { status: 'expired', error: 'Authorized grant has no token (already consumed)' },
      };
    }
    const pair: AuthorizedPair = {
      founderId: body.founderId,
      nodeId: body.nodeId,
      nodeToken: body.nodeToken,
      tokenExpiresAt: body.tokenExpiresAt,
      installId: body.installId,
    };
    return { kind: 'authorized', result: { status: 'authorized' }, pair };
  }

  return {
    kind: 'renderer',
    result: { status: 'expired', error: `Unknown status from server: ${body.status}` },
  };
}

/**
 * Server-side logout (records logout timestamp, server-side identity stays
 * revocable separately). Best-effort — callers should drop node-config.json
 * regardless of the response.
 */
export async function postLogout(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
): Promise<void> {
  try {
    await fetch(apiBase(apiBaseUrl, '/api/founder-node/logout'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // FounderNodeGuard expects `Authorization: FounderNode {nodeId}:{nodeToken}`.
        Authorization: `FounderNode ${nodeId}:${nodeToken}`,
      },
      body: JSON.stringify({}),
    });
  } catch {
    /* best-effort */
  }
}

/** Server-side revoke — permanent. Best-effort. */
export async function postRevoke(
  apiBaseUrl: string,
  nodeId: string,
): Promise<void> {
  try {
    await fetch(apiBase(apiBaseUrl, '/api/founder-node/revoke'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodeId }),
    });
  } catch {
    /* best-effort */
  }
}

/**
 * Rotate the nodeToken. Requires the current valid token. Returns the new
 * token (and updated expiry) so the caller can write them to node-config.json.
 */
export async function postRotateToken(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
): Promise<{ nodeId: string; nodeToken: string; tokenExpiresAt?: string; tokenRotatedAt: string }> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/rotate-token'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `FounderNode ${nodeId}:${nodeToken}`,
    },
    body: JSON.stringify({}),
  });
  const body = (await res.json().catch(() => null)) as {
    nodeId?: string;
    nodeToken?: string;
    tokenExpiresAt?: string;
    tokenRotatedAt?: string;
    message?: string | string[];
  } | null;
  if (!res.ok || !body?.nodeToken) {
    const msg = Array.isArray(body?.message) ? body.message.join(', ') : body?.message;
    throw new Error(msg ?? `Token rotation failed (${res.status})`);
  }
  return {
    nodeId: body.nodeId!,
    nodeToken: body.nodeToken,
    tokenExpiresAt: body.tokenExpiresAt,
    tokenRotatedAt: body.tokenRotatedAt ?? new Date().toISOString(),
  };
}
