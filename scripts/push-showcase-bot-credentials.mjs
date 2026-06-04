/**
 * Push admin showcase credentials from Neon to btc-conservative-agent Railway service.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createDecipheriv, scryptSync } from 'crypto';
import { PrismaClient } from '@prisma/client';
import { exchangeCredentialsToEnvVars } from '@dcf/utils';
import { loadVaultEnv } from './load-vault-env.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const GQL = 'https://backboard.railway.com/graphql/v2';
const BOT_SERVICE = 'btc-conservative-agent';

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

function decryptSecret(payload, jwtSecret) {
  const key = scryptSync(jwtSecret, 'dcf-security-v1', 32);
  const buf = Buffer.from(payload, 'base64');
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const encrypted = buf.subarray(28);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
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
const vercel = readDotEnv(join(vault, '.env.vercel.check'));
const token = xSecrets.RAILWAY_TOKEN?.trim();
const jwtSecret = vercel.JWT_SECRET;
loadVaultEnv(root);

if (!token || !process.env.DATABASE_URL || !jwtSecret) {
  console.error('Missing RAILWAY_TOKEN, DATABASE_URL, or JWT_SECRET in vault');
  process.exit(1);
}

const prisma = new PrismaClient();
const row = await prisma.platformSettings.findUnique({ where: { id: 'default' } });
await prisma.$disconnect();
if (!row?.showcaseExchangeCredentialEnc) {
  console.error('No showcase exchange credentials saved in admin — save keys first.');
  process.exit(1);
}

const vars = { PORT: '5000' };
if (vercel.BOT_CONTROL_SECRET?.trim()) {
  vars.BOT_CONTROL_SECRET = vercel.BOT_CONTROL_SECRET.trim();
}
const ex = JSON.parse(decryptSecret(row.showcaseExchangeCredentialEnc, jwtSecret));
const provider = row.showcaseExchangeProvider ?? 'bybit';
Object.assign(vars, exchangeCredentialsToEnvVars(provider, {
  apiKey: ex.apiKey,
  apiSecret: ex.apiSecret,
  passphrase: ex.passphrase,
  testnet: ex.testnet,
}));

const aiKey = row.showcaseAiCredentialEnc
  ? decryptSecret(row.showcaseAiCredentialEnc, jwtSecret)
  : null;
if (aiKey) {
  vars.DEEPSEEK_API_KEY = aiKey;
}

console.log(`Pushing ${Object.keys(vars).join(', ')} to ${BOT_SERVICE}…`);

const data = await gql(
  token,
  `query {
    projects { edges { node {
      id
      environments { edges { node { id name } } }
      services { edges { node { id name } } }
    } } }
  }`,
);

let project = null;
let env = null;
let botService = null;
for (const p of data.projects?.edges?.map((e) => e.node) ?? []) {
  botService = p.services?.edges?.find((e) => e.node.name === BOT_SERVICE)?.node;
  if (!botService) continue;
  project = p;
  env =
    p.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
    p.environments?.edges?.[0]?.node;
  break;
}

if (!project || !env || !botService) {
  console.error(`${BOT_SERVICE} not found`);
  process.exit(1);
}

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
      variables: vars,
      replace: false,
    },
  },
);

await gql(
  token,
  `mutation($serviceId: String!, $environmentId: String!) {
    serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
  }`,
  { serviceId: botService.id, environmentId: env.id },
);

console.log('Showcase credentials pushed and bot redeploy triggered.');
