import { contextBridge, ipcRenderer } from 'electron';

export type PairDefaults = {
  apiBaseUrl: string;
  label: string;
};

export type PairInput = {
  apiBaseUrl: string;
  code: string;
  label: string;
};

/**
 * Phase 2 device-code grant returned by POST /api/founder-node/device-code.
 * Surfaced to the renderer so it can display the userCode + verification URL.
 */
export type DeviceGrant = {
  userCode: string;
  verificationUri: string;
  verificationUriComplete: string;
  expiresAt: string;
  interval: number;
  installId: string;
};

/**
 * Phase 2 normalized poll result. The main process maps RFC 8628 HTTP codes
 * to this high-level status so the renderer doesn't have to peek at HTTP
 * semantics. `interval` (seconds) is the wait the renderer should apply
 * before its next poll — main merges the slow_down Retry-After into it.
 */
export type PollResult =
  | { status: 'pending'; interval: number }
  | { status: 'slow_down'; interval: number }
  | { status: 'authorized' }
  | { status: 'expired'; error: string }
  | { status: 'denied'; error: string };

contextBridge.exposeInMainWorld('founderNodePair', {
  getDefaults: (): Promise<PairDefaults> => ipcRenderer.invoke('get-pair-defaults'),
  openSettings: (): Promise<void> => ipcRenderer.invoke('open-settings'),
  pair: (input: PairInput): Promise<void> => ipcRenderer.invoke('pair', input),

  // ─── Phase 2 — device-code flow ────────────────────────────────────────
  startDeviceCode: (): Promise<DeviceGrant> => ipcRenderer.invoke('start-device-code'),
  pollDeviceCode: (grant: DeviceGrant): Promise<PollResult> =>
    ipcRenderer.invoke('poll-device-code', grant),
  openUrl: (url: string): Promise<void> => ipcRenderer.invoke('open-url', url),
});
