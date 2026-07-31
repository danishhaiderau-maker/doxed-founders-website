import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { CANONICAL_SHOWCASE_BOT_URL } from './canonical-showcase-runtime';

/**
 * Fly-canonical lock — FIX 2 belt-and-suspenders guard.
 *
 * `config/fly-canonical.lock.json` is the source-controlled statement that
 * Fly.io is the sole AI/strategy/trading owner and that desktop 7002 is a
 * read-only proxy. The desktop launchers (`scripts/start-home-bot.ps1`,
 * `scripts/start-fly-desktop-mirror.ps1`, `scripts/fly-canonical-lock.ps1`)
 * already enforce this on the Windows side; this module is the API-side
 * mirror so a stale or rogue desktop publisher can never satisfy the
 * canonical-owner check on Railway even if it shares `BOT_CONTROL_SECRET`.
 *
 * The lock is read once at module load (source-controlled file, no live
 * mutation) and exposed as a frozen object. Reads are atomic and safe to
 * call from hot paths.
 */
export type FlyCanonicalLock = {
  schema: string;
  frozen: boolean;
  sourceUrl: string;
  desktopBotEnabled: boolean;
  desktopDashboardProxyPort?: number;
  desktopAnalyzerPort?: number;
  reason?: string;
};

/**
 * Resolve the lock file path. Walk up from cwd (production deploys run at
 * repo root; tests may run from `apps/api/`), then check a few well-known
 * source-relative candidates as a fallback. This makes the lock discoverable
 * from any subdir without depending on `__dirname` (which differs between
 * tsx src runs and dist compiled runs).
 */
function resolveLockPath(): string {
  const seg = 'config';
  const name = 'fly-canonical.lock.json';
  const seen = new Set<string>();
  const candidates: string[] = [];
  const pushDir = (dir: string) => {
    if (!dir || seen.has(dir)) return;
    seen.add(dir);
    candidates.push(join(dir, seg, name));
  };

  // Walk cwd and each ancestor — covers repo root regardless of where
  // the test runner starts.
  let cur = process.cwd();
  for (let i = 0; i < 6 && cur; i++) {
    pushDir(cur);
    const parent = join(cur, '..');
    if (parent === cur) break;
    cur = parent;
  }

  // Source-relative fallbacks for compiled dist and bundled layouts.
  if (typeof __dirname === 'string') {
    pushDir(join(__dirname, '..', '..', '..')); // src/trading-agents -> repo root
    pushDir(join(__dirname, '..', '..'));       // dist/trading-agents -> repo root
    pushDir(__dirname);
  }

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return join(process.cwd(), seg, name);
}

function loadLock(path: string = resolveLockPath()): FlyCanonicalLock | null {
  try {
    const raw = readFileSync(path, 'utf8');
    const parsed = JSON.parse(raw) as Partial<FlyCanonicalLock>;
    if (
      parsed
      && typeof parsed === 'object'
      && typeof parsed.sourceUrl === 'string'
      && typeof parsed.frozen === 'boolean'
      && typeof parsed.desktopBotEnabled === 'boolean'
    ) {
      return {
        schema: String(parsed.schema ?? 'fly_canonical_runtime_v1'),
        frozen: parsed.frozen === true,
        sourceUrl: parsed.sourceUrl.replace(/\/$/, ''),
        desktopBotEnabled: parsed.desktopBotEnabled === true,
        desktopDashboardProxyPort:
          typeof parsed.desktopDashboardProxyPort === 'number'
            ? parsed.desktopDashboardProxyPort
            : undefined,
        desktopAnalyzerPort:
          typeof parsed.desktopAnalyzerPort === 'number'
            ? parsed.desktopAnalyzerPort
            : undefined,
        reason: parsed.reason,
      };
    }
    return null;
  } catch {
    return null;
  }
}

export const FLY_CANONICAL_LOCK_PATH: string = resolveLockPath();
export const FLY_CANONICAL_LOCK: FlyCanonicalLock | null = loadLock();

/**
 * True when the source-controlled lock exists, is frozen, points at the
 * canonical Fly URL, and explicitly disables the desktop bot. Used by the
 * API to reject any pushed/direct snapshot that does not carry matching
 * Fly-origin identity.
 */
export const FLY_CANONICAL_LOCK_ENFORCED: boolean =
  FLY_CANONICAL_LOCK?.frozen === true
  && FLY_CANONICAL_LOCK.desktopBotEnabled === false
  && FLY_CANONICAL_LOCK.sourceUrl === CANONICAL_SHOWCASE_BOT_URL;

/**
 * True when the bot-declared `dashboard_url` matches the canonical Fly URL.
 * On Fly, btc_conservative_agent.py sets DASHBOARD_PUBLIC_URL to
 * https://doxed-btc-bot.fly.dev/ which surfaces in /api/state. A desktop
 * process (loopback :7002 or LAN) reports a non-Fly URL, so this is a
 * robust signal of Fly origin that cannot be forged by re-publishing the
 * cached snapshot through a different process.
 */
export function isFlyDeclaredDashboardUrl(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim().replace(/\/$/, '');
  return trimmed === CANONICAL_SHOWCASE_BOT_URL;
}

