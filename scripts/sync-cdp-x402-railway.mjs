#!/usr/bin/env node
/** Push CDP x402 keys from vault to Railway API service and redeploy. */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(dirname(root), 'doxedcryptofounder-secrets', 'vault');
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
const localEnv = readDotEnv(join(vault, '.env'));
const token = xSecrets.RAILWAY_TOKEN?.trim() || xSecrets.RAILWAY_API_TOKEN?.trim();
const cdpKeyId = localEnv.CDP_API_KEY_ID?.trim();
const cdpKeySecret = localEnv.CDP_API_KEY_SECRET?.trim();

if (!token) {
  console.error('Missing RAILWAY_TOKEN in vault/.env.x.secrets');
  process.exit(1);
}
if (!cdpKeyId || !cdpKeySecret) {
  console.error('Missing CDP_API_KEY_ID/SECRET in vault/.env');
  process.exit(1);
}

const vars = {
  CDP_API_KEY_ID: cdpKeyId,
  CDP_API_KEY_SECRET: cdpKeySecret,
  X402_FACILITATOR_URL: 'https://api.cdp.coinbase.com/platform/v2/x402',
  X402_SIGNAL_ENABLED: 'true',
};

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

const target = data.projects?.edges?.map((e) => e.node).find((p) =>
  p.services?.edges?.some((s) => s.node.name === 'doxed-founders-website'),
);
if (!target) throw new Error('doxed-founders-website not found');

const env =
  target.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
  target.environments?.edges?.[0]?.node;
const service = target.services?.edges?.find((e) => e.node.name === 'doxed-founders-website')?.node;
if (!env || !service) throw new Error('Missing Railway env/service');

await gql(token, `mutation($input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $input) }`, {
  input: {
    projectId: target.id,
    environmentId: env.id,
    serviceId: service.id,
    variables: vars,
    replace: false,
  },
});

await gql(token, `mutation($serviceId: String!, $environmentId: String!) { serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId) }`, {
  serviceId: service.id,
  environmentId: env.id,
});

console.log('✓ CDP x402 vars synced to Railway — redeploy triggered');
