#!/usr/bin/env node
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const GQL = 'https://backboard.railway.com/graphql/v2';

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    map[t.slice(0, i).trim()] = v;
  }
  return map;
}

async function gql(token, query, variables = {}) {
  const res = await fetch(GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  return res.json();
}

const token = readDotEnv(join(vault, '.env.x.secrets')).RAILWAY_TOKEN?.trim();
const depId = process.argv[2] ?? 'bcfb7d95-9eb9-4886-b971-a4d82e8fbfd9';

const queries = [
  ['deployment', `query ($id: String!) { deployment(id: $id) { id status staticUrl createdAt meta } }`, { id: depId }],
  [
    'logs',
    `query ($id: String!) { deploymentLogs(deploymentId: $id, limit: 80) { message severity timestamp } }`,
    { id: depId },
  ],
  [
    'buildLogs',
    `query ($id: String!) { buildLogs(deploymentId: $id, limit: 80) { message severity timestamp } }`,
    { id: depId },
  ],
];

for (const [label, query, variables] of queries) {
  const json = await gql(token, query, variables);
  console.log('\n===', label, '===');
  if (json.errors) console.log(JSON.stringify(json.errors, null, 2));
  else console.log(JSON.stringify(json.data, null, 2).slice(0, 8000));
}
