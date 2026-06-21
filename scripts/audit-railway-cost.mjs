/**
 * Audit Railway services: regions, replicas, resource limits, billing hints.
 * Read-only — does not change anything unless --apply-ram is passed.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');

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

const applyRam = process.argv.includes('--apply-ram');
const token =
  process.env.RAILWAY_TOKEN?.trim() ||
  readDotEnv(vault).RAILWAY_TOKEN?.trim();

if (!token) {
  console.error('Missing RAILWAY_TOKEN in vault/.env.x.secrets');
  process.exit(1);
}

const RECOMMENDED_RAM = {
  'doxed-founders-website': 1024,
  'btc-conservative-agent': 1024,
};

const data = await gql(
  token,
  `query {
    projects { edges { node {
      id name
      services { edges { node {
        id name
        serviceInstances { edges { node {
          id
          region
          numReplicas
          sleepApplication
          restartPolicyType
          nixpacksPlan
          railwayConfigFile
          latestDeployment { id status createdAt }
        } } }
      } } }
    } } }
  }`,
);

console.log('\n=== Railway cost audit ===\n');

let totalMemoryMb = 0;
const services = [];

for (const p of data.projects?.edges ?? []) {
  console.log(`Project: ${p.node.name}`);
  for (const s of p.node.services?.edges ?? []) {
    const svc = s.node;
    for (const inst of svc.serviceInstances?.edges ?? []) {
      const n = inst.node;
      const mem = n.nixpacksPlan?.memory ?? n.nixpacksPlan?.limits?.memory ?? null;
      const cpu = n.nixpacksPlan?.cpu ?? n.nixpacksPlan?.limits?.cpu ?? null;
      const replicas = n.numReplicas ?? 1;
      const memMb = mem ? Math.round(mem / 1024 / 1024) : 'default (~512–8192)';
      if (typeof memMb === 'number') totalMemoryMb += memMb * replicas;

      const row = {
        name: svc.name,
        serviceId: svc.id,
        instanceId: n.id,
        region: n.region ?? '(unset)',
        replicas,
        memoryMb: memMb,
        cpuVcpu: cpu ?? 'default',
        sleep: n.sleepApplication,
        deploy: n.latestDeployment?.status,
      };
      services.push(row);

      console.log(`  ${svc.name}`);
      console.log(`    region:     ${row.region}`);
      console.log(`    replicas:   ${replicas}`);
      console.log(`    memory:     ${typeof memMb === 'number' ? memMb + ' MB' : memMb}`);
      console.log(`    cpu:        ${row.cpuVcpu}`);
      console.log(`    sleep:      ${row.sleep}`);
      console.log(`    deploy:     ${row.deploy}`);
    }
  }
  console.log('');
}

console.log('--- Summary ---');
console.log(`Services: ${services.length}`);
console.log(`Configured RAM (known): ${totalMemoryMb || 'unknown — check Railway dashboard Settings → Resources'}`);
console.log('Duplicates @dcf/web, @dcf/api: run npm run cleanup:railway');

const bot = services.find((s) => s.name === 'btc-conservative-agent');
if (bot?.region?.includes('asia')) {
  console.log('\n⚠ Bot is in ASIA region (railway.toml multiRegionConfig).');
  console.log('  Bitfinex + Neon (US-east) may not need Singapore — US-west/east often cheaper.');
  console.log('  To change: Railway dashboard → btc-conservative-agent → Settings → Region');
  console.log('  Or remove [deploy.multiRegionConfig] from services/btc-conservative-agent/railway.toml');
}

console.log('\n--- Recommendations ---');
console.log('1. API (doxed-founders-website): try 512MB–1GB if stable');
console.log('2. Bot (btc-conservative-agent): keep 1GB min (heavy Python + Flask); 512MB may OOM');
console.log('3. Duplicates already removed if cleanup:railway reports clean');
console.log('4. ~$3/day ≈ 2 always-on services × ~1–2GB RAM each on usage billing');

if (applyRam) {
  console.log('\nRAM limits cannot be set via public Railway GraphQL API.');
  console.log('Use dashboard: Service → Settings → Resources → Memory/CPU');
} else {
  console.log('\nSet RAM in Railway dashboard → each service → Settings → Resources');
}
