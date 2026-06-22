#!/usr/bin/env node
/**
 * One-shot: DNS + tunnel + wire + cloud sync (Neon, Railway, Vercel, GitHub push).
 */
import { execSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadVaultEnv } from './load-vault-env.mjs';
import { loadCloudflareEnv, CF_HOSTNAME } from './cloudflare-env.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const skipGit = process.argv.includes('--skip-git');
const skipSync = process.argv.includes('--skip-sync');

function run(cmd, opts = {}) {
  console.log(`\n> ${cmd}\n`);
  execSync(cmd, { cwd: repoRoot, stdio: 'inherit', shell: true, ...opts });
}

function runSoft(cmd) {
  const r = spawnSync(cmd, { cwd: repoRoot, shell: true, stdio: 'inherit' });
  return r.status === 0;
}

function ensureCloudflaredRunning() {
  if (process.platform !== 'win32') return;
  const tokenPath = path.join(process.env.USERPROFILE || '', '.cloudflared', 'doxed-btc-bot.token');
  if (!fs.existsSync(tokenPath)) return;
  const listed = spawnSync('tasklist', ['/FI', 'IMAGENAME eq cloudflared.exe'], {
    encoding: 'utf8',
  });
  if (listed.stdout?.includes('cloudflared.exe')) {
    console.log('OK  cloudflared already running');
    return;
  }
  const token = fs.readFileSync(tokenPath, 'utf8').trim();
  spawnSync(
    'powershell',
    [
      '-NoProfile',
      '-Command',
      `Start-Process cloudflared -ArgumentList 'tunnel','run','--token','${token.replace(/'/g, "''")}'`,
    ],
    { stdio: 'inherit' },
  );
  console.log('OK  cloudflared started');
}

async function waitBotLocal() {
  for (let i = 0; i < 12; i++) {
    try {
      const r = await fetch('http://127.0.0.1:7800/api/ping', { signal: AbortSignal.timeout(5000) });
      if (r.ok) return true;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 5000));
  }
  return false;
}

async function waitBotPublic() {
  const url = `https://${CF_HOSTNAME}/api/ping`;
  for (let i = 0; i < 18; i++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(8000) });
      if (r.ok) return true;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 8000));
  }
  return false;
}

async function main() {
  console.log('\n=== Finish home production (auto) ===\n');
  loadVaultEnv(repoRoot);
  loadCloudflareEnv();

  console.log('--- 1/7 Cloudflare tunnel (API) ---');
  if (process.env.CLOUDFLARE_API_TOKEN) {
    runSoft('npm run setup:named-tunnel:api -- --skip-service --skip-wire');
  } else {
    console.warn('No CLOUDFLARE_API_TOKEN — skipping tunnel API (using saved token if present)');
  }

  console.log('\n--- 2/7 Railway domain DNS (bot CNAME) ---');
  runSoft('node scripts/railway-domain-dns-bot.mjs --no-browser');

  console.log('\n--- 3/7 Start cloudflared ---');
  ensureCloudflaredRunning();

  console.log('\n--- 4/7 Local bot health ---');
  const localOk = await waitBotLocal();
  console.log(localOk ? 'OK  bot :7800' : 'WARN bot not on :7800 — start via Agent Hub after restart');

  console.log('\n--- 5/7 Public tunnel health ---');
  const publicOk = await waitBotPublic();
  console.log(publicOk ? `OK  https://${CF_HOSTNAME}` : 'WARN public URL not live yet (DNS may still propagate)');

  console.log('\n--- 6/7 Wire Neon + Railway ---');
  const wireFlags = publicOk ? '' : ' --skip-health-check';
  run(`npm run wire:home-bot -- https://${CF_HOSTNAME}${wireFlags}`);

  if (!skipSync) {
    console.log('\n--- 7/7 Cloud sync (Neon, Railway, Vercel) ---');
    fs.writeFileSync(path.join(repoRoot, '.home-bot-mode'), 'enabled');
    process.env.HOME_BOT_MODE = '1';
    run('npm run sync:all');
  }

  if (!skipGit) {
    console.log('\n--- Git push ---');
    runSoft('git add SETUP-NAMED-TUNNEL.cmd SETUP-NAMED-TUNNEL-API.cmd package.json scripts/setup-named-tunnel-api.mjs scripts/run-named-bot-tunnel.ps1 scripts/cloudflare-env.mjs scripts/namecom-dns-bot.mjs scripts/finish-home-production.mjs');
    runSoft(
      'git commit -m "Add Cloudflare API tunnel setup and Name.com DNS finish automation."',
    );
    runSoft('git push origin master');
  }

  console.log('\n=== Finish complete ===');
  console.log(`  Bot URL: https://${CF_HOSTNAME}`);
  console.log('  Kill command center windows, then RESTART-LAUNCHER.cmd → Agent Hub Start everything\n');
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
