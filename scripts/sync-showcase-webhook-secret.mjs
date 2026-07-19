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
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { getVaultDir } from './secrets-vault-path.mjs';

const GQL = 'https://backboard.railway.com/graphql/v2';
const API_SERVICE = 'doxed-founders-website';
const CONTROL_KEY = 'BOT_CONTROL_SECRET';
const WEBHOOK_KEY = 'SHOWCASE_WEBHOOK_SECRET';
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

function railwayCliEnv() {
  const env = { ...process.env };
  // A stale environment token masks an otherwise valid interactive Railway
  // CLI session. The CLI credential store remains available without it.
  delete env.RAILWAY_TOKEN;
  delete env.RAILWAY_API_TOKEN;
  return env;
}

function railwayCommand(args) {
  if (process.platform === 'win32') {
    return {
      file: process.env.ComSpec || 'cmd.exe',
      args: ['/d', '/s', '/c', 'railway.cmd', ...args],
    };
  }
  return { file: 'railway', args };
}

function railwayCliVariables() {
  const command = railwayCommand(['variables', '--json']);
  const raw = execFileSync(command.file, command.args, {
    cwd: process.cwd(),
    env: railwayCliEnv(),
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  return JSON.parse(raw);
}

function railwayCliSet(key, value) {
  const command = railwayCommand(['variables', '--set', `${key}=${value}`]);
  execFileSync(command.file, command.args, {
    cwd: process.cwd(),
    env: railwayCliEnv(),
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

const xSecrets = readDotEnv(join(vault, '.env.x.secrets'));
const token =
  xSecrets.RAILWAY_TOKEN?.trim() ||
  xSecrets.RAILWAY_API_TOKEN?.trim() ||
  process.env.RAILWAY_TOKEN?.trim();

let railwayVariables = null;
let graphContext = null;

if (token) {
  try {
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

    for (const candidate of data.projects?.edges?.map((edge) => edge.node) ?? []) {
      const service = candidate.services?.edges?.find(
        (edge) => edge.node.name === API_SERVICE,
      )?.node;
      if (!service) continue;
      const environment =
        candidate.environments?.edges?.find(
          (edge) => edge.node.name === 'production',
        )?.node ?? candidate.environments?.edges?.[0]?.node;
      if (!environment) continue;
      graphContext = { project: candidate, environment, service };
      break;
    }

    if (!graphContext) {
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
        projectId: graphContext.project.id,
        environmentId: graphContext.environment.id,
        serviceId: graphContext.service.id,
      },
    );
    railwayVariables = variables.variables;
  } catch {
    console.warn('Stored Railway API token unavailable; using authenticated Railway CLI.');
  }
}

if (!railwayVariables) {
  railwayVariables = railwayCliVariables();
}

const controlSecret = railwayVariables?.[CONTROL_KEY]?.trim();
if (!controlSecret) {
  throw new Error(`${CONTROL_KEY} is missing from the linked Railway service`);
}

let webhookSecret = railwayVariables?.[WEBHOOK_KEY]?.trim();
let created = false;
if (!webhookSecret) {
  webhookSecret = randomBytes(32).toString('base64url');
  created = true;
  if (graphContext && token) {
    await gql(
      token,
      `mutation($input: VariableCollectionUpsertInput!) {
        variableCollectionUpsert(input: $input)
      }`,
      {
        input: {
          projectId: graphContext.project.id,
          environmentId: graphContext.environment.id,
          serviceId: graphContext.service.id,
          variables: { [WEBHOOK_KEY]: webhookSecret },
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
      {
        serviceId: graphContext.service.id,
        environmentId: graphContext.environment.id,
      },
    );
  } else {
    railwayCliSet(WEBHOOK_KEY, webhookSecret);
  }
}

for (const file of [
  join(vault, '.env.vercel.check'),
  join(vault, 'home-bot.env'),
]) {
  upsertDotEnv(file, CONTROL_KEY, controlSecret);
  upsertDotEnv(file, WEBHOOK_KEY, webhookSecret);
}

console.log(
  `Relay control and webhook secrets synchronized without printing values ` +
    `(${created ? 'webhook secret created on Railway' : 'matched existing Railway values'}).`,
);
