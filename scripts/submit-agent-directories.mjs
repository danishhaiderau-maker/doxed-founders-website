#!/usr/bin/env node
/**
 * Submit Conservative BTC Agent to all agent directories.
 * - Validates live metadata URLs
 * - POST Fushu API when available
 * - Attempts SAID CLI if agent-wallet.json is funded
 * - Attempts Spawn API if THESPAWN_API_KEY in vault
 * - Opens manual submit forms with --open (Windows)
 *
 * Usage:
 *   npm run submit:agent-directories
 *   npm run submit:agent-directories -- --open
 *   npm run submit:agent-directories -- --said
 */
import { execSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  AGENT_DIRECTORY_WEB_TARGETS,
  CONSERVATIVE_BTC_DIRECTORY_PROFILE as P,
} from '../packages/utils/dist/agent-directory-submissions.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultEnv = join(dirname(root), 'doxedcryptofounder-secrets', 'vault', '.env');

function loadVault() {
  if (!existsSync(vaultEnv)) return {};
  const map = {};
  for (const line of readFileSync(vaultEnv, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    map[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^["']|["']$/g, '');
  }
  return map;
}

async function checkUrl(url) {
  try {
    const res = await fetch(url, { method: 'GET', redirect: 'follow' });
    return { url, ok: res.ok, status: res.status };
  } catch (err) {
    return { url, ok: false, status: err instanceof Error ? err.message : 'error' };
  }
}

async function submitFushu() {
  const body = {
    name: P.name,
    tagline: P.tagline,
    source_url: P.hubUrl,
    author_name: P.company,
    author_email: P.authorEmail,
    type: 'agent',
    language: 'typescript',
    protocols: ['rest'],
    capabilities: ['signal-intent', 'lifecycle-events', 'btc-perp-signals'],
    description: P.description,
    endpoint: P.mandateUrl,
  };
  try {
    const res = await fetch('https://fushu.dev/api/v1/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      json = { raw: text.slice(0, 200) };
    }
    if (res.ok) {
      console.log('✓ Fushu API submit:', json.id ?? json.message ?? json);
      return true;
    }
    console.log(`✗ Fushu API (${res.status}):`, json.error ?? json.message ?? text.slice(0, 120));
    return false;
  } catch (err) {
    console.log('✗ Fushu API failed:', err instanceof Error ? err.message : err);
    return false;
  }
}

async function submitSpawn(apiKey) {
  const body = {
    name: P.name,
    description: P.description,
    chain_id: 8453,
    metadata_uri: P.agentJsonUrl,
    image_url: P.iconUrl,
    x402_support: false,
    services: [
      { name: 'REST', endpoint: P.mandateUrl },
      { name: 'web', endpoint: P.hubUrl },
      { name: 'docs', endpoint: P.docsUrl },
    ],
  };
  try {
    const res = await fetch('https://thespawn.io/api/v1/agents', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    const json = await res.json();
    if (res.ok) {
      console.log('✓ Spawn API:', json.url ?? JSON.stringify(json).slice(0, 200));
      if (json.onchain_registration) {
        console.log('  → Sign on-chain tx on Base, then mark SPAWN in admin UI.');
      }
      return true;
    }
    console.log('✗ Spawn API:', json.error ?? json.message ?? res.status);
    return false;
  } catch (err) {
    console.log('✗ Spawn API failed:', err instanceof Error ? err.message : err);
    return false;
  }
}

function openUrls(urls) {
  for (const url of urls) {
    try {
      spawnSync('cmd', ['/c', 'start', '', url], { stdio: 'ignore', shell: true });
    } catch {
      console.log('Open manually:', url);
    }
  }
}

function printManualPack() {
  console.log('\n=== Copy-paste for ALL manual forms ===\n');
  console.log('Name:          ', P.name);
  console.log('Tagline:       ', P.tagline);
  console.log('Website:       ', P.hubUrl);
  console.log('Demo URL:      ', P.hubUrl);
  console.log('Docs:          ', P.docsUrl);
  console.log('Logo:          ', P.iconUrl);
  console.log('Thumbnail:     ', P.thumbnailUrl);
  console.log('AgentCard:     ', P.agentCardUrl);
  console.log('Email:         ', P.authorEmail);
  console.log('Company:       ', P.company);
  console.log('Categories:    ', P.categories.join(', '));
  console.log('Tags:          ', P.tags.join(', '));
  console.log('Pricing:       ', P.pricingModel);
  console.log('\nDescription:\n', P.description);
}

async function main() {
  const openForms = process.argv.includes('--open');
  const runSaid = process.argv.includes('--said');

  console.log('\n=== Conservative BTC Agent — directory submissions ===\n');

  console.log('--- Metadata health ---');
  const urls = [P.hubUrl, P.agentCardUrl, P.agentJsonUrl, P.docsUrl, P.iconUrl, P.thumbnailUrl];
  for (const url of urls) {
    const r = await checkUrl(url);
    console.log(`${r.ok ? '✓' : '✗'} ${r.status} ${url}`);
  }

  console.log('\n--- Automated submissions ---');
  await submitFushu();

  const vault = loadVault();
  const spawnKey = vault.THESPAWN_API_KEY || process.env.THESPAWN_API_KEY;
  if (spawnKey) {
    await submitSpawn(spawnKey);
  } else {
    console.log('○ Spawn: set THESPAWN_API_KEY in vault to auto-call API');
  }

  if (runSaid) {
    console.log('\n--- SAID (Solana) ---');
    try {
      execSync('node scripts/said-register-simple.mjs', { cwd: root, stdio: 'inherit' });
    } catch {
      console.log('SAID step incomplete — fund agent-wallet.json with ~0.02 SOL and re-run with --said');
    }
  } else {
    console.log('○ SAID: run with --said after funding agent-wallet.json (~0.02 SOL)');
  }

  console.log('\n--- Manual web directories (free) ---');
  for (const t of AGENT_DIRECTORY_WEB_TARGETS) {
    console.log(`  ${t.free ? 'FREE' : 'PAID'}  ${t.label}`);
    console.log(`       ${t.submitUrl}`);
    if (t.notes) console.log(`       ${t.notes}`);
  }

  printManualPack();

  if (openForms) {
    console.log('\n--- Opening submit forms in browser ---');
    openUrls(AGENT_DIRECTORY_WEB_TARGETS.map((t) => t.submitUrl));
  } else {
    console.log('\nTip: npm run submit:agent-directories -- --open');
  }

  console.log('\nAfter each manual submit → Admin → Agent registrations → Mark registered.\n');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
