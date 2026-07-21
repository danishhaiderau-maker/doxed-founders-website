/**
 * Deep snapshot: participant events, FILLED vs status, duplicate order IDs.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

const neonPath = path.join(getVaultDir(), '.env.neon');
if (fs.existsSync(neonPath)) {
  for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    process.env[t.slice(0, i).trim()] = v;
  }
}

const prisma = new PrismaClient();

function pickPayload(p) {
  if (!p || typeof p !== 'object') return {};
  return p;
}

async function main() {
  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  const requestedInstanceId = process.argv[2] || process.env.LIVE_COPY_INSTANCE_ID || null;
  const instanceWhere = requestedInstanceId
    ? { id: requestedInstanceId }
    : {
        agentId: agent.id,
        exchangeProvider: 'bitfinex',
        status: { in: ['ACTIVE', 'PAUSED'] },
        OR: [
          { expiresAt: null },
          { expiresAt: { gt: new Date() } },
        ],
      };
  const cheetah = await prisma.tradingAgentInstance.findFirst({
    where: instanceWhere,
    include: { user: { select: { id: true, platformHandle: true, name: true } } },
    orderBy: { updatedAt: 'desc' },
  });
  if (!cheetah) {
    console.log('No Bitfinex hire');
    return;
  }

  const userId = cheetah.userId;
  const who = cheetah.user?.platformHandle ?? cheetah.user?.name ?? userId;
  console.log(`\n=== ${who} instance=${cheetah.id} status=${cheetah.status} @ ${new Date().toISOString()} ===`);
  console.log(`instance lastError: ${cheetah.lastError ?? '(none)'}`);
  const dashboard = cheetah.dashboardState && typeof cheetah.dashboardState === 'object'
    ? cheetah.dashboardState
    : {};
  const reconcile = dashboard.copyRelayReconcile ?? null;
  console.log(
    `reconcile: exchange=${Number(reconcile?.exchangePositionQty ?? 0).toFixed(5)} ` +
      `ledger=${Number(reconcile?.ledgerOpenQty ?? 0).toFixed(5)} ` +
      `delta=${Number(reconcile?.deltaBtc ?? 0).toFixed(5)} ` +
      `open=${reconcile?.openLots ?? 0} pending=${reconcile?.pendingLots ?? 0} ` +
      `alert=${Boolean(reconcile?.alert)}`,
  );
  console.log(
    `relay tick=${dashboard.lastTickAt ?? 'n/a'} orphanOrders=${
      Array.isArray(dashboard.orphanOrderIds) ? dashboard.orphanOrderIds.length : 0
    } mirrorDiff=${dashboard.mirrorDiff?.counts?.total ?? 0} ` +
      `dynamicStops=${Boolean(dashboard.exchangeDynamicStopsEnabled)}`,
  );

  const rows = await prisma.signalCycleParticipant.findMany({
    where: {
      userId,
      cycle: { agentId: agent.id },
      status: { in: ['PENDING_ENTRY', 'OPEN'] },
    },
    include: {
      cycle: { select: { tradeId: true, status: true, createdAt: true } },
      events: { orderBy: { createdAt: 'asc' } },
    },
    orderBy: { createdAt: 'asc' },
  });

  console.log(`\nManaged legs: ${rows.length} (OPEN=${rows.filter((r) => r.status === 'OPEN').length}, PENDING=${rows.filter((r) => r.status === 'PENDING_ENTRY').length})`);

  const orderMap = new Map();
  let ledgerQty = 0;
  let openQty = 0;

  for (const row of rows) {
    const filled = row.events.filter((e) => e.eventType === 'FILLED');
    const exits = row.events.filter((e) => e.eventType === 'EXIT');
    const stops = row.events.filter((e) => e.eventType === 'STOP_LOSS_ARMED');
    const placed = row.events.find((e) => e.eventType === 'ORDER_PLACED');
    const catchup = row.events.find((e) => e.eventType === 'MIRROR_CATCHUP_ENTRY');
    const mergedPayload = row.events.reduce(
      (acc, event) => Object.assign(acc, pickPayload(event.payload)),
      {},
    );
    const pp = {
      ...mergedPayload,
      ...pickPayload(placed?.payload),
      ...pickPayload(catchup?.payload),
    };

    const orderId = pp.bitfinexOrderId ?? pp.bitfinex_order_id;
    const qty = pp.qty ?? 0;
    const limit = pp.limit_price ?? pp.limitPrice;
    const dir = pp.direction;

    if (row.status === 'OPEN') {
      ledgerQty += Number(qty) || 0;
      openQty += 1;
    }

    if (orderId) {
      const key = String(orderId);
      if (!orderMap.has(key)) orderMap.set(key, []);
      orderMap.get(key).push(row.cycle.tradeId.slice(0, 10));
    }

    console.log(`\n--- ${row.status} | ${row.cycle.tradeId.slice(0, 12)}… | cycle=${row.cycle.status}`);
    console.log(`  fillPrice=${row.fillPrice} pnlUsd=${row.pnlUsd}`);
    console.log(`  orderId=${orderId ?? '?'} qty=${qty} limit=${limit} ${dir ?? ''}`);
    console.log(
      `  events: placed=${placed ? 'Y' : 'N'} catchup=${catchup ? 'Y' : 'N'} ` +
        `filled=${filled.length} stops=${stops.length} exits=${exits.length}`,
    );
    if (filled.length) {
      const fp = pickPayload(filled[filled.length - 1].payload);
      console.log(`  last FILLED: fill=${fp.fill_price} stop=${fp.stopOrderId} @ ${filled[filled.length - 1].createdAt.toISOString()}`);
    }
    if (exits.length) {
      const ep = pickPayload(exits[exits.length - 1].payload);
      console.log(`  last EXIT: ${ep.exit_reason} pnl=${ep.pnl_usd}`);
    }
    const last3 = row.events.slice(-3);
    for (const e of last3) {
      const pl = pickPayload(e.payload);
      console.log(`  ${e.createdAt.toISOString()} ${e.eventType} ${pl.event ?? pl.exit_reason ?? ''} ${pl.new_limit ?? pl.limit_price ?? ''}`);
    }
  }

  console.log('\n=== Duplicate Bitfinex order IDs ===');
  for (const [oid, trades] of orderMap) {
    if (trades.length > 1) console.log(`  order ${oid} used by ${trades.length} cycles: ${trades.join(', ')}`);
  }

  const recentExitEvents = await prisma.signalCycleEvent.findMany({
    where: {
      participant: { userId },
      eventType: { in: ['EXIT', 'UPDATE_STOPS', 'FILLED'] },
      createdAt: { gt: new Date(Date.now() - 30 * 60_000) },
    },
    orderBy: { createdAt: 'desc' },
    take: 15,
    include: { participant: { include: { cycle: { select: { tradeId: true } } } } },
  });

  console.log('\n=== Last 30m FILLED/EXIT/PEAK events ===');
  for (const e of recentExitEvents) {
    const pl = pickPayload(e.payload);
    console.log(
      `  ${e.createdAt.toISOString()} ${e.eventType} ${pl.event ?? pl.exit_reason ?? ''} peak=${pl.peak_margin_pct ?? '-'} unreal=${pl.unreal_margin_pct ?? '-'} trade=${e.participant.cycle.tradeId.slice(0, 10)}`,
    );
  }

  console.log(`\nLedger OPEN lots: ${openQty} | sum qty: ${ledgerQty.toFixed(5)} BTC`);

  const recentParticipants = await prisma.signalCycleParticipant.findMany({
    where: { userId, cycle: { agentId: agent.id } },
    include: {
      cycle: { select: { tradeId: true, status: true, createdAt: true } },
      events: { orderBy: { createdAt: 'desc' }, take: 5 },
    },
    orderBy: { updatedAt: 'desc' },
    take: 8,
  });
  console.log('\n=== Recent participant lifecycle ===');
  for (const row of recentParticipants) {
    console.log(`  ${row.updatedAt.toISOString()} ${row.status} ${row.cycle.tradeId}`);
    for (const event of [...row.events].reverse()) {
      const payload = pickPayload(event.payload);
      console.log(
        `    ${event.createdAt.toISOString()} ${event.eventType} ` +
          `${payload.reason ?? payload.event ?? payload.orderStatus ?? ''} ` +
          `order=${payload.bitfinexOrderId ?? payload.bitfinex_order_id ?? ''} ` +
          `cancelled=${payload.cancelledOrderId ?? payload.cancelled_order_id ?? ''} ` +
          `limit=${payload.limit_price ?? payload.limitPrice ?? ''} qty=${payload.qty ?? ''}`,
      );
    }
  }
  await prisma.$disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
