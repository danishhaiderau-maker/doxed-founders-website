/**
 * Upsert DATABASE_URL + schema push flags and redeploy API + btc bot services.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const GQL = 'https://backboard.railway.com/graphql/v2';

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    let v = trimmed.slice(idx + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    map[trimmed.slice(0, idx).trim()] = v;
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

const xSecrets = readDotEnv(join(vault, '.env.x.secrets'));
const neon = readDotEnv(join(vault, '.env.neon'));
const vercelCheck = readDotEnv(join(vault, '.env.vercel.check'));

const token =
  xSecrets.RAILWAY_TOKEN?.trim() ||
  xSecrets.RAILWAY_API_TOKEN?.trim() ||
  process.env.RAILWAY_TOKEN?.trim();
const dbUrl = neon.DATABASE_URL || vercelCheck.DATABASE_URL;
const jwtSecret = vercelCheck.JWT_SECRET?.trim();

if (!token || !dbUrl) {
  console.error('Missing RAILWAY_TOKEN or DATABASE_URL in vault');
  process.exit(1);
}
if (!jwtSecret || jwtSecret.length < 32) {
  console.error('Missing JWT_SECRET (32+ chars) in vault/.env.vercel.check');
  process.exit(1);
}

const cors =
  'https://doxxedcrypto.digital,https://www.doxxedcrypto.digital,https://doxed-founders-website.vercel.app';
const apiVars = {
  DATABASE_URL: dbUrl,
  JWT_SECRET: jwtSecret,
  NODE_ENV: 'production',
  PRISMA_DB_PUSH: 'true',
  PRISMA_SCHEMA: 'prisma/schema.prisma',
  CORS_ORIGINS: cors,
};

const TARGET_SERVICES = new Set(['doxed-founders-website', 'btc-conservative-agent']);

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

for (const project of data.projects?.edges?.map((e) => e.node) ?? []) {
  const env =
    project.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    project.environments?.edges?.[0]?.node;
  if (!env) continue;

  for (const svcEdge of project.services?.edges ?? []) {
    const svc = svcEdge.node;
    if (!TARGET_SERVICES.has(svc.name)) continue;

    const variables = svc.name === 'doxed-founders-website' ? apiVars : { PORT: '5000' };
    console.log(`Sync ${project.name} / ${svc.name}…`);
    await gql(
      token,
      `mutation($input: VariableCollectionUpsertInput!) {
        variableCollectionUpsert(input: $input)
      }`,
      {
        input: {
          projectId: project.id,
          environmentId: env.id,
          serviceId: svc.id,
          variables,
          replace: false,
        },
      },
    );
    await gql(
      token,
      `mutation($serviceId: String!, $environmentId: String!) {
        serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
      }`,
      { serviceId: svc.id, environmentId: env.id },
    );
    console.log(`  ✓ ${svc.name} redeploy triggered`);
  }
}

console.log('\nDone. Wait 3–5 min for Railway builds.');
