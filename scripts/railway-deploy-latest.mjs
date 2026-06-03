/**
 * Deploy latest local source to Railway production API (full build, not redeploy-only).
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'child_process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultSecrets = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');
const GQL = 'https://backboard.railway.com/graphql/v2';

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
    secrets.RAILWAY_TOKEN?.trim() ||
    secrets.RAILWAY_API_TOKEN?.trim();
  if (!token) {
    console.error('Missing RAILWAY_TOKEN in vault/.env.x.secrets');
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
  const target = projects.find((p) =>
    p.services?.edges?.some((s) => s.node.name === 'doxed-founders-website'),
  );
  if (!target) throw new Error('doxed-founders-website project not found');

  const env =
    target.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    target.environments?.edges?.[0]?.node;
  const service = target.services?.edges?.find((e) => e.node.name === 'doxed-founders-website')?.node;
  if (!env || !service) throw new Error('Missing env/service');

  console.log(`Deploying from local source → ${target.name} / ${service.name}`);
  console.log(`Project=${target.id} Environment=${env.id} Service=${service.id}\n`);

  const result = spawnSync(
    'railway',
    ['up', '--detach', '--ci'],
    {
      cwd: root,
      stdio: 'inherit',
      shell: process.platform === 'win32',
      env: {
        ...process.env,
        RAILWAY_TOKEN: token,
        RAILWAY_PROJECT_ID: target.id,
        RAILWAY_ENVIRONMENT_ID: env.id,
        RAILWAY_SERVICE_ID: service.id,
      },
    },
  );

  if (result.status !== 0) {
    console.error('\nrailway up failed — check token or run: railway login');
    process.exit(result.status ?? 1);
  }

  console.log('\nBuild uploaded. Wait 5–8 min, then:');
  console.log('  curl https://doxed-founders-website-production.up.railway.app/api/projects/platform/adoption-metrics?days=14');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
