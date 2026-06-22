#!/usr/bin/env node
/**
 * Automate bot CNAME in Railway workspace domain DNS via logged-in browser session.
 * Falls back to Cloudflare + Name.com API paths when UI automation is unavailable.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readTunnelMeta, tunnelCnameTarget, CF_DOMAIN, CF_HOSTNAME } from './cloudflare-env.mjs';
import { loadCloudflareEnv } from './cloudflare-env.mjs';
import { loadVaultEnv } from './load-vault-env.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const headless = !process.argv.includes('--headed');
const skipBrowser = process.argv.includes('--skip-browser');

async function publicDnsLive(target) {
  try {
    const r = await fetch(`https://dns.google/resolve?name=${CF_HOSTNAME}&type=5`);
    const j = await r.json();
    return j.Answer?.some((a) => a.data?.includes('cfargotunnel.com'));
  } catch {
    return false;
  }
}

async function tryCloudflareDns(tunnelId) {
  loadCloudflareEnv();
  if (!process.env.CLOUDFLARE_API_TOKEN) return false;
  const target = tunnelCnameTarget(tunnelId);
  const zoneId = process.env.CLOUDFLARE_ZONE_ID || 'e5b41e1d9809507e75ecd826d8d66bef';
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const res = await fetch(`https://api.cloudflare.com/client/v4/zones/${zoneId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const zone = (await res.json()).result;
  if (zone?.status === 'active') {
    console.log('OK  Cloudflare zone active — bot CNAME should resolve after propagation');
    return await publicDnsLive(target);
  }
  console.log(`Cloudflare zone status: ${zone?.status} (NS must point to ${(zone?.name_servers || []).join(', ')})`);
  return false;
}

async function tryRailwayUi(target) {
  const profileDir = path.join(repoRoot, '.chrome-railway-profile');
  fs.mkdirSync(profileDir, { recursive: true });

  console.log('Opening Railway workspace domains (automation browser)...');
  let context;
  try {
    context = await chromium.launchPersistentContext(profileDir, {
      channel: 'chrome',
      headless,
    });
  } catch (err) {
    console.warn(`Browser launch failed (${err.message}) — use --skip-browser`);
    return false;
  }

  const page = context.pages()[0] || (await context.newPage());
  await page.goto('https://railway.app/workspace/domains', { waitUntil: 'domcontentloaded', timeout: 60000 });

  if (page.url().includes('/login')) {
    console.warn('Railway not logged in in Chrome — open Railway in Chrome, log in, then re-run');
    await context.close();
    return false;
  }

  // Open domain detail
  const domainLink = page.locator(`text=${CF_DOMAIN}`).first();
  await domainLink.waitFor({ timeout: 15000 }).catch(() => null);
  if (!(await domainLink.count())) {
    console.warn(`Domain ${CF_DOMAIN} not found in Railway workspace domains list`);
    await context.close();
    return false;
  }
  await domainLink.click();
  await page.waitForTimeout(2000);

  const body = await page.textContent('body');
  if (/managed at your external provider/i.test(body || '')) {
    console.warn('Railway shows external DNS provider — use Cloudflare NS or add CNAME at Name.com zone');
    if (!headless) await page.waitForTimeout(5000);
    await context.close();
    return false;
  }

  // Try add-record flow (UI labels vary)
  const addBtn = page.getByRole('button', { name: /add.*record|new record|add dns/i }).first();
  if (await addBtn.count()) {
    await addBtn.click();
    await page.waitForTimeout(1000);
  }

  const hostInput = page.locator('input[name="host"], input[placeholder*="host" i], input[placeholder*="name" i]').first();
  const typeSelect = page.locator('select, [role="combobox"]').filter({ hasText: /CNAME/i }).first();
  const valueInput = page.locator('input[name="value"], input[name="content"], input[placeholder*="value" i]').first();

  if (await hostInput.count()) {
    await hostInput.fill('bot');
  }
  if (await typeSelect.count()) {
    await typeSelect.click().catch(() => null);
    await page.getByText(/^CNAME$/i).first().click().catch(() => null);
  }
  if (await valueInput.count()) {
    await valueInput.fill(target);
  }

  const saveBtn = page.getByRole('button', { name: /save|add|create|submit/i }).first();
  if (await saveBtn.count()) {
    await saveBtn.click();
    await page.waitForTimeout(3000);
    console.log('OK  Submitted DNS record form in Railway UI');
  } else {
    console.warn('Could not find Railway DNS save button — UI may have changed');
  }

  if (!headless) await page.waitForTimeout(8000);
  await context.close();
  return await publicDnsLive(target);
}

async function tryNamecom(target) {
  try {
    const { spawnSync } = await import('node:child_process');
    const r = spawnSync(process.execPath, [path.join(repoRoot, 'scripts', 'namecom-dns-bot.mjs'), '--no-browser', '--quick'], {
      cwd: repoRoot,
      stdio: 'pipe',
      encoding: 'utf8',
    });
    if (r.status === 0) return true;
    if (r.stdout) process.stdout.write(r.stdout);
    if (r.stderr) process.stderr.write(r.stderr);
  } catch {
    /* skip */
  }
  return false;
}

async function main() {
  loadVaultEnv(repoRoot);
  loadCloudflareEnv();
  const meta = readTunnelMeta();
  if (!meta?.tunnelId) throw new Error('Tunnel missing — run npm run setup:named-tunnel:api first');
  const target = tunnelCnameTarget(meta.tunnelId);

  console.log('\n=== Apply bot DNS (auto) ===\n');
  console.log(`Target: ${CF_HOSTNAME} -> ${target}\n`);

  if (await publicDnsLive()) {
    console.log(`OK  ${CF_HOSTNAME} already resolves publicly`);
    fs.writeFileSync(path.join(repoRoot, '.home-tunnel-url'), `https://${CF_HOSTNAME}`);
    return;
  }

  if (await tryCloudflareDns(meta.tunnelId)) return;

  if (await tryNamecom(target)) {
    console.log(`OK  ${CF_HOSTNAME} is live (Name.com)`);
    fs.writeFileSync(path.join(repoRoot, '.home-tunnel-url'), `https://${CF_HOSTNAME}`);
    return;
  }

  if (!skipBrowser) {
    if (await tryRailwayUi(target)) {
      console.log(`OK  ${CF_HOSTNAME} is live`);
      fs.writeFileSync(path.join(repoRoot, '.home-tunnel-url'), `https://${CF_HOSTNAME}`);
      return;
    }
  }

  console.log('\nDNS still not public. Authoritative zone is Name.com/NS1 (apex A exists, bot missing).');
  console.log('Add in Railway → Workspace → Domains → doxxedcrypto.digital → DNS records:');
  console.log(`  Host: bot   Type: CNAME   Value: ${target}`);
  console.log('\nOr set custom nameservers to Cloudflare (zone records already configured):');
  console.log('  james.ns.cloudflare.com, vera.ns.cloudflare.com\n');
  process.exitCode = 2;
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
