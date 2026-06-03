/**
 * Sync Vercel + Railway production to latest repo settings.
 * Reads secrets from ../doxedcryptofounder-secrets/vault (never commit).
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execSync, spawnSync } from 'child_process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(dirname(root), 'doxedcryptofounder-secrets', 'vault');

const RAILWAY_API = 'https://doxed-founders-website-production.up.railway.app';
const SITE_URL = 'https://doxxedcrypto.digital';
const RAILWAY_GQL = 'https://backboard.railway.com/graphql/v2';

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const k = trimmed.slice(0, idx).trim();
    let v = trimmed.slice(idx + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    if (v) map[k] = v;
  }
  return map;
}

function run(cmd, opts = {}) {
  console.log(`> ${cmd}`);
  return execSync(cmd, { cwd: root, stdio: 'inherit', ...opts });
}

function runCapture(cmd) {
  return execSync(cmd, { cwd: root, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
}

async function railwayGql(token, query, variables = {}) {
  const res = await fetch(RAILWAY_GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors?.length) throw new Error(json.errors.map((e) => e.message).join('; '));
  return json.data;
}

async function syncRailway(token, vars) {
  const data = await railwayGql(
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
  if (!target) throw new Error('doxed-founders-website Railway service not found');

  const env =
    target.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    target.environments?.edges?.[0]?.node;
  const service =
    target.services?.edges?.find((e) => e.node.name === 'doxed-founders-website')?.node;
  if (!env || !service) throw new Error('Missing Railway env/service');

  await railwayGql(
    token,
    `mutation($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }`,
    {
      input: {
        projectId: target.id,
        environmentId: env.id,
        serviceId: service.id,
        variables: vars,
        replace: false,
      },
    },
  );

  await railwayGql(
    token,
    `mutation($serviceId: String!, $environmentId: String!) {
      serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
    }`,
    { serviceId: service.id, environmentId: env.id },
  );

  console.log(`Railway redeploy triggered on ${target.name} / ${service.name}`);
}

function upsertVercelEnv(name, value, environments = ['production'], sensitive = false) {
  if (!value?.trim()) {
    console.warn(`Skip empty Vercel var: ${name}`);
    return;
  }
  for (const env of environments) {
    const args = ['env', 'add', name, env, '--force'];
    if (sensitive) args.push('--sensitive');
    const add = spawnSync('vercel', args, {
      cwd: root,
      input: `${value.trim()}\n`,
      encoding: 'utf8',
      shell: process.platform === 'win32',
    });
    if (add.status !== 0) {
      const stderr = [add.stderr, add.stdout].filter(Boolean).join('\n').trim();
      throw new Error(`Failed to set Vercel ${name} (${env}): ${stderr || 'exit ' + add.status}`);
    }
    console.log(`Vercel ${name} (${env}) updated`);
  }
}

async function main() {
  const neon = readDotEnv(join(vault, '.env.neon'));
  const xSecrets = readDotEnv(join(vault, '.env.x.secrets'));
  const vercelCheck = readDotEnv(join(vault, '.env.vercel.check'));
  const localEnv = readDotEnv(join(vault, '.env'));

  const apiUrl = xSecrets.API_URL?.trim() || RAILWAY_API;
  const dbUrl = neon.DATABASE_URL || vercelCheck.DATABASE_URL;
  const jwtSecret = vercelCheck.JWT_SECRET || 'rhTQ807wvteYvFpgBhz0mwwD2y6EaH0JNKGRwPbbEDs=';
  const nextAuthSecret = vercelCheck.NEXTAUTH_SECRET?.trim() || jwtSecret;
  const googleId =
    vercelCheck.GOOGLE_CLIENT_ID?.trim() ||
    localEnv.GOOGLE_CLIENT_ID?.trim() ||
    '84665204636-sl9vqeu6eqr0gg3nrgo490vvvfagsfb0.apps.googleusercontent.com';
  const googleSecret =
    vercelCheck.GOOGLE_CLIENT_SECRET?.trim() || localEnv.GOOGLE_CLIENT_SECRET?.trim() || '';

  console.log('\n=== Sync production cloud ===\n');

  try {
    runCapture('vercel whoami');
  } catch {
    console.error('Vercel CLI not logged in');
    process.exit(1);
  }

  const vercelVars = {
    API_URL: apiUrl,
    NEXTAUTH_URL: SITE_URL,
    NEXTAUTH_SECRET: nextAuthSecret,
    PUBLIC_SITE_URL: SITE_URL,
    CORS_ORIGINS: `${SITE_URL},https://www.doxxedcrypto.digital,https://doxed-founders-website.vercel.app`,
  };
  if (googleId) vercelVars.GOOGLE_CLIENT_ID = googleId;
  if (googleSecret) vercelVars.GOOGLE_CLIENT_SECRET = googleSecret;

  const sensitiveVercel = new Set(['NEXTAUTH_SECRET', 'GOOGLE_CLIENT_SECRET']);
  for (const [name, value] of Object.entries(vercelVars)) {
    if (value?.trim()) upsertVercelEnv(name, value.trim(), ['production'], sensitiveVercel.has(name));
  }

  console.log('\nDeploying Vercel production...');
  run('vercel deploy --prod --yes');

  const railwayToken =
    xSecrets.RAILWAY_TOKEN?.trim() ||
    process.env.RAILWAY_TOKEN?.trim() ||
    process.env.RAILWAY_API_TOKEN?.trim();

  if (railwayToken && dbUrl) {
    const cors = `${SITE_URL},https://www.doxxedcrypto.digital,https://doxed-founders-website.vercel.app`;
    await syncRailway(railwayToken, {
      DATABASE_URL: dbUrl,
      JWT_SECRET: jwtSecret,
      NODE_ENV: 'production',
      PRISMA_DB_PUSH: 'true',
      PRISMA_SCHEMA: 'prisma/schema.prisma',
      CORS_ORIGINS: cors,
    });
  } else {
    console.warn('\nSkip Railway API sync — set RAILWAY_TOKEN in vault/.env.x.secrets');
  }

  console.log('\n=== Smoke test ===\n');
  run(`node scripts/smoke-test.mjs`, {
    env: { ...process.env, API_URL: SITE_URL },
  });

  console.log('\n=== Done ===');
  console.log(`Site: ${SITE_URL}`);
  console.log(`API:  ${apiUrl}/api/health`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
