/** Founder OS autopilot — sync stack, publish, redeploy, resume from memory. */

export type PlatformSyncKey =
  | 'github'
  | 'neon'
  | 'vercel'
  | 'railway'
  | 'memory'
  | 'cursor'
  | 'llm';

export type PlatformSyncItem = {
  key: PlatformSyncKey;
  label: string;
  connected: boolean;
  detail?: string;
  action?: 'connect' | 'sync' | 'ready';
};

export function detectAutopilotIntent(prompt: string): boolean {
  const p = prompt.trim().toLowerCase();
  if (!p) return false;
  return (
    /\b(take full control|full autopilot|autopilot mode|sync everything|push all updates)\b/i.test(
      p,
    ) ||
    /\b(sync|push|commit|deploy|publish).*(neon|vercel|railway|github|everywhere|all)\b/i.test(
      p,
    ) ||
    /\b(run autopilot|ship everything|push.*production)\b/i.test(p)
  );
}

export function memoryStoragePrivacyLabel(mode: string): string {
  switch (mode) {
    case 'LOCAL_DEVICE':
      return 'Memory stays in this browser only — nothing stored on Founder OS servers.';
    case 'LOCAL_SYNC':
      return 'Full tasks stay local; only encrypted metadata syncs so you can resume elsewhere.';
    case 'FOUNDER_NODE':
      return 'Vault on your machine; cloud stores encrypted metadata only.';
    case 'GITHUB':
      return 'Memory lives in .github/founder-os/ in your repo — you own the files.';
    case 'PLATFORM':
    default:
      return 'Memory on Founder OS (Neon) with GitHub activity sync — standard build-in-public mode.';
  }
}
