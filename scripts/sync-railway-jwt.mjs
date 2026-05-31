/**
 * Sync JWT_SECRET to Railway (fixes Google Authenticator on login).
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultDir = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const SERVICE_ID = '1b9fb50f-305b-4424-8570-23fabe441dc7';

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    map[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^["']|["']$/g, '');
  }
  return map;
}

async function gql(token, query, variables = {}) {
  const res = await fetch(GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const text = await res.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error(`Railway API error: ${text.slice(0, 200)}`);
  }
  if (json.errors?.length) throw new Error(json.errors.map((e) => e.message).join('; '));
  return json.data;
}

const x = readDotEnv(join(vaultDir, '.env.x.secrets'));
const vercel = readDotEnv(join(vaultDir, '.env.vercel.check'));
const selfHost = readDotEnv(join(vaultDir, '.env.self-host'));
const token = process.env.RAILWAY_TOKEN || x.RAILWAY_TOKEN;
const jwt = vercel.JWT_SECRET || selfHost.JWT_SECRET;

if (!token) {
  console.error('Missing RAILWAY_TOKEN');
  process.exit(1);
}
if (!jwt || jwt.length < 32) {
  console.error('Missing JWT_SECRET in vault');
  process.exit(1);
}

const projects = await gql(
  token,
  `query {
    projects { edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name } } }
    } } }
  }`,
);

const project = projects.projects?.edges
  ?.map((e) => e.node)
  ?.find((p) => p.services?.edges?.some((s) => s.node.id === SERVICE_ID));
if (!project) {
  console.error('Production project not found');
  process.exit(1);
}

const env =
  project.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
  project.environments?.edges?.[0]?.node;

console.log('Setting JWT_SECRET…');
await gql(token, `mutation($input: VariableUpsertInput!) { variableUpsert(input: $input) }`, {
  input: {
    projectId: project.id,
    environmentId: env.id,
    serviceId: SERVICE_ID,
    name: 'JWT_SECRET',
    value: jwt,
  },
});

console.log('Redeploying API…');
await gql(
  token,
  `mutation($serviceId: String!, $environmentId: String!) {
    serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
  }`,
  { serviceId: SERVICE_ID, environmentId: env.id },
);

console.log('JWT_SECRET synced. Wait ~60s, then Google Authenticator codes should work at login.');
