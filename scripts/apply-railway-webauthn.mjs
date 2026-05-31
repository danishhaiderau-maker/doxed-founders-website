/**
 * Set WebAuthn origin/RP ID on Railway API for passkey login on doxxedcrypto.digital.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const GQL = 'https://backboard.railway.com/graphql/v2';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.x.secrets');
const SERVICE_ID = '1b9fb50f-305b-4424-8570-23fabe441dc7';

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
  console.error('Missing RAILWAY_TOKEN');
  process.exit(1);
}

const vars = {
  WEBAUTHN_ORIGIN: 'https://doxxedcrypto.digital',
  WEBAUTHN_RP_ID: 'doxxedcrypto.digital',
  WEBAUTHN_RP_NAME: 'Doxxed Crypto Founder OS',
  PUBLIC_SITE_URL: 'https://doxxedcrypto.digital',
};

const projects = await gql(
  token,
  `query {
    projects { edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name } } }
    } } }
  }`,
);

const project = projects.projects?.edges
  ?.map((e) => e.node)
  ?.find((p) => p.services?.edges?.some((s) => s.node.id === SERVICE_ID));
if (!project) {
  console.error('Production project not found');
  process.exit(1);
}

const env =
  project.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
  project.environments?.edges?.[0]?.node;

for (const [name, value] of Object.entries(vars)) {
  console.log(`Setting ${name}…`);
  await gql(
    token,
    `mutation($input: VariableUpsertInput!) { variableUpsert(input: $input) }`,
    {
      input: {
        projectId: project.id,
        environmentId: env.id,
        serviceId: SERVICE_ID,
        name,
        value,
      },
    },
  );
}

console.log('WebAuthn vars applied. Redeploy API: npm run redeploy:railway');
