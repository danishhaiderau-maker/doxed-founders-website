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
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    map[t.slice(0, i).trim()] = v;
  }
  return map;
}

const token = readDotEnv(join(vault, '.env.x.secrets')).RAILWAY_TOKEN?.trim();
const serviceId = process.argv[2] ?? '1b9fb50f-305b-4424-8570-23fabe441dc7';
const envId = '6f656983-32eb-4d20-8430-044bcd306aec';

const query = `query {
  deployments(first: 30, input: { serviceId: "${serviceId}", environmentId: "${envId}" }) {
    edges { node { id status createdAt meta } }
  }
}`;

const res = await fetch('https://backboard.railway.com/graphql/v2', {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ query }),
});
const json = await res.json();
const deps = json.data?.deployments?.edges?.map((e) => e.node) ?? [];

console.log('\n=== Deployments ===');
for (const d of deps) {
  const extra = d.meta?.skippedReason ?? d.meta?.commitMessage?.slice(0, 50) ?? '';
  console.log(`${d.status.padEnd(12)} ${d.createdAt.slice(0, 19)} ${extra}`);
}

const lastReal = deps.find((d) => !['SKIPPED'].includes(d.status));
if (lastReal) {
  console.log('\nLatest non-skipped:', lastReal.id, lastReal.status);
  const logQ = `query ($id: String!) { deploymentLogs(deploymentId: $id, limit: 40) { message severity } }`;
  const logRes = await fetch('https://backboard.railway.com/graphql/v2', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: logQ, variables: { id: lastReal.id } }),
  });
  const logs = (await logRes.json()).data?.deploymentLogs ?? [];
  console.log('\n=== Last 40 log lines ===');
  for (const l of logs.slice(-40)) console.log(`[${l.severity}] ${l.message}`);
}
