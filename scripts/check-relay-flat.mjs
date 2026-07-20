#!/usr/bin/env node
/**
 * Read-only flat-boundary check for the showcase-to-Bitfinex relay.
 * Does not call Bitfinex directly and does not print credentials.
 */
import fs from 'node:fs';
import path from 'node:path';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

const envFile = path.join(getVaultDir(), '.env.neon');
if (fs.existsSync(envFile)) {
  for (const raw of fs.readFileSync(envFile, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const splitAt = line.indexOf('=');
    if (splitAt < 1) continue;
    const key = line.slice(0, splitAt).trim();
    let value = line.slice(splitAt + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

const prisma = new PrismaClient();
const botUrls = [
  process.env.SHOWCASE_OWNER_URL?.trim(),
  'http://10.0.0.102:7002',
  process.env.TRADING_AGENT_BOT_URL?.trim(),
  resolveHomeBotPublicUrl(),
].filter(Boolean);

async function fetchOwnerState() {
  let lastError = null;
  for (const baseUrl of [...new Set(botUrls)]) {
    try {
      const bot = await fetch(`${baseUrl}/api/state`, {
        signal: AbortSignal.timeout(10_000),
      }).then((response) => {
        if (!response.ok) throw new Error(`showcase HTTP ${response.status}`);
        return response.json();
      });
      if (bot?.dashboard_owner === true) {
        return { bot, baseUrl };
      }
      lastError = new Error(`${baseUrl} is not the dashboard owner`);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError ?? new Error('showcase owner unavailable');
}

async function main() {
  const { bot, baseUrl: botUrl } = await fetchOwnerState();

  const agent = await prisma.tradingAgent.findUnique({
    where: { slug: 'conservative-btc' },
    select: { id: true },
  });
  if (!agent) throw new Error('conservative-btc agent missing');

  const instances = await prisma.tradingAgentInstance.findMany({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    include: {
      user: { select: { platformHandle: true, name: true } },
    },
  });

  const rows = [];
  for (const instance of instances) {
    const dashboard = instance.dashboardState ?? {};
    const activeParticipants = await prisma.signalCycleParticipant.count({
      where: {
        userId: instance.userId,
        cycle: { agentId: agent.id },
        status: { in: ['PENDING_ENTRY', 'OPEN'] },
      },
    });
    const reconcile =
      dashboard.copyRelayReconcile
      ?? dashboard.copyRelaySim?.reconcile
      ?? null;
    rows.push({
      user:
        instance.user.platformHandle
        || instance.user.name
        || instance.userId,
      status: instance.status,
      lastError: instance.lastError,
      activeParticipants,
      reconcile,
      orphanOrderIds: dashboard.orphanOrderIds ?? [],
      orphanPositionIds: dashboard.orphanPositionIds ?? [],
    });
  }

  const output = {
    at: new Date().toISOString(),
    showcase: {
      botVersion: bot.bot_version ?? null,
      botInstanceId: bot.bot_instance_id ?? null,
      url: botUrl,
      dashboardOwner: bot.dashboard_owner === true,
      positions: Array.isArray(bot.positions) ? bot.positions.length : null,
      pendingOrders: Array.isArray(bot.orders) ? bot.orders.length : null,
    },
    instances: rows,
  };
  console.log(JSON.stringify(output, null, 2));

  const showcaseFlat =
    output.showcase.positions === 0
    && output.showcase.pendingOrders === 0;
  const trackedFlat = rows.every((row) => row.activeParticipants === 0);
  const reconciledFlat = rows
    .filter((row) => String(row.user).toLowerCase().includes('cheetah'))
    .every((row) => {
      const rec = row.reconcile;
      return (
        Number(rec?.exchangePositionQty ?? 0) === 0
        && Number(rec?.ledgerOpenQty ?? 0) === 0
        && Number(rec?.deltaBtc ?? 0) === 0
        && Number(rec?.openLots ?? 0) === 0
        && Number(rec?.pendingLots ?? 0) === 0
        && row.orphanOrderIds.length === 0
        && row.orphanPositionIds.length === 0
      );
    });
  process.exitCode = showcaseFlat && trackedFlat && reconciledFlat ? 0 : 2;
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
