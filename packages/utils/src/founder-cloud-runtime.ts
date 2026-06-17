/** Phase 5 — Founder Cloud local stack runtime metadata. */

export type FounderCloudMode = {
  enabled: boolean;
  repoPath?: string;
  stackRunning?: boolean;
  webUrl?: string;
  apiUrl?: string;
  lastStartedAt?: string;
  lastError?: string;
};

export const FOUNDER_CLOUD_DEFAULT_URLS = {
  web: 'http://127.0.0.1:3000',
  api: 'http://127.0.0.1:4000',
} as const;

export function parseFounderCloudMode(raw: unknown): FounderCloudMode | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  return {
    enabled: Boolean(o.enabled),
    repoPath: typeof o.repoPath === 'string' ? o.repoPath : undefined,
    stackRunning: typeof o.stackRunning === 'boolean' ? o.stackRunning : undefined,
    webUrl: typeof o.webUrl === 'string' ? o.webUrl : undefined,
    apiUrl: typeof o.apiUrl === 'string' ? o.apiUrl : undefined,
    lastStartedAt: typeof o.lastStartedAt === 'string' ? o.lastStartedAt : undefined,
    lastError: typeof o.lastError === 'string' ? o.lastError : undefined,
  };
}

export function missionControlLocalUrl(mode: FounderCloudMode | null): string | null {
  if (!mode?.enabled) return null;
  return mode.webUrl ?? (mode.stackRunning ? FOUNDER_CLOUD_DEFAULT_URLS.web : null);
}
