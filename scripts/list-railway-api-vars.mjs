#!/usr/bin/env node
/** List Railway projects/services and CDP var presence per API service. */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const vault = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'doxedcryptofounder-secrets', 'vault');
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
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    map[t.slice(0, i).trim()] = v;
  }
  return map;
}

const token = readDotEnv(join(vault, '.env.x.secrets')).RAILWAY_TOKEN?.trim();
const res = await fetch(GQL, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({
    query: `query { projects { edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name } } }
    } } } }`,
  }),
});
const projects = (await res.json()).data.projects.edges.map((e) => e.node);

for (const p of projects) {
  const env = p.environments.edges.find((e) => e.node.name === 'production')?.node ?? p.environments.edges[0]?.node;
  if (!env) continue;
  for (const s of p.services.edges.map((e) => e.node)) {
    if (!s.name.includes('doxed') && !s.name.includes('founders')) continue;
    const vq = await fetch(GQL, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: `query($projectId: String!, $environmentId: String!, $serviceId: String!) {
          variables(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId)
        }`,
        variables: { projectId: p.id, environmentId: env.id, serviceId: s.id },
      }),
    });
    const vars = (await vq.json()).data?.variables ?? {};
    console.log(`\n${p.name} / ${s.name}:`);
    console.log('  CDP_ID:', vars.CDP_API_KEY_ID ? 'yes' : 'no');
    console.log('  X402_FACILITATOR:', vars.X402_FACILITATOR_URL ?? '(unset)');
  }
}
