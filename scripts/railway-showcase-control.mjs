#!/usr/bin/env node
/** Test Railway deploymentStop + deploymentRestart for btc-conservative-agent */
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
  console.error('NO_TOKEN');
  process.exit(1);
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

const action = process.argv[2] ?? 'status';
const serviceId = 'bea2a356-d94f-44bf-82b4-c187f8063857';
const environmentId = process.argv[3] ?? '';

async function latestDeploymentId() {
  const data = await gql(
    `query($environmentId: String!, $serviceId: String!) {
      serviceInstance(environmentId: $environmentId, serviceId: $serviceId) {
        latestDeployment { id status }
      }
    }`,
    { environmentId: environmentId || (await resolveEnv()), serviceId },
  );
  return data.serviceInstance?.latestDeployment;
}

async function resolveEnv() {
  const projects = await gql(`query { projects { edges { node {
    environments { edges { node { id name } } }
    services { edges { node { id name } } }
  } } } }`);
  for (const p of projects.projects.edges) {
    const env =
      p.node.environments.edges.find((e) => e.node.name === 'production')?.node ??
      p.node.environments.edges[0]?.node;
    const svc = p.node.services.edges.find((s) => s.node.name === 'btc-conservative-agent')?.node;
    if (env && svc) return env.id;
  }
  throw new Error('env not found');
}

if (action === 'status') {
  console.log(JSON.stringify(await latestDeploymentId(), null, 2));
} else if (action === 'stop') {
  const dep = await latestDeploymentId();
  console.log('Stopping', dep);
  const r = await gql(`mutation($id: String!) { deploymentStop(id: $id) }`, { id: dep.id });
  console.log('stop result', r);
} else if (action === 'start') {
  const dep = await latestDeploymentId();
  console.log('Restarting', dep);
  try {
    const r = await gql(`mutation($id: String!) { deploymentRestart(id: $id) }`, { id: dep.id });
    console.log('restart result', r);
  } catch (e) {
    console.log('restart failed, redeploying…', e.message);
    const envId = environmentId || (await resolveEnv());
    const r = await gql(
      `mutation($serviceId: String!, $environmentId: String!) {
        serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
      }`,
      { serviceId, environmentId: envId },
    );
    console.log('redeploy result', r);
  }
}
