#!/usr/bin/env node
/**
 * Permanent bot tunnel via Cloudflare API (no cloudflared tunnel login / cert.pem).
 *
 * One-time: create API token at https://dash.cloudflare.com/profile/api-tokens
 *   - Account: Cloudflare Tunnel Edit (or Cloudflare One Connector cloudflared Write)
 *   - Zone doxxedcrypto.digital: DNS Edit
 *
 * Then:
 *   set CLOUDFLARE_API_TOKEN=your_token
 *   npm run setup:named-tunnel:api
 *
 * Or pass token inline (PowerShell):
 *   $env:CLOUDFLARE_API_TOKEN="..."; npm run setup:named-tunnel:api
 */
import { execSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadCloudflareEnv } from './cloudflare-env.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');

loadCloudflareEnv();
const API_TOKEN = process.env.CLOUDFLARE_API_TOKEN?.trim();
const ACCOUNT_ID = (process.env.CLOUDFLARE_ACCOUNT_ID || '242582298202462d75198184516c54d2').trim();
const ZONE_ID = (process.env.CLOUDFLARE_ZONE_ID || 'e5b41e1d9809507e75ecd826d8d66bef').trim();
const TUNNEL_NAME = (process.env.CLOUDFLARE_TUNNEL_NAME || 'doxed-btc-bot').trim();
const HOSTNAME = (process.env.CLOUDFLARE_TUNNEL_HOSTNAME || 'bot.doxxedcrypto.digital').trim();
const BOT_PORT = Number(process.env.HOME_BOT_PORT || 7800);
const STABLE_URL = `https://${HOSTNAME}`;
const DNS_NAME = HOSTNAME.replace(/\.doxxedcrypto\.digital$/i, '') || 'bot';

const skipWire = process.argv.includes('--skip-wire');
const skipService = process.argv.includes('--skip-service');

function die(msg) {
  console.error(`\n${msg}\n`);
  process.exit(1);
}

if (!API_TOKEN) {
  die(`Missing CLOUDFLARE_API_TOKEN.

Create a token: Cloudflare dashboard → doxxedcrypto.digital → API → Get your API token
Permissions needed:
  - Account → Cloudflare Tunnel → Edit
  - Zone  → DNS → Edit (doxxedcrypto.digital)

Then run:
  $env:CLOUDFLARE_API_TOKEN="paste_token_here"
  npm run setup:named-tunnel:api
`);
}

async function cf(apiPath, { method = 'GET', body } = {}) {
  const res = await fetch(`https://api.cloudflare.com/client/v4${apiPath}`, {
    method,
    headers: {
      Authorization: `Bearer ${API_TOKEN}`,
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

async function findOrCreateTunnel() {
  const list = await cf(`/accounts/${ACCOUNT_ID}/cfd_tunnel`);
  const existing = (list || []).find((t) => t.name === TUNNEL_NAME && !t.deleted_at);
  if (existing) {
    console.log(`OK  Tunnel exists: ${existing.id}`);
    return existing.id;
  }
  console.log(`Creating tunnel ${TUNNEL_NAME}...`);
  const created = await cf(`/accounts/${ACCOUNT_ID}/cfd_tunnel`, {
    method: 'POST',
    body: { name: TUNNEL_NAME, config_src: 'cloudflare' },
  });
  console.log(`OK  Created tunnel: ${created.id}`);
  return created.id;
}

async function configureIngress(tunnelId) {
  console.log(`Configuring ingress ${HOSTNAME} -> http://127.0.0.1:${BOT_PORT}`);
  await cf(`/accounts/${ACCOUNT_ID}/cfd_tunnel/${tunnelId}/configurations`, {
    method: 'PUT',
    body: {
      config: {
        ingress: [
          { hostname: HOSTNAME, service: `http://127.0.0.1:${BOT_PORT}` },
          { service: 'http_status:404' },
        ],
      },
    },
  });
  console.log('OK  Ingress configured (remote-managed)');
}

async function ensureDns(tunnelId) {
  const target = `${tunnelId}.cfargotunnel.com`;
  const records = await cf(`/zones/${ZONE_ID}/dns_records?type=CNAME&name=${DNS_NAME}`);
  const match = (records || []).find((r) => r.name === HOSTNAME || r.name === `${DNS_NAME}.doxxedcrypto.digital`);
  if (match) {
    if (match.content === target) {
      console.log(`OK  DNS already points to ${target}`);
      return;
    }
    await cf(`/zones/${ZONE_ID}/dns_records/${match.id}`, {
      method: 'PATCH',
      body: { type: 'CNAME', name: DNS_NAME, content: target, proxied: true },
    });
    console.log(`OK  DNS updated -> ${target}`);
    return;
  }
  await cf(`/zones/${ZONE_ID}/dns_records`, {
    method: 'POST',
    body: { type: 'CNAME', name: DNS_NAME, content: target, proxied: true, ttl: 1 },
  });
  console.log(`OK  DNS created: ${HOSTNAME} -> ${target}`);
}

async function fetchRunToken(tunnelId) {
  const token = await cf(`/accounts/${ACCOUNT_ID}/cfd_tunnel/${tunnelId}/token`);
  if (!token || typeof token !== 'string') throw new Error('Empty tunnel token from API');
  return token;
}

function saveLocalState(tunnelId, runToken) {
  const configDir = path.join(process.env.USERPROFILE || repoRoot, '.cloudflared');
  fs.mkdirSync(configDir, { recursive: true });
  const tokenPath = path.join(configDir, `${TUNNEL_NAME}.token`);
  fs.writeFileSync(tokenPath, runToken, 'utf8');
  fs.writeFileSync(
    path.join(configDir, 'tunnel-api.json'),
    JSON.stringify({ tunnelId, tunnelName: TUNNEL_NAME, hostname: HOSTNAME, accountId: ACCOUNT_ID }, null, 2),
  );
  fs.writeFileSync(path.join(repoRoot, '.home-tunnel-url'), STABLE_URL);
  fs.writeFileSync(path.join(repoRoot, '.home-use-named-tunnel'), 'enabled');
  console.log(`OK  Token saved: ${tokenPath}`);
}

function installWindowsService(runToken) {
  if (process.platform !== 'win32') {
    console.log('Skip service install (not Windows). Run: cloudflared tunnel run --token <token>');
    return;
  }
  try {
    execSync('cloudflared --version', { stdio: 'ignore' });
  } catch {
    die('Install cloudflared: winget install Cloudflare.cloudflared');
  }
  try {
    execSync('taskkill /F /IM cloudflared.exe', { stdio: 'ignore' });
  } catch {
    /* none running */
  }
  console.log('Installing cloudflared Windows service (token mode)...');
  try {
    execSync(`cloudflared service install ${runToken}`, { stdio: 'inherit', shell: true });
    try {
      execSync('sc start cloudflared', { stdio: 'inherit' });
    } catch {
      console.warn('Start service manually: sc start cloudflared');
    }
    console.log('OK  Windows service installed');
  } catch {
    console.warn('\nService install needs Administrator (right-click SETUP-NAMED-TUNNEL-API.cmd → Run as administrator).');
    console.warn('Starting tunnel in background for this session instead...\n');
    const tokenPath = path.join(process.env.USERPROFILE || repoRoot, '.cloudflared', `${TUNNEL_NAME}.token`);
    spawn('cloudflared', ['tunnel', 'run', '--token', runToken], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    }).unref();
  }
}

async function wireProduction() {
  console.log('\n=== Wiring Neon + Railway ===');
  execSync(`npm run wire:home-bot -- ${STABLE_URL}`, { cwd: repoRoot, stdio: 'inherit', shell: true });
}

async function waitForLive() {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${STABLE_URL}/api/ping`, { signal: AbortSignal.timeout(8000) });
      if (r.ok) return true;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 5000));
  }
  return false;
}

async function main() {
  console.log('\n=== Cloudflare API tunnel setup ===\n');
  console.log(`Account: ${ACCOUNT_ID}`);
  console.log(`Zone:    ${ZONE_ID}`);
  console.log(`URL:     ${STABLE_URL}\n`);

  const tunnelId = await findOrCreateTunnel();
  await configureIngress(tunnelId);
  await ensureDns(tunnelId);
  const runToken = await fetchRunToken(tunnelId);
  saveLocalState(tunnelId, runToken);

  if (!skipService) installWindowsService(runToken);

  if (!skipWire) {
    const live = await waitForLive();
    if (!live) {
      console.warn('Tunnel not live yet — wiring with --skip-health-check');
      execSync(`npm run wire:home-bot -- ${STABLE_URL} --skip-health-check`, {
        cwd: repoRoot,
        stdio: 'inherit',
        shell: true,
      });
    } else {
      await wireProduction();
    }
  }

  console.log('\n=== Done ===');
  console.log(`  ${STABLE_URL}`);
  console.log('  Verify: curl ' + STABLE_URL + '/api/ping');
  console.log('  No browser login required — API token only.\n');
}

main().catch((e) => die(e.message));
