#!/usr/bin/env node
/**
 * Read-only flat-boundary check for the showcase-to-Bitfinex relay.
 * Uses the executor's fresh raw Bitfinex reconciliation and does not print credentials.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import prismaPackage from '../node_modules/.prisma/client/default.js';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

for (const envFile of [
  path.join(getVaultDir(), '.env.neon'),
  path.join(getVaultDir(), 'home-bot.env'),
]) {
  if (!fs.existsSync(envFile)) continue;
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

const { PrismaClient } = prismaPackage;
const prisma = new PrismaClient();
const adminToken =
  process.env.BOT_ADMIN_TOKEN?.trim()
  || process.env.BOT_CONTROL_SECRET?.trim()
  || '';
const botUrls = [
  process.env.SHOWCASE_OWNER_URL?.trim(),
  'http://10.0.0.102:7002',
  process.env.TRADING_AGENT_BOT_URL?.trim(),
  resolveHomeBotPublicUrl(),
].filter(Boolean);

export function hasFullOwnerOrderState(bot) {
  return (
    bot != null
    && typeof bot === 'object'
    && (
      Array.isArray(bot.orders)
      || Array.isArray(bot.pending_orders)
    )
  );
}

async function fetchOwnerState() {
  if (process.env.REQUIRE_BOT_ADMIN_TOKEN === 'YES' && !adminToken) {
    throw new Error('BOT_ADMIN_TOKEN is required for an authenticated owner-state flat proof');
  }
  let lastError = null;
  for (const baseUrl of [...new Set(botUrls)]) {
    try {
      const bot = await fetch(`${baseUrl}/api/state`, {
        headers: adminToken
          ? { 'X-Bot-Admin-Token': adminToken }
          : undefined,
        signal: AbortSignal.timeout(10_000),
      }).then((response) => {
        if (!response.ok) throw new Error(`showcase HTTP ${response.status}`);
        return response.json();
      });
      if (bot?.dashboard_owner === true) {
        if (
          process.env.REQUIRE_BOT_ADMIN_TOKEN === 'YES'
          && !hasFullOwnerOrderState(bot)
        ) {
          throw new Error(
            `${baseUrl} did not return the authenticated owner order state`,
          );
        }
        return { bot, baseUrl };
      }
      lastError = new Error(`${baseUrl} is not the dashboard owner`);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError ?? new Error('showcase owner unavailable');
}

export function isStrictRawFlatReconcileSnapshot(rec, nowMs = Date.now()) {
  if (rec == null || typeof rec !== 'object') return false;
  for (const key of [
    'rawExchangePositionQty',
    'dustPositionQty',
    'signedExchangePositionQty',
    'ledgerOpenQty',
    'signedLedgerOpenQty',
    'deltaBtc',
    'openLots',
    'pendingLots',
  ]) {
    if (!Object.prototype.hasOwnProperty.call(rec, key)) return false;
    if (typeof rec[key] !== 'number' || !Number.isFinite(rec[key])) return false;
  }

  const reconcileAgeMs = nowMs - Date.parse(String(rec.updatedAt ?? ''));
  return (
    rec.rawExchangePositionQty === 0
    && rec.dustPositionQty === 0
    && rec.signedExchangePositionQty === 0
    && rec.ledgerOpenQty === 0
    && rec.signedLedgerOpenQty === 0
    && rec.deltaBtc === 0
    && rec.openLots === 0
    && rec.pendingLots === 0
    && Number.isFinite(reconcileAgeMs)
    && reconcileAgeMs >= 0
    && reconcileAgeMs <= 60_000
  );
}

export function isStrictExchangeOrderAuditFlat(audit, nowMs = Date.now()) {
  if (audit == null || typeof audit !== 'object') return false;
  const checkedAgeMs = nowMs - Date.parse(String(audit.checkedAt ?? ''));
  return (
    audit.known === true
    && audit.activeOrderCount === 0
    && audit.managedActiveOrderCount === 0
    && audit.foreignActiveOrderCount === 0
    && Number.isFinite(checkedAgeMs)
    && checkedAgeMs >= 0
    && checkedAgeMs <= 60_000
  );
}

async function main() {
  const { bot, baseUrl: botUrl } = await fetchOwnerState();
  const pendingOrders = (bot.orders ?? bot.pending_orders ?? []).filter(
    (order) =>
      order
      && !['FILLED', 'CANCELLED', 'CANCELED', 'EXPIRED', 'REJECTED'].includes(
        String(order.status ?? '').toUpperCase(),
      ),
  );

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
      instanceId: instance.id,
      user:
        instance.user.platformHandle
        || instance.user.name
        || instance.userId,
      status: instance.status,
      lastError: instance.lastError,
      activeParticipants,
      reconcile,
      relayExecutionMode: dashboard.relayExecutionMode ?? null,
      relayArmedAt: dashboard.relayArmedAt ?? null,
      realTradingConfirmedAt: dashboard.realTradingConfirmedAt ?? null,
      exchangeOrderAudit: dashboard.exchangeOrderAudit ?? null,
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
      pendingOrders: pendingOrders.length,
    },
    instances: rows,
  };
  console.log(JSON.stringify(output, null, 2));

  const showcaseFlat =
    output.showcase.positions === 0
    && output.showcase.pendingOrders === 0;
  const trackedFlat = rows.every((row) => row.activeParticipants === 0);
  const cheetahRows = rows
    .filter((row) => String(row.user).toLowerCase().includes('cheetah'));
  const relayPausedAndDisarmed = cheetahRows.length > 0
    && cheetahRows.every(
      (row) =>
        row.status === 'PAUSED'
        && row.relayExecutionMode === 'PAUSED'
        && row.relayArmedAt == null
        && row.realTradingConfirmedAt == null,
    );
  const reconciledFlat = cheetahRows.length > 0
    && cheetahRows.every((row) => {
      return (
        isStrictRawFlatReconcileSnapshot(row.reconcile)
        && isStrictExchangeOrderAuditFlat(row.exchangeOrderAudit)
        && row.orphanOrderIds.length === 0
        && row.orphanPositionIds.length === 0
      );
    });
  process.exitCode =
    showcaseFlat && trackedFlat && relayPausedAndDisarmed && reconciledFlat ? 0 : 2;
}

const isDirectRun =
  process.argv[1] != null
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectRun) {
  main()
    .catch((error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    })
    .finally(async () => {
      await prisma.$disconnect();
    });
}
