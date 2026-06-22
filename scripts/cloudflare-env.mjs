import fs from 'node:fs';
import path from 'node:path';
import { getVaultDir } from './secrets-vault-path.mjs';
import { readDotEnv } from './home-bot-config.mjs';

export const CF_ACCOUNT_ID =
  process.env.CLOUDFLARE_ACCOUNT_ID?.trim() || '242582298202462d75198184516c54d2';
export const CF_ZONE_ID =
  process.env.CLOUDFLARE_ZONE_ID?.trim() || 'e5b41e1d9809507e75ecd826d8d66bef';
export const CF_TUNNEL_NAME = process.env.CLOUDFLARE_TUNNEL_NAME?.trim() || 'doxed-btc-bot';
export const CF_HOSTNAME =
  process.env.CLOUDFLARE_TUNNEL_HOSTNAME?.trim() || 'bot.doxxedcrypto.digital';
export const CF_DOMAIN = 'doxxedcrypto.digital';
export const CF_NS = ['james.ns.cloudflare.com', 'vera.ns.cloudflare.com'];

export function loadCloudflareEnv() {
  const vaultFile = path.join(getVaultDir(), '.env.cloudflare');
  if (fs.existsSync(vaultFile)) {
    for (const [k, v] of Object.entries(readDotEnv(vaultFile))) {
      if (!process.env[k]) process.env[k] = v;
    }
  }
  return process.env.CLOUDFLARE_API_TOKEN?.trim() || '';
}

export async function cfApi(apiPath, { method = 'GET', body, token } = {}) {
  const apiToken = token || loadCloudflareEnv();
  if (!apiToken) throw new Error('Missing CLOUDFLARE_API_TOKEN');
  const res = await fetch(`https://api.cloudflare.com/client/v4${apiPath}`, {
    method,
    headers: {
      Authorization: `Bearer ${apiToken}`,
      'Content-Type': 'application/json',
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!data.success) {
    const err = data.errors?.map((e) => e.message).join('; ') || res.statusText;
    throw new Error(`${method} ${apiPath}: ${err}`);
  }
  return data.result;
}

export function readTunnelMeta() {
  const configDir = path.join(process.env.USERPROFILE || '', '.cloudflared');
  const metaPath = path.join(configDir, 'tunnel-api.json');
  if (!fs.existsSync(metaPath)) return null;
  return JSON.parse(fs.readFileSync(metaPath, 'utf8'));
}

export function tunnelCnameTarget(tunnelId) {
  return `${tunnelId}.cfargotunnel.com`;
}
