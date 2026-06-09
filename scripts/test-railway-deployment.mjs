#!/usr/bin/env node
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');

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

const token =
  readDotEnv(join(vault, '.env.x.secrets')).RAILWAY_TOKEN ||
  readDotEnv(join(vault, '.env.x.secrets')).RAILWAY_API_TOKEN;
if (!token) {
  console.log('NO_TOKEN');
  process.exit(0);
}

async function gql(query, variables = {}) {
  const res = await fetch('https://backboard.railway.com/graphql/v2', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const json = await res.json();
  if (json.errors?.length) throw new Error(JSON.stringify(json.errors));
  return json.data;
}

const projects = await gql(`query {
  projects { edges { node {
    id name
    environments { edges { node { id name } } }
    services { edges { node { id name } } }
  } } }
}`);

let serviceId, environmentId;
for (const p of projects.projects.edges) {
  const env =
    p.node.environments.edges.find((e) => e.node.name === 'production')?.node ??
    p.node.environments.edges[0]?.node;
  const svc = p.node.services.edges.find((s) => s.node.name === 'btc-conservative-agent')?.node;
  if (svc && env) {
    serviceId = svc.id;
    environmentId = env.id;
    console.log('service', svc.name, serviceId, 'env', env.name);
    break;
  }
}

if (!serviceId) {
  console.log('service not found');
  process.exit(1);
}

const queries = [
  [`deployments list`, `query($input: DeploymentListInput!, $first: Int) {
    deployments(input: $input, first: $first) {
      edges { node { id status createdAt } }
    }
  }`, { input: { serviceId, environmentId }, first: 5 }],
  [`serviceInstance`, `query($environmentId: String!, $serviceId: String!) {
    serviceInstance(environmentId: $environmentId, serviceId: $serviceId) {
      latestDeployment { id status }
    }
  }`, { environmentId, serviceId }],
];

for (const [label, query, variables] of queries) {
  try {
    const data = await gql(query, variables);
    console.log(label, JSON.stringify(data, null, 2));
  } catch (e) {
    console.log(label, 'FAILED', e.message);
  }
}
