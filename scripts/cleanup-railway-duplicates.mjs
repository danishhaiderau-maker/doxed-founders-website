/**
 * Remove duplicate Railway services that fail healthchecks (@dcf/web, @dcf/api).
 * Web runs on Vercel; production API is doxed-founders-website only.
 *
 * Requires RAILWAY_TOKEN in vault/.env.x.secrets or env.
 * Create at https://railway.app/account/tokens
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultSecrets = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');

/** Never delete the real production API service. */
const PROTECTED = new Set(['doxed-founders-website']);

/** Duplicate / misconfigured services — safe to delete. */
const DELETE_NAMES = new Set(['@dcf/web', '@dcf/api', 'dcf/web', 'dcf/api']);

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    map[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, '');
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
  const mutations = [
    {
      label: 'serviceDelete',
      query: `mutation($id: String!) { serviceDelete(id: $id) }`,
      variables: { id: serviceId },
    },
    {
      label: 'serviceRemove',
      query: `mutation($id: String!) { serviceRemove(id: $id) }`,
      variables: { id: serviceId },
    },
  ];

  for (const m of mutations) {
    try {
      await gql(token, m.query, m.variables);
      console.log(`  ✓ deleted ${serviceName} via ${m.label}`);
      return true;
    } catch (err) {
      if (!/Cannot query field|Unknown type|not found/i.test(err.message)) {
        console.warn(`  ${m.label}: ${err.message}`);
      }
    }
  }
  throw new Error(`Could not delete ${serviceName} — delete manually in Railway dashboard`);
}

async function main() {
  const secrets = readDotEnv(vaultSecrets);
  const token =
    process.env.RAILWAY_TOKEN?.trim() ||
    process.env.RAILWAY_API_TOKEN?.trim() ||
    secrets.RAILWAY_TOKEN?.trim() ||
    secrets.RAILWAY_API_TOKEN?.trim();

  if (!token) {
    console.error(`
Missing RAILWAY_TOKEN.

Manual fix (2 minutes):
  1. Railway dashboard → @dcf/web → Settings → Delete Service
  2. Railway dashboard → @dcf/api → Settings → Delete Service
  Keep: doxed-founders-website (already SUCCESS — this is production)

Why they fail: no DATABASE_URL / JWT_SECRET. Web belongs on Vercel, not Railway.

Add token for automated cleanup:
  vault/.env.x.secrets → RAILWAY_TOKEN=...
  npm run cleanup:railway
`);
    process.exit(1);
  }

  const data = await gql(
    token,
    `query {
      projects { edges { node {
        id name
        services { edges { node { id name } } }
      } } }
    }`,
  );

  const projects = data.projects?.edges?.map((e) => e.node) ?? [];
  const target = projects.find((p) =>
    p.services?.edges?.some((s) => s.node.name === 'doxed-founders-website'),
  );

  if (!target) {
    throw new Error('Project with doxed-founders-website not found');
  }

  console.log(`Project: ${target.name}\n`);

  let deleted = 0;
  for (const svcEdge of target.services?.edges ?? []) {
    const svc = svcEdge.node;
    if (PROTECTED.has(svc.name)) {
      console.log(`  keep ${svc.name} (production API)`);
      continue;
    }
    if (!DELETE_NAMES.has(svc.name)) {
      console.log(`  skip ${svc.name} (not a known duplicate)`);
      continue;
    }
    console.log(`  deleting ${svc.name} (${svc.id})…`);
    await deleteService(token, svc.id, svc.name);
    deleted += 1;
  }

  console.log(`\nDone. Removed ${deleted} duplicate service(s).`);
  console.log('Verify: https://doxed-founders-website-production.up.railway.app/api/health');
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
