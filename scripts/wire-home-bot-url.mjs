#!/usr/bin/env node
/**
 * Point production API + Neon at home bot URL; optionally pause Railway showcase bot.
 *
 * Usage:
 *   npm run wire:home-bot
 *   npm run wire:home-bot -- https://bot.doxxedcrypto.digital
 *   npm run wire:home-bot -- --skip-health-check
 *   npm run wire:home-bot -- --pause-railway-bot
 */
import { PrismaClient } from '@prisma/client';
import { loadVaultEnv } from './load-vault-env.mjs';
import { fileURLToPath } from 'url';
import path from 'path';
import {
  readDotEnv,
  resolveHomeBotPublicUrl,
  RAILWAY_API_SERVICE,
  RAILWAY_BOT_SERVICE,
} from './home-bot-config.mjs';
import { getVaultDir } from './secrets-vault-path.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
loadVaultEnv(root);

const GQL = 'https://backboard.railway.com/graphql/v2';
const args = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const flags = new Set(process.argv.slice(2).filter((a) => a.startsWith('--')));
const botUrl = resolveHomeBotPublicUrl(args[0]);
const skipHealth = flags.has('--skip-health-check');
const pauseBot = flags.has('--pause-railway-bot') || !flags.has('--keep-railway-bot');

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

async function checkHealth(url) {
  for (const path of ['/health', '/api/state']) {
    try {
      const res = await fetch(`${url}${path}`, {
        signal: AbortSignal.timeout(12000),
        headers: { Accept: 'application/json' },
      });
      if (res.ok) {
        console.log(`  ✓ ${url}${path} → HTTP ${res.status}`);
        return true;
      }
      console.log(`  ✗ ${url}${path} → HTTP ${res.status}`);
    } catch (err) {
      console.log(`  ✗ ${url}${path} → ${err instanceof Error ? err.message : err}`);
    }
  }
  return false;
}

console.log('\n=== Wire home bot URL ===\n');
console.log(`Target: ${botUrl}\n`);

if (!skipHealth) {
  console.log('Health check (start tunnel + bot on home first, or pass --skip-health-check):');
  const ok = await checkHealth(botUrl);
  if (!ok) {
    console.error(`
Home bot not reachable at ${botUrl}

1. On home PC: npm run setup:home-bot-tunnel  (or scripts/setup-home-bot-tunnel.ps1)
2. Start bot:   npm run print:home-bot-env then start-home-bot.ps1
3. Re-run:      npm run wire:home-bot -- ${botUrl}

Or wire now and fix tunnel later:
  npm run wire:home-bot -- ${botUrl} --skip-health-check
`);
    process.exit(1);
  }
}

if (!process.env.DATABASE_URL) {
  console.error('Missing DATABASE_URL — set vault/.env.neon');
  process.exit(1);
}

const prisma = new PrismaClient();
await prisma.platformSettings.upsert({
  where: { id: 'default' },
  create: { id: 'default', showcaseBotPublicUrl: botUrl },
  update: { showcaseBotPublicUrl: botUrl },
});
await prisma.$disconnect();
console.log(`✓ Neon platformSettings.showcaseBotPublicUrl = ${botUrl}`);

const xSecrets = readDotEnv(path.join(getVaultDir(), '.env.x.secrets'));
const vercel = readDotEnv(path.join(getVaultDir(), '.env.vercel.check'));
const token = xSecrets.RAILWAY_TOKEN?.trim() || process.env.RAILWAY_TOKEN?.trim();

if (!token) {
  console.warn('No RAILWAY_TOKEN — set API vars manually in Railway dashboard:');
  console.warn(`  TRADING_AGENT_BOT_URL=${botUrl}`);
  console.warn(`  CONSERVATIVE_BTC_BOT_URL=${botUrl}`);
} else {
  const data = await gql(
    token,
    `query {
      projects { edges { node {
        id name
        environments { edges { node { id name } } }
        services { edges { node { id name serviceInstances { edges { node { id } } } } } }
      } } }
    }`,
  );

  let project = null;
  let env = null;
  let apiService = null;
  let botService = null;

  for (const p of data.projects?.edges?.map((e) => e.node) ?? []) {
    apiService = p.services?.edges?.find((e) => e.node.name === RAILWAY_API_SERVICE)?.node;
    botService = p.services?.edges?.find((e) => e.node.name === RAILWAY_BOT_SERVICE)?.node;
    if (!apiService) continue;
    project = p;
    env =
      p.environments?.edges?.find((e) => e.node.name === 'production')?.node ??
      p.environments?.edges?.[0]?.node;
    break;
  }

  if (project && env && apiService) {
    const apiVars = {
      TRADING_AGENT_BOT_URL: botUrl,
      CONSERVATIVE_BTC_BOT_URL: botUrl,
    };
    if (vercel.BOT_CONTROL_SECRET?.trim()) {
      apiVars.BOT_CONTROL_SECRET = vercel.BOT_CONTROL_SECRET.trim();
    }
    await gql(token, `mutation($input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $input) }`, {
      input: {
        projectId: project.id,
        environmentId: env.id,
        serviceId: apiService.id,
        variables: apiVars,
        replace: false,
      },
    });
    await gql(token, `mutation($s: String!, $e: String!) { serviceInstanceRedeploy(serviceId: $s, environmentId: $e) }`, {
      s: apiService.id,
      e: env.id,
    });
    console.log(`✓ Railway ${RAILWAY_API_SERVICE} → bot URL updated + redeployed`);
  }

  if (pauseBot && botService && env) {
    try {
      await gql(
        token,
        `mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
          serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input)
        }`,
        {
          serviceId: botService.id,
          environmentId: env.id,
          input: { sleepApplication: true },
        },
      );
      console.log(`✓ Railway ${RAILWAY_BOT_SERVICE} → sleep enabled (stops billing)`);
    } catch (err) {
      console.warn(`Could not sleep ${RAILWAY_BOT_SERVICE}: ${err.message}`);
      console.warn(`  Stop billing now: Railway → ${RAILWAY_BOT_SERVICE} → Settings → Delete Service`);
    }
  }
}

console.log(`
Done. Agent Hub / copy relay will use: ${botUrl}

Verify:
  curl ${botUrl}/health
  curl https://doxed-founders-website-production.up.railway.app/api/health
  npm run prepare:bitfinex-relay-test
`);
