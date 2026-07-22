/**
 * Phase 2 — pairing state machine for Founder Node.
 *
 * Six states mirror the contract in @dcf/founder-vault PairingState and the
 * IPC `IpcAuthStateValue`:
 *
 *   not_paired                  — no node-config.json (or token-less)
 *   pairing                     — device-code flow in progress
 *   paired_gateway_unreachable  — paired but last heartbeat failed (network)
 *   connected                   — paired + last heartbeat succeeded
 *   token_expired               — paired but token has expired (server 401)
 *   revoked                     — server returned 401 + cleared local creds
 *
 * Tray menu + tooltip + heartbeat derive from this. The state is computed
 * from the on-disk config plus runtime signals (lastSyncOkAt, lastSyncError,
 * authRecoveryHandled). It's deliberately pure so it can be unit-tested
 * without Electron.
 */
import type { FounderNodeConfig, PairingState } from '@dcf/founder-vault';

export interface PairingStateInputs {
  /** The on-disk node-config.json, or null when not paired. */
  config: FounderNodeConfig | null;
  /** True when the user is in the device-code flow (pair window open). */
  pairingInProgress: boolean;
  /** Last successful heartbeat timestamp, or null if never succeeded. */
  lastSyncOkAt: Date | null;
  /** Last sync error message, or null if last sync was clean. */
  lastSyncError: string | null;
  /** True when we've already wiped creds and shown the re-pair dialog. */
  authRecoveryHandled: boolean;
  /** Optional: token expiry from the on-disk config, parsed. */
  tokenExpiresAt?: Date | null;
}

/**
 * Compute the current pairing state. Pure: takes the runtime inputs and
 * returns one of the six canonical states.
 *
 * Decision order:
 *   1. revoked           — authRecoveryHandled (we wiped creds on a 401)
 *   2. not_paired        — no config at all
 *   3. pairing           — device-code flow in flight
 *   4. token_expired     — config present but tokenExpiresAt is in the past
 *   5. paired_gateway_unreachable — config present + last sync failed (network)
 *   6. connected         — config present + last sync ok (or never tried yet)
 */
export function computePairingState(inputs: PairingStateInputs): PairingState {
  const { config, pairingInProgress, lastSyncOkAt, lastSyncError, authRecoveryHandled } = inputs;

  if (authRecoveryHandled) return 'revoked';
  if (!config) return 'not_paired';
  if (pairingInProgress) return 'pairing';

  const expiresAt = inputs.tokenExpiresAt ?? parseTokenExpiry(config);
  if (expiresAt && expiresAt.getTime() < Date.now()) {
    return 'token_expired';
  }

  if (lastSyncError) return 'paired_gateway_unreachable';
  if (lastSyncOkAt) return 'connected';

  // Just paired, never synced yet — treat as connected optimistically; the
  // next heartbeat will either confirm it or flip to paired_gateway_unreachable.
  return 'connected';
}

function parseTokenExpiry(config: FounderNodeConfig): Date | null {
  if (!config.tokenExpiresAt) return null;
  try {
    const d = new Date(config.tokenExpiresAt);
    return Number.isNaN(d.getTime()) ? null : d;
  } catch {
    return null;
  }
}

/**
 * Human-readable label for the tray menu. The tray shows this in a disabled
 * menu item + the tooltip uses it as a prefix.
 */
export function pairingStateLabel(state: PairingState, config: FounderNodeConfig | null): string {
  switch (state) {
    case 'not_paired':
      return 'Not paired — sign in with Founder ID';
    case 'pairing':
      return 'Pairing in progress…';
    case 'paired_gateway_unreachable':
      return '⚠ Gateway unreachable';
    case 'connected':
      return config?.founderId
        ? `✓ Connected as ${config.founderId}`
        : `✓ Connected: ${config?.label ?? 'node'}`;
    case 'token_expired':
      return '⚠ Token expired — re-pair';
    case 'revoked':
      return '⚠ Node revoked — re-pair';
    default: {
      const _exhaustive: never = state;
      void _exhaustive;
      return '';
    }
  }
}

/** Short tooltip text — pairs with the app version string. */
export function pairingStateTooltip(state: PairingState, lastError: string | null): string {
  switch (state) {
    case 'not_paired':
      return 'Founder Node — click "Sign in with Founder ID" to pair.';
    case 'pairing':
      return 'Founder Node — pairing in progress.';
    case 'paired_gateway_unreachable':
      return `Founder Node — gateway unreachable${lastError ? `: ${lastError.slice(0, 80)}` : '.'}`;
    case 'connected':
      return 'Founder Node — connected.';
    case 'token_expired':
      return 'Founder Node — token expired. Re-pair via the tray menu.';
    case 'revoked':
      return 'Founder Node — node revoked. Re-pair via the tray menu.';
    default: {
      const _exhaustive: never = state;
      void _exhaustive;
      return '';
    }
  }
}
