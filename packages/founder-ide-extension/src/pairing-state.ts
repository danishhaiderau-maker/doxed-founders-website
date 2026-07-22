/**
 * Phase 2 — IDE-side pairing state computation.
 *
 * Mirrors the tray's pairing-state.ts but reads from VS Code settings +
 * node-config.json + extension-level runtime signals (last gateway result).
 * Pure / testable: takes the inputs, returns one of the 6 canonical states
 * + the icon/tooltip the status bar should display.
 */
import type { PairingState } from '@dcf/founder-vault';

export interface IdePairingInputs {
  /** True when the user is in the device-code flow (signIn command in flight). */
  pairingInProgress: boolean;
  /** True when node-config.json exists with valid shape. */
  hasConfig: boolean;
  /** Parsed tokenExpiresAt from node-config.json, or null. */
  tokenExpiresAt: Date | null;
  /** ISO timestamp of the last successful gateway response, or null. */
  lastOkAt: Date | null;
  /** True when the last gateway call returned 401 Unauthorized. */
  lastWasUnauthorized: boolean;
}

/**
 * Compute the pairing state. Pure: takes inputs, returns a PairingState.
 *
 * Decision order:
 *   1. revoked             — last gateway call 401'd and config is gone
 *   2. not_paired          — no config at all
 *   3. pairing             — sign-in flow in flight
 *   4. token_expired       — config present but tokenExpiresAt is in the past
 *   5. paired_gateway_unreachable — config present + last call failed (non-auth)
 *   6. connected           — config present + last call ok (or never tried yet)
 */
export function computeIdePairingState(inputs: IdePairingInputs): PairingState {
  const { pairingInProgress, hasConfig, tokenExpiresAt, lastOkAt, lastWasUnauthorized } = inputs;

  if (lastWasUnauthorized && !hasConfig) return 'revoked';
  if (!hasConfig) return 'not_paired';
  if (pairingInProgress) return 'pairing';

  if (tokenExpiresAt && tokenExpiresAt.getTime() < Date.now()) {
    return 'token_expired';
  }
  if (lastWasUnauthorized) return 'paired_gateway_unreachable';
  if (lastOkAt) return 'connected';

  // Just paired, no gateway traffic yet — optimistically show connected.
  return 'connected';
}

/**
 * The `$(...)` codicon + text the status bar shows. Click opens the manage
 * QuickPick.
 */
export function pairingStateStatusBar(state: PairingState): { text: string; tooltip: string } {
  switch (state) {
    case 'not_paired':
      return {
        text: '$(warning) Founder OS: Sign in',
        tooltip: 'Founder OS not configured. Click to sign in with Founder ID.',
      };
    case 'pairing':
      return {
        text: '$(sync~spin) Founder OS: Signing in…',
        tooltip: 'Founder OS device-code sign-in in progress.',
      };
    case 'paired_gateway_unreachable':
      return {
        text: '$(warning) Founder OS: Gateway unreachable',
        tooltip: 'Founder OS paired but the last gateway call failed. Click to manage.',
      };
    case 'connected':
      return {
        text: '$(check) Founder OS: Connected',
        tooltip: 'Founder OS connected. Click to manage.',
      };
    case 'token_expired':
      return {
        text: '$(warning) Founder OS: Token expired',
        tooltip: 'Founder OS token has expired. Click to re-pair.',
      };
    case 'revoked':
      return {
        text: '$(error) Founder OS: Revoked',
        tooltip: 'Founder OS credentials were revoked. Click to re-pair.',
      };
    default: {
      const _exhaustive: never = state;
      void _exhaustive;
      return { text: 'Founder OS', tooltip: '' };
    }
  }
}
