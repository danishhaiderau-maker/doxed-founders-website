/**
 * List Railway projects/services and trigger redeploy on production API services.
 * Requires RAILWAY_TOKEN in env or vault/.env.x.secrets
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultSecrets = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');

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

async function main() {
  const secrets = readDotEnv(vaultSecrets);
  const token =
    process.env.RAILWAY_TOKEN?.trim() ||
    process.env.RAILWAY_API_TOKEN?.trim() ||
    secrets.RAILWAY_TOKEN?.trim() ||
    secrets.RAILWAY_API_TOKEN?.trim();

  if (!token) {
    console.error('Missing RAILWAY_TOKEN. Add to vault/.env.x.secrets or run: railway login');
    process.exit(1);
  }

  const data = await gql(
    token,
    `query {
      projects { edges { node {
        id name
        environments { edges { node { id name } } }
        services { edges { node { id name } } }
      } } }
    }`,
  );

  const projects = data.projects?.edges?.map((e) => e.node) ?? [];
  console.log(`Found ${projects.length} Railway project(s)\n`);

  const apiServiceNames = new Set(['doxed-founders-website', '@dcf/api', 'dcf/api', 'api']);

  for (const project of projects) {
    const env =
      project.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
      project.environments?.edges?.[0]?.node;
    if (!env) continue;

    console.log(`Project: ${project.name} (${project.id})`);

    for (const svcEdge of project.services?.edges ?? []) {
      const svc = svcEdge.node;
      const isApi =
        apiServiceNames.has(svc.name) ||
        svc.name.includes('doxed-founders') ||
        svc.name.includes('dcf/api') ||
        svc.name === '@dcf/api';

      if (!isApi) {
        console.log(`  skip ${svc.name} (not API)`);
        continue;
      }

      console.log(`  redeploy ${svc.name} (${svc.id})…`);
      try {
        await gql(
          token,
          `mutation($serviceId: String!, $environmentId: String!) {
            serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
          }`,
          { serviceId: svc.id, environmentId: env.id },
        );
        console.log(`  ✓ redeploy triggered`);
      } catch (err) {
        console.error(`  ✗ ${err.message}`);
      }
    }
    console.log('');
  }

  console.log('Wait 3–5 min, then check: https://doxed-founders-website-production.up.railway.app/api/health');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
