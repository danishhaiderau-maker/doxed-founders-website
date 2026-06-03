import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultSecrets = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');

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

const secrets = readDotEnv(vaultSecrets);
const token = process.env.RAILWAY_TOKEN?.trim() || secrets.RAILWAY_TOKEN?.trim();
if (!token) {
  console.error('no token');
  process.exit(1);
}

const serviceId = '1b9fb50f-305b-4424-8570-23fabe441dc7';

const queries = [
  ['service', `query { service(id: "${serviceId}") { id name repoTriggers { edges { node { repository branch } } } } }`],
  [
    'deployments',
    `query {
      deployments(
        first: 8
        input: { serviceId: "${serviceId}", environmentId: "6f656983-32eb-4d20-8430-044bcd306aec" }
      ) {
        edges { node { id status createdAt url meta } }
      }
    }`,
  ],
];

for (const [label, query] of queries) {
  const res = await fetch('https://backboard.railway.com/graphql/v2', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  const json = await res.json();
  console.log(`\n=== ${label} ===\n`, JSON.stringify(json, null, 2));
}
