#!/usr/bin/env node
/**
 * Keep the HMAC used by the home :7002 showcase webhook identical to Railway.
 * The secret is never printed and is stored only in the external secrets vault.
 */
import {
  existsSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import { randomBytes } from 'node:crypto';
import { join } from 'node:path';
import { getVaultDir } from './secrets-vault-path.mjs';

const GQL = 'https://backboard.railway.com/graphql/v2';
const API_SERVICE = 'doxed-founders-website';
const KEY = 'SHOWCASE_WEBHOOK_SECRET';
const vault = getVaultDir();

function readDotEnv(file) {
  const values = {};
  if (!existsSync(file)) return values;
  for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx < 1) continue;
    let value = line.slice(idx + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[line.slice(0, idx).trim()] = value;
  }
  return values;
}

function upsertDotEnv(file, key, value) {
  const existing = existsSync(file) ? readFileSync(file, 'utf8') : '';
  const lines = existing.split(/\r?\n/).filter((line, index, all) =>
    !(index === all.length - 1 && line === ''),
  );
  const prefix = `${key}=`;
  const index = lines.findIndex((line) => line.trimStart().startsWith(prefix));
  if (index >= 0) lines[index] = `${key}=${value}`;
  else lines.push(`${key}=${value}`);
  const temp = `${file}.tmp`;
  writeFileSync(temp, `${lines.join('\n')}\n`, 'utf8');
  renameSync(temp, file);
}

async function gql(token, query, variables = {}) {
  const response = await fetch(GQL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ query, variables }),
  });
  const json = await response.json();
  if (!response.ok || json.errors?.length) {
    throw new Error(
      json.errors?.map((error) => error.message).join('; ') ||
        `Railway GraphQL HTTP ${response.status}`,
    );
  }
  return json.data;
}

const xSecrets = readDotEnv(join(vault, '.env.x.secrets'));
const token =
  xSecrets.RAILWAY_TOKEN?.trim() ||
  xSecrets.RAILWAY_API_TOKEN?.trim() ||
  process.env.RAILWAY_TOKEN?.trim();
if (!token) throw new Error('Railway token missing from the external vault');

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
let environment = null;
let service = null;
for (const candidate of data.projects?.edges?.map((edge) => edge.node) ?? []) {
  const match = candidate.services?.edges?.find(
    (edge) => edge.node.name === API_SERVICE,
  )?.node;
  if (!match) continue;
  project = candidate;
  service = match;
  environment =
    candidate.environments?.edges?.find(
      (edge) => edge.node.name === 'production',
    )?.node ?? candidate.environments?.edges?.[0]?.node;
  break;
}
if (!project || !environment || !service) {
  throw new Error(`Railway service ${API_SERVICE} not found`);
}

const variables = await gql(
  token,
  `query($projectId: String!, $environmentId: String!, $serviceId: String!) {
    variables(
      projectId: $projectId,
      environmentId: $environmentId,
      serviceId: $serviceId
    )
  }`,
  {
    projectId: project.id,
    environmentId: environment.id,
    serviceId: service.id,
  },
);

let secret = variables.variables?.[KEY]?.trim();
let created = false;
if (!secret) {
  secret = randomBytes(32).toString('base64url');
  created = true;
  await gql(
    token,
    `mutation($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }`,
    {
      input: {
        projectId: project.id,
        environmentId: environment.id,
        serviceId: service.id,
        variables: { [KEY]: secret },
        replace: false,
      },
    },
  );
  await gql(
    token,
    `mutation($serviceId: String!, $environmentId: String!) {
      serviceInstanceRedeploy(
        serviceId: $serviceId,
        environmentId: $environmentId
      )
    }`,
    { serviceId: service.id, environmentId: environment.id },
  );
}

upsertDotEnv(join(vault, '.env.vercel.check'), KEY, secret);
upsertDotEnv(join(vault, 'home-bot.env'), KEY, secret);

console.log(
  `${KEY} synchronized to the external vault and home-bot.env ` +
    `(${created ? 'created on Railway; redeploy triggered' : 'matched existing Railway value'}).`,
);
