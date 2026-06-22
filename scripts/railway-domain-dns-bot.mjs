#!/usr/bin/env node
/**
 * Add bot tunnel CNAME on Railway-purchased domain (doxxedcrypto.digital).
 *
 * Railway owns the domain — DNS is managed at:
 *   https://railway.app/workspace/domains → doxxedcrypto.digital → DNS records
 *
 * Public GraphQL has no API for purchased-domain DNS yet; this script verifies
 * state and opens the Railway domain page with exact values to paste.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readTunnelMeta, tunnelCnameTarget, CF_DOMAIN, CF_HOSTNAME } from './cloudflare-env.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const openBrowser = !process.argv.includes('--no-browser');

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

async function checkNs() {
  const r = await fetch(`https://dns.google/resolve?name=${CF_DOMAIN}&type=2`);
  const j = await r.json();
  return (j.Answer || []).map((a) => a.data?.replace(/\.$/, ''));
}

async function main() {
  const meta = readTunnelMeta();
  if (!meta?.tunnelId) throw new Error('Tunnel missing — run npm run setup:named-tunnel:api first');
  const target = tunnelCnameTarget(meta.tunnelId);

  console.log('\n=== Railway domain DNS (bot tunnel) ===\n');
  console.log(`Domain registered on Railway (not Name.com account Dandare3517).`);
  console.log(`Add this record in Railway → Workspace → Domains → ${CF_DOMAIN}:\n`);
  console.log(`  Type:   CNAME`);
  console.log(`  Host:   bot`);
  console.log(`  Value:  ${target}`);
  console.log(`  TTL:    300 (or default)\n`);

  const ns = await checkNs();
  const railwayManaged = ns.some((n) => /railway/i.test(n));
  const namecomNs = ns.some((n) => /name\.com/i.test(n));
  if (namecomNs && !railwayManaged) {
    console.log('Note: Domain currently uses Name.com nameservers (custom NS on Railway).');
    console.log('If Railway DNS editor is disabled, click "Reset to Railway" nameservers first,');
    console.log('then add the CNAME above in Railway domain DNS records.\n');
  } else if (railwayManaged) {
    console.log('OK  Railway-managed nameservers detected.\n');
  }

  if (openBrowser && process.platform === 'win32') {
    spawnSync('cmd', ['/c', 'start', '', 'https://railway.app/workspace/domains'], { stdio: 'ignore' });
  }

  const live = await waitForDns();
  if (live) {
    console.log(`OK  ${CF_HOSTNAME} resolves publicly`);
    fs.writeFileSync(path.join(repoRoot, '.home-tunnel-url'), `https://${CF_HOSTNAME}`);
    return;
  }

  console.log('DNS not live yet — add the CNAME in Railway domain settings, then re-run:');
  console.log('  npm run finish:railway-dns\n');
  process.exitCode = 2;
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
