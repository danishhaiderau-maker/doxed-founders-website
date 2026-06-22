#!/usr/bin/env node
/**
 * Publish bot tunnel DNS at Name.com (registrar — Cloudflare API cannot change NS here).
 *
 * Preferred: CNAME bot -> {tunnel-id}.cfargotunnel.com (keeps apex on Vercel via name.com)
 * Optional: --switch-cloudflare-ns  mirrors apex A on Cloudflare + sets Cloudflare NS at name.com
 *
 * Vault: ../doxedcryptofounder-secrets/vault/.env.namecom
 *   NAMECOM_USERNAME=your_name.com_username
 *   NAMECOM_API_TOKEN=your_name.com_api_token
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { getVaultDir } from './secrets-vault-path.mjs';
import { readDotEnv } from './home-bot-config.mjs';
import {
  CF_DOMAIN,
  CF_HOSTNAME,
  CF_NS,
  loadCloudflareEnv,
  readTunnelMeta,
  tunnelCnameTarget,
  cfApi,
} from './cloudflare-env.mjs';

const switchNs = process.argv.includes('--switch-cloudflare-ns');
const openBrowser = !process.argv.includes('--no-browser');
const DOMAIN = CF_DOMAIN;
const BOT_HOST = 'bot';
const APEX_IP = '76.76.21.21';

function loadNamecomEnv() {
  const vaultFile = path.join(getVaultDir(), '.env.namecom');
  if (fs.existsSync(vaultFile)) {
    for (const [k, v] of Object.entries(readDotEnv(vaultFile))) {
      if (!process.env[k]) process.env[k] = v;
    }
  }
  const user = process.env.NAMECOM_USERNAME?.trim();
  const token = process.env.NAMECOM_API_TOKEN?.trim();
  if (!user || !token) {
    throw new Error(`Missing Name.com API credentials.

Cloudflare API cannot update Name.com nameservers — the domain registrar is name.com.

Add once to: ${vaultFile}
  NAMECOM_USERNAME=your_username
  NAMECOM_API_TOKEN=your_api_token

Create token: https://www.name.com/account/settings/api

Then re-run: npm run finish:home-dns
`);
  }
  return { user, token };
}

function authHeader(user, token) {
  return `Basic ${Buffer.from(`${user}:${token}`).toString('base64')}`;
}

async function namecom(pathname, { method = 'GET', body, user, token } = {}) {
  const res = await fetch(`https://api.name.com${pathname}`, {
    method,
    headers: {
      Authorization: authHeader(user, token),
      'Content-Type': 'application/json',
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error(`${method} ${pathname}: HTTP ${res.status} ${text.slice(0, 200)}`);
  }
  if (!res.ok) {
    throw new Error(`${method} ${pathname}: ${json.message || res.statusText}`);
  }
  return json;
}

async function listRecords(user, token) {
  const data = await namecom(`/core/v1/domains/${DOMAIN}/records`, { user, token });
  return data.records || data || [];
}

async function ensureBotCname(user, token, target) {
  const records = await listRecords(user, token);
  const existing = records.find(
    (r) => r.type === 'CNAME' && (r.host === BOT_HOST || r.fqdn?.startsWith(`${BOT_HOST}.`)),
  );
  if (existing) {
    if (existing.answer === target || existing.answer === `${target}.`) {
      console.log(`OK  Name.com CNAME ${CF_HOSTNAME} -> ${target}`);
      return;
    }
    await namecom(`/core/v1/domains/${DOMAIN}/records/${existing.id}`, {
      method: 'PUT',
      user,
      token,
      body: { type: 'CNAME', host: BOT_HOST, answer: target, ttl: 300 },
    });
    console.log(`OK  Name.com CNAME updated -> ${target}`);
    return;
  }
  await namecom(`/core/v1/domains/${DOMAIN}/records`, {
    method: 'POST',
    user,
    token,
    body: { type: 'CNAME', host: BOT_HOST, answer: target, ttl: 300 },
  });
  console.log(`OK  Name.com CNAME created: ${CF_HOSTNAME} -> ${target}`);
}

async function mirrorApexOnCloudflare() {
  loadCloudflareEnv();
  const records = await cfApi(`/zones/${process.env.CLOUDFLARE_ZONE_ID || 'e5b41e1d9809507e75ecd826d8d66bef'}/dns_records?type=A&name=${DOMAIN}`);
  const apex = records?.find((r) => r.name === DOMAIN);
  if (apex?.content === APEX_IP) {
    console.log(`OK  Cloudflare apex A already ${APEX_IP}`);
    return;
  }
  if (apex) {
    await cfApi(`/zones/${process.env.CLOUDFLARE_ZONE_ID || 'e5b41e1d9809507e75ecd826d8d66bef'}/dns_records/${apex.id}`, {
      method: 'PATCH',
      body: { type: 'A', name: '@', content: APEX_IP, proxied: true },
    });
  } else {
    await cfApi(`/zones/${process.env.CLOUDFLARE_ZONE_ID || 'e5b41e1d9809507e75ecd826d8d66bef'}/dns_records`, {
      method: 'POST',
      body: { type: 'A', name: '@', content: APEX_IP, proxied: true, ttl: 1 },
    });
  }
  console.log(`OK  Cloudflare apex A -> ${APEX_IP} (Vercel)`);
}

async function switchNameservers(user, token) {
  await namecom(`/core/v1/domains/${DOMAIN}:setNameservers`, {
    method: 'POST',
    user,
    token,
    body: { nameservers: CF_NS },
  });
  console.log(`OK  Name.com nameservers -> ${CF_NS.join(', ')}`);
}

function openNamecomDnsPage(target) {
  if (!openBrowser || process.platform !== 'win32') return;
  const url = `https://www.name.com/account/domain/details/${DOMAIN}#dns`;
  console.log(`\nOpening Name.com DNS: ${url}`);
  console.log(`Add CNAME: host=bot  answer=${target}  TTL=300\n`);
  spawnSync('cmd', ['/c', 'start', '', url], { stdio: 'ignore' });
}

async function waitForDns() {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`https://dns.google/resolve?name=${CF_HOSTNAME}&type=5`);
      const j = await r.json();
      const hit = j.Answer?.find((a) => a.type === 5);
      if (hit?.data?.includes('cfargotunnel.com')) return true;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 8000));
  }
  return false;
}

async function main() {
  const meta = readTunnelMeta();
  if (!meta?.tunnelId) throw new Error('Tunnel not configured — run npm run setup:named-tunnel:api first');
  const target = tunnelCnameTarget(meta.tunnelId);
  console.log('\n=== Name.com DNS for bot tunnel ===\n');
  console.log(`Target CNAME: ${CF_HOSTNAME} -> ${target}\n`);

  try {
    const { user, token } = loadNamecomEnv();
    await namecom('/core/v1/hello', { user, token });
    console.log('OK  Name.com API authenticated');

    if (switchNs) {
      await mirrorApexOnCloudflare();
      await switchNameservers(user, token);
    } else {
      await ensureBotCname(user, token, target);
    }
  } catch (err) {
    console.warn(`\nName.com API: ${err.message}`);
    openNamecomDnsPage(target);
    const live = await waitForDns();
    if (!live) {
      console.warn('DNS not live yet — add the CNAME at Name.com, then re-run npm run finish:home-dns');
      process.exitCode = 2;
      return;
    }
  }

  const live = await waitForDns();
  console.log(live ? `\nOK  ${CF_HOSTNAME} resolves publicly` : `\nWARN DNS propagation still pending`);
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
