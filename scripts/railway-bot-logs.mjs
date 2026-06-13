#!/usr/bin/env node
/** Fetch latest Railway deployment logs for btc-conservative-agent. */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const GQL = 'https://backboard.railway.com/graphql/v2';
const SERVICE = process.argv[2] ?? 'btc-conservative-agent';

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
const token = xSecrets.RAILWAY_TOKEN?.trim() || process.env.RAILWAY_TOKEN?.trim();
if (!token) {
  console.error('Missing RAILWAY_TOKEN in vault/.env.x.secrets');
  process.exit(1);
}

const data = await gql(
  token,
  `query {
    projects { edges { node {
      id name
      services { edges { node {
        id name
        serviceInstances { edges { node {
          id
          latestDeployment { id status staticUrl createdAt }
        } } }
      } } }
    } } }
  }`,
);

let found = false;
for (const project of data.projects?.edges?.map((e) => e.node) ?? []) {
  for (const svcEdge of project.services?.edges ?? []) {
    const svc = svcEdge.node;
    if (svc.name !== SERVICE) continue;
    found = true;
    const inst = svc.serviceInstances?.edges?.[0]?.node;
    const dep = inst?.latestDeployment;
    console.log(`\n=== ${project.name} / ${svc.name} ===`);
    console.log(`Deployment: ${dep?.id ?? 'none'}  status=${dep?.status ?? '?'}  url=${dep?.staticUrl ?? '?'}`);
    if (!dep?.id) continue;

    try {
      const logs = await gql(
        token,
        `query ($id: String!) {
          deploymentLogs(deploymentId: $id, limit: 100) {
            edges { node { message severity timestamp } }
          }
        }`,
        { id: dep.id },
      );
      const lines = logs.deploymentLogs?.edges?.map((e) => e.node) ?? [];
      console.log(`\n--- Last ${lines.length} log lines ---\n`);
      for (const l of lines.slice(-60)) {
        const ts = l.timestamp?.slice(11, 19) ?? '';
        console.log(`${ts} [${l.severity ?? '?'}] ${(l.message ?? '').slice(0, 300)}`);
      }
    } catch (err) {
      console.warn('deploymentLogs query failed:', err instanceof Error ? err.message : err);
    }
  }
}

if (!found) {
  console.error(`Service not found: ${SERVICE}`);
  process.exit(1);
}
