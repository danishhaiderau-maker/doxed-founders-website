#!/usr/bin/env node
/**
 * Verify btc-conservative-agent is gone from Railway; optionally delete if still present.
 * Usage: node scripts/verify-railway-bot-service.mjs [--delete]
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { RAILWAY_BOT_SERVICE } from './home-bot-config.mjs';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultSecrets = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');
const deleteIfPresent = process.argv.includes('--delete');
const LEGACY_URL = 'https://btc-conservative-agent-production.up.railway.app';

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    map[t.slice(0, i).trim()] = v;
  }
  return map;
}

async function gql(token, query, variables = {}) {
  const res = await fetch(GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors?.length) throw new Error(json.errors.map((e) => e.message).join('; '));
  return json.data;
}

async function deleteService(token, serviceId, serviceName) {
  for (const [label, query] of [
    ['serviceDelete', `mutation($id: String!) { serviceDelete(id: $id) }`],
    ['serviceRemove', `mutation($id: String!) { serviceRemove(id: $id) }`],
  ]) {
    try {
      await gql(token, query, { id: serviceId });
      console.log(`✓ Deleted ${serviceName} via ${label}`);
      return true;
    } catch (err) {
      if (!/Cannot query field|Unknown type|not found/i.test(err.message)) {
        console.warn(`  ${label}: ${err.message}`);
      }
    }
  }
  return false;
}

async function checkLegacyUrl() {
  try {
    const res = await fetch(`${LEGACY_URL}/health`, { signal: AbortSignal.timeout(12_000) });
    return { up: res.ok, status: res.status };
  } catch (err) {
    return { up: false, error: err instanceof Error ? err.message : String(err) };
  }
}

async function main() {
  console.log('\n=== Railway showcase bot service check ===\n');

  const token =
    process.env.RAILWAY_TOKEN?.trim() ||
    readDotEnv(vaultSecrets).RAILWAY_TOKEN?.trim();

  let found = null;
  if (token) {
    const data = await gql(
      token,
      `query {
        projects { edges { node {
          id name
          services { edges { node { id name } } }
        } } }
      }`,
    );
    for (const p of data.projects?.edges?.map((e) => e.node) ?? []) {
      const svc = p.services?.edges?.find((e) => e.node.name === RAILWAY_BOT_SERVICE)?.node;
      if (svc) {
        found = { project: p.name, ...svc };
        break;
      }
    }
  } else {
    console.warn('No RAILWAY_TOKEN — skipping GraphQL service list');
  }

  if (found) {
    console.log(`✗ Service still in Railway: ${found.name} (${found.id}) in project "${found.project}"`);
    if (deleteIfPresent && token) {
      const ok = await deleteService(token, found.id, found.name);
      if (!ok) {
        console.error('Could not delete via API — remove manually in Railway dashboard');
        process.exit(1);
      }
      found = null;
    } else if (!deleteIfPresent) {
      console.log('  Re-run with --delete to remove via API');
    }
  } else {
    console.log(`✓ No "${RAILWAY_BOT_SERVICE}" service in Railway projects`);
  }

  const urlCheck = await checkLegacyUrl();
  if (urlCheck.up) {
    console.log(`✗ Legacy URL still responds: ${LEGACY_URL} → HTTP ${urlCheck.status}`);
    console.log('  DNS/cache may linger briefly after delete; confirm Railway dashboard shows no service.');
  } else {
    console.log(`✓ Legacy URL down: ${LEGACY_URL}${urlCheck.error ? ` (${urlCheck.error})` : ''}`);
  }

  console.log('');
  process.exit(found && !deleteIfPresent ? 1 : 0);
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
