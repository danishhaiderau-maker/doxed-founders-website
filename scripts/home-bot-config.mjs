/**
 * Shared config for home-hosted BTC bot (replaces Railway btc-conservative-agent).
 */
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { getVaultDir } from './secrets-vault-path.mjs';

// Fly is the authoritative 24/7 showcase. The custom hostname may be repointed
// later, but operational tools must not silently fall back to the retired
// home/Cloudflare route.
export const DEFAULT_HOME_BOT_PUBLIC_URL = 'https://doxed-btc-bot.fly.dev';
export const DEFAULT_HOME_BOT_LOCAL_PORT = 7002;
export const RAILWAY_BOT_SERVICE = 'btc-conservative-agent';
export const RAILWAY_API_SERVICE = 'doxed-founders-website';
export const RELAY_WEBHOOK_URL =
  'https://doxxedcrypto.digital/api/trading-agents/conservative-btc/showcase-relay-event';

export function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    let v = trimmed.slice(idx + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    map[trimmed.slice(0, idx).trim()] = v;
  }
  return map;
}

/** Public HTTPS URL for home bot (Cloudflare tunnel or custom). */
export function resolveHomeBotPublicUrl(argvUrl, repoRoot) {
  const fromArg = argvUrl?.trim();
  if (fromArg) return fromArg.replace(/\/$/, '');
  const fromEnv = process.env.HOME_BOT_PUBLIC_URL?.trim()
    || process.env.TRADING_AGENT_BOT_URL?.trim();
  if (fromEnv) return fromEnv.replace(/\/$/, '');
  const root = repoRoot ?? process.cwd();
  // The retired laptop tunnel is diagnostic-only. It must be explicitly
  // requested so an old .home-tunnel-url cannot silently override Fly.
  if (process.env.ALLOW_HOME_TUNNEL_FALLBACK === 'YES') {
    const tunnelFile = join(root, '.home-tunnel-url');
    if (existsSync(tunnelFile)) {
      const fromTunnel = readFileSync(tunnelFile, 'utf8').trim();
      if (fromTunnel.startsWith('https://')) return fromTunnel.replace(/\/$/, '');
    }
    const vaultFile = join(getVaultDir(), '.env.home-bot');
    const fromVault = readDotEnv(vaultFile).HOME_BOT_PUBLIC_URL?.trim();
    if (fromVault) return fromVault.replace(/\/$/, '');
  }
  return DEFAULT_HOME_BOT_PUBLIC_URL;
}

export function homeBotLocalUrl(port = DEFAULT_HOME_BOT_LOCAL_PORT) {
  const p = process.env.HOME_BOT_LOCAL_PORT?.trim() || String(port);
  return `http://127.0.0.1:${p}`;
}
