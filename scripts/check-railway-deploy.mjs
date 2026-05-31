/** Check latest Railway deployment status for doxed-founders-website */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');
const SERVICE_ID = '1b9fb50f-305b-4424-8570-23fabe441dc7';
const ENV_ID = 'production'; // resolved below

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
  const json = await res.json();
  if (json.errors?.length) throw new Error(json.errors.map((e) => e.message).join('; '));
  return json.data;
}

const secrets = readDotEnv(vault);
const token = process.env.RAILWAY_TOKEN || secrets.RAILWAY_TOKEN;
if (!token) {
  console.error('No RAILWAY_TOKEN');
  process.exit(1);
}

const projects = await gql(
  token,
  `query {
    projects { edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name serviceInstances { edges { node {
        id latestDeployment { id status createdAt meta }
      } } } } } }
    } } }
  }`,
);

for (const p of projects.projects?.edges ?? []) {
  const project = p.node;
  const svc = project.services?.edges?.find((e) => e.node.name === 'doxed-founders-website')?.node;
  if (!svc) continue;
  console.log(`Project: ${project.name}`);
  for (const inst of svc.serviceInstances?.edges ?? []) {
    const dep = inst.node.latestDeployment;
    if (!dep) continue;
    console.log(JSON.stringify({
      deploymentId: dep.id,
      status: dep.status,
      createdAt: dep.createdAt,
      commit: dep.meta?.commitMessage ?? dep.meta?.image ?? dep.meta,
    }, null, 2));

    if (process.argv.includes('--logs') && dep.id) {
      const logs = await gql(
        token,
        `query($id: String!) {
          deploymentLogs(deploymentId: $id, limit: 100) { message severity timestamp }
        }`,
        { id: dep.id },
      );
      console.log('\n--- deploy logs (last 40) ---');
      for (const line of (logs.deploymentLogs ?? []).slice(-40)) {
        console.log(`${line.severity ?? 'info'} | ${line.message}`);
      }
    }
  }
}
