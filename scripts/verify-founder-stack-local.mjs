#!/usr/bin/env node
/**
 * verify-founder-stack-local.mjs
 *
 * Product-usable local path check (no IDE chrome / Electron binary testing):
 *   FounderVault node-config.json → AI Gateway /v1/models with Node bearer
 *   (no OAuth). Optional: local API health if :4000 is up.
 *
 * Usage:
 *   node scripts/verify-founder-stack-local.mjs
 *   npm run verify:founder-stack-local
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const vaultPath = path.join(os.homedir(), 'FounderVault', 'node-config.json');
const checks = [];

function pass(name, detail) {
  checks.push({ name, ok: true, detail });
  console.log(`  PASS  ${name} — ${detail}`);
}
function fail(name, detail) {
  checks.push({ name, ok: false, detail });
  console.error(`  FAIL  ${name} — ${detail}`);
}

console.log('[verify-founder-stack-local]');
console.log(`  vault=${vaultPath}`);

let vault = null;
if (!fs.existsSync(vaultPath)) {
  fail('vault_file', '~/FounderVault/node-config.json missing — pair Founder Node first');
} else {
  try {
    vault = JSON.parse(fs.readFileSync(vaultPath, 'utf8'));
    if (!vault?.apiBaseUrl || !vault?.nodeId || !vault?.nodeToken) {
      fail('vault_fields', 'node-config.json missing apiBaseUrl / nodeId / nodeToken');
      vault = null;
    } else {
      pass(
        'vault_file',
        `nodeId=${vault.nodeId} apiBaseUrl=${vault.apiBaseUrl} (OAuth not required for IDE chat)`,
      );
    }
  } catch (err) {
    fail('vault_file', `unreadable: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function probe(url, headers = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 12_000);
  try {
    const res = await fetch(url, { headers, signal: ctrl.signal });
    const text = await res.text();
    return { status: res.status, text: text.slice(0, 400) };
  } finally {
    clearTimeout(t);
  }
}

// Local API optional
try {
  const local = await probe('http://127.0.0.1:4000/api/health');
  if (local.status >= 200 && local.status < 300) {
    pass('local_api_health', `HTTP ${local.status}`);
  } else {
    fail('local_api_health', `HTTP ${local.status} ${local.text}`);
  }
} catch (err) {
  fail(
    'local_api_health',
    `not reachable (${err instanceof Error ? err.message : String(err)}) — start with npm run start:api:prod or npm run dev:api`,
  );
}

if (vault) {
  const base = String(vault.apiBaseUrl).replace(/\/$/, '');
  const bearer = `fos_${vault.nodeId}:${vault.nodeToken}`;
  const modelsUrl = `${base}/api/v1/models`;
  try {
    const res = await probe(modelsUrl, { Authorization: `Bearer ${bearer}` });
    if (res.status >= 200 && res.status < 300) {
      pass('gateway_models_no_oauth', `GET ${modelsUrl} → HTTP ${res.status}`);
    } else if (res.status === 401 || res.status === 403) {
      fail(
        'gateway_models_no_oauth',
        `HTTP ${res.status} — Node token rejected; re-pair Founder Node (not an IDE OAuth issue)`,
      );
    } else {
      fail('gateway_models_no_oauth', `HTTP ${res.status} ${res.text}`);
    }
  } catch (err) {
    fail(
      'gateway_models_no_oauth',
      `unreachable ${modelsUrl}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

const failed = checks.filter((c) => !c.ok).length;
const passed = checks.filter((c) => c.ok).length;
console.log('');
console.log(`[verify-founder-stack-local] ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.log('Next: pair Node → ensure API/gateway up → re-run. IDE black window is a separate agent.');
  process.exit(1);
}
console.log('Local vault → gateway path OK (chat usable once IDE chrome is fixed).');
process.exit(0);
