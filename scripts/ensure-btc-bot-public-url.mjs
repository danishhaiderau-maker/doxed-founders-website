/**
 * Ensure btc-conservative-agent has a Railway public URL and wire TRADING_AGENT_BOT_URL on API.
 * Reads RAILWAY_TOKEN from ../doxedcryptofounder-secrets/vault/.env.x.secrets
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const GQL = 'https://backboard.railway.com/graphql/v2';
const BOT_SERVICE = 'btc-conservative-agent';
const API_SERVICE = 'doxed-founders-website';
const REPO = 'danishhaiderau-maker/doxed-founders-website';
const ROOT_DIR = 'services/btc-conservative-agent';
/** Bybit blocks some Railway US regions — Singapore works for public market data. */
const BOT_REGION = 'asia-southeast1-eqsg3a';

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
  if (json.errors?.length) {
    throw new Error(json.errors.map((e) => e.message).join('; '));
  }
  return json.data;
}

const xSecrets = readDotEnv(join(vault, '.env.x.secrets'));
const token =
  xSecrets.RAILWAY_TOKEN?.trim() ||
  xSecrets.RAILWAY_API_TOKEN?.trim() ||
  process.env.RAILWAY_TOKEN?.trim();

if (!token) {
  console.error('Missing RAILWAY_TOKEN in vault/.env.x.secrets');
  process.exit(1);
}

const data = await gql(
  token,
  `query {
    projects { edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name } } }
    } } }
  }`,
);

let project = null;
let botService = null;
let apiService = null;
let env = null;

for (const p of data.projects?.edges?.map((e) => e.node) ?? []) {
  const hasApi = p.services?.edges?.some((e) => e.node.name === API_SERVICE);
  if (!hasApi) continue;
  project = p;
  env =
    p.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    p.environments?.edges?.[0]?.node;
  botService = p.services?.edges?.find((e) => e.node.name === BOT_SERVICE)?.node ?? null;
  apiService = p.services?.edges?.find((e) => e.node.name === API_SERVICE)?.node ?? null;
  break;
}

if (!project || !env || !apiService) {
  console.error(`Project with ${API_SERVICE} not found`);
  process.exit(1);
}

console.log(`Project: ${project.name}`);

if (!botService) {
  console.log(`Creating service ${BOT_SERVICE} from ${REPO}…`);
  const created = await gql(
    token,
    `mutation($input: ServiceCreateInput!) {
      serviceCreate(input: $input) { id name }
    }`,
    {
      input: {
        projectId: project.id,
        environmentId: env.id,
        name: BOT_SERVICE,
        branch: 'master',
        source: { repo: REPO },
      },
    },
  );
  botService = created.serviceCreate;
  console.log(`  Created ${botService.name} (${botService.id})`);

  console.log(`Configuring root directory ${ROOT_DIR}…`);
  await gql(
    token,
    `mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
      serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input)
    }`,
    {
      serviceId: botService.id,
      environmentId: env.id,
      input: {
        rootDirectory: ROOT_DIR,
        startCommand: 'python bot.py',
        healthcheckPath: '/health',
        healthcheckTimeout: 120,
        builder: 'NIXPACKS',
        multiRegionConfig: { [BOT_REGION]: { numReplicas: 1 } },
      },
    },
  );

  console.log('Setting bot service variables…');
  await gql(
    token,
    `mutation($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }`,
    {
      input: {
        projectId: project.id,
        environmentId: env.id,
        serviceId: botService.id,
        variables: { PORT: '5000' },
        replace: false,
      },
    },
  );

  console.log('Triggering initial bot deploy…');
  await gql(
    token,
    `mutation($serviceId: String!, $environmentId: String!) {
      serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
    }`,
    { serviceId: botService.id, environmentId: env.id },
  );
}

// Ensure region stays on Singapore (Bybit geo-blocks default US Railway regions).
await gql(
  token,
  `mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
    serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input)
  }`,
  {
    serviceId: botService.id,
    environmentId: env.id,
    input: { multiRegionConfig: { [BOT_REGION]: { numReplicas: 1 } } },
  },
);

async function fetchBotDomain() {
  const domainData = await gql(
    token,
    `query($projectId: String!, $environmentId: String!, $serviceId: String!) {
      domains(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) {
        serviceDomains { domain }
        customDomains { domain }
      }
    }`,
    { projectId: project.id, environmentId: env.id, serviceId: botService.id },
  );
  const block = domainData.domains;
  return block?.serviceDomains?.[0]?.domain ?? block?.customDomains?.[0]?.domain ?? null;
}

let domain = await fetchBotDomain();

if (!domain) {
  console.log('Generating Railway public domain…');
  await gql(
    token,
    `mutation($input: ServiceDomainCreateInput!) {
      serviceDomainCreate(input: $input) { id domain }
    }`,
    {
      input: {
        serviceId: botService.id,
        environmentId: env.id,
      },
    },
  );
  domain = await fetchBotDomain();
}

if (!domain) {
  console.error('Could not resolve public domain — deploy the bot service first, then retry.');
  process.exit(1);
}

const botUrl = `https://${domain.replace(/^https?:\/\//, '').replace(/\/$/, '')}`;
console.log(`Bot public URL: ${botUrl}`);

console.log(`Setting TRADING_AGENT_BOT_URL on ${API_SERVICE}…`);
await gql(
  token,
  `mutation($input: VariableCollectionUpsertInput!) {
    variableCollectionUpsert(input: $input)
  }`,
  {
    input: {
      projectId: project.id,
      environmentId: env.id,
      serviceId: apiService.id,
      variables: {
        TRADING_AGENT_BOT_URL: botUrl,
        CONSERVATIVE_BTC_BOT_URL: botUrl,
      },
      replace: false,
    },
  },
);

await gql(
  token,
  `mutation($serviceId: String!, $environmentId: String!) {
    serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
  }`,
  { serviceId: apiService.id, environmentId: env.id },
);

console.log('Redeploying bot service…');
await gql(
  token,
  `mutation($serviceId: String!, $environmentId: String!) {
    serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
  }`,
  { serviceId: botService.id, environmentId: env.id },
);

console.log('API + bot redeploy triggered.');
console.log(`\nPaste in Admin → Bot public URL:\n${botUrl}`);
