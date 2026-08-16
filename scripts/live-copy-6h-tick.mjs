/**
 * One JSON tick for 6h lifecycle watch — stdout only.
 * Read-only: :7002/api/state + Neon dashboardState (includes exchangeLiveBook).
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LOCAL_BOT = (
  process.env.SHOWCASE_OWNER_URL
  || process.env.SHOWCASE_BOT_LOCAL_URL
  || 'http://10.0.0.102:7002'
).replace(/\/$/, '');
const FALLBACK_BOT = resolveHomeBotPublicUrl(undefined, REPO);
const INSTANCE_ID = process.env.LIVE_COPY_INSTANCE_ID || 'cmq6cfwv4001jli0dqx5r31ve';
const OPEN_PART = new Set(['INTENT', 'PENDING_ENTRY', 'OPEN']);

function loadDbUrl() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const vault = resolve(getVaultDir(), '.env.neon');
  if (!existsSync(vault)) throw new Error('no DATABASE_URL');
  for (const raw of readFileSync(vault, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    if (k.trim() === 'DATABASE_URL') return rest.join('=').trim().replace(/^"|"$/g, '');
  }
  throw new Error('DATABASE_URL not found');
}

async function fetchBot() {
  for (const [base, source] of [
    [LOCAL_BOT, 'local:7002'],
    [FALLBACK_BOT, 'tunnel'],
  ]) {
    try {
      const r = await fetch(`${base}/api/state`, { signal: AbortSignal.timeout(12_000) });
      if (!r.ok) continue;
      const s = await r.json();
      const positions = (s.positions || [])
        .filter((p) => p && (p.status === 'OPEN' || !p.status))
        .map((p) => ({
          trade_id: p.trade_id,
          dir: p.dir || p.direction,
          entry: p.entry,
          qty: p.qty,
          margin_usdt: p.margin_usdt ?? p.margin_usd,
        }));
      return {
        ok: true,
        source,
        price: s.price ?? s.mark,
        leverage: s.leverage ?? s.state?.leverage,
        positions,
        pendingOrders: (s.orders || s.pending_orders || [])
          .filter((o) => o && !['FILLED', 'CANCELLED', 'EXPIRED'].includes(o.status))
          .map((o) => ({ trade_id: o.trade_id, limit: o.limit_price ?? o.limit })),
      };
    } catch {
      /* try next */
    }
  }
  return { ok: false, source: 'unreachable', positions: [], pendingOrders: [] };
}

function syncVerdict({
  bot,
  copyOpen,
  copyPending,
  copyAll,
  showcaseOpen,
  expectedMissedShowcase,
  suppressedPreArmShowcase,
  entryEnabled,
  instanceLastError,
  dash,
  alerts,
}) {
  if (!bot.ok) return { verdict: 'FAIL', reason: 'showcase_unreachable' };
  if (instanceLastError) alerts.push(`lastError:${instanceLastError}`);
  const md = dash?.mirrorDiff;
  const mdTypes = md?.counts?.byType || {};
  if (mdTypes.COPY_ORDER_NO_SHOWCASE > 0) {
    alerts.push(`COPY_ORDER_NO_SHOWCASE:${mdTypes.COPY_ORDER_NO_SHOWCASE}`);
  }
  if (mdTypes.COPY_POSITION_NO_SHOWCASE > 0) {
    alerts.push(`COPY_POSITION_NO_SHOWCASE:${mdTypes.COPY_POSITION_NO_SHOWCASE}`);
  }
  if (md?.counts?.total > 0) alerts.push(`MIRROR_DIFF:${md.counts.total}`);

  const actionableShowcaseOpen = new Set(
    entryEnabled
      ? [...showcaseOpen].filter(
          (t) => !expectedMissedShowcase.has(t) && !suppressedPreArmShowcase.has(t),
        )
      : [],
  );
  const missEntry = [...actionableShowcaseOpen].filter((t) => !copyAll.has(t));
  const orphan = [...copyOpen].filter((t) => !showcaseOpen.has(t));
  const pendingWorking = [...actionableShowcaseOpen].filter(
    (t) => !copyOpen.has(t) && copyAll.has(t),
  );

  if (missEntry.length) alerts.push(`missEntry:${missEntry.map((t) => t.slice(0, 10)).join(',')}`);
  if (orphan.length) alerts.push(`orphan:${orphan.map((t) => t.slice(0, 10)).join(',')}`);

  const book = dash?.exchangeLiveBook || dash?.liveBook;
  const reconcile = dash?.copyRelayReconcile;
  const reconcileDelta = Math.abs(Number(reconcile?.deltaBtc ?? 0));
  const exchangePosition = Math.abs(
    Number(reconcile?.signedExchangePositionQty ?? reconcile?.exchangePositionQty ?? 0),
  );
  if (reconcile?.alert === true || reconcileDelta > 0.00000001 || exchangePosition > 0.00000001) {
    alerts.push(
      `RECONCILE_MISMATCH:delta=${Number(reconcile?.deltaBtc ?? 0)} exchange=${Number(
        reconcile?.signedExchangePositionQty ?? reconcile?.exchangePositionQty ?? 0,
      )}`,
    );
  }
  if (!entryEnabled && copyPending.length > 0) {
    alerts.push(`UNRESOLVED_PENDING:${copyPending.length}`);
  }
  const derivFree = book?.derivativesUsd ?? book?.derivativesAvailableUsd ?? null;
  if (derivFree != null && derivFree < 1 && missEntry.length) {
    alerts.push(`margin_block:$${derivFree}`);
  }

  if (
    alerts.some((a) =>
      /missEntry|orphan|margin_block|COPY_ORDER|showcase_unreachable|RECONCILE_MISMATCH|UNRESOLVED_PENDING/i.test(
        a,
      ),
    )
  ) {
    return { verdict: 'FAIL', reason: alerts.join('; ') };
  }
  if (alerts.length || pendingWorking.length) {
    return { verdict: 'WARN', reason: alerts.join('; ') || 'pending_working' };
  }
  if (actionableShowcaseOpen.size === 0 && copyOpen.size === 0) {
    return { verdict: 'PASS', reason: 'both_flat' };
  }
  const matched = [...actionableShowcaseOpen].filter((t) => copyOpen.has(t)).length;
  if (matched === actionableShowcaseOpen.size && orphan.length === 0) {
    return { verdict: 'PASS', reason: 'action_match' };
  }
  return { verdict: 'WARN', reason: 'partial_match' };
}

let prisma;
try {
  prisma = new PrismaClient({ datasources: { db: { url: loadDbUrl() } } });
} catch (e) {
  console.log(JSON.stringify({ type: 'tick', at: new Date().toISOString(), syncVerdict: 'FAIL', syncReason: `db_init:${e.message}`, bot: await fetchBot() }));
  process.exit(0);
}

try {
  const bot = await fetchBot();
  let inst = null;
  let dash = {};
  let participants = [];
  let dbOk = true;
  let dbErr = null;

  try {
    inst = await prisma.tradingAgentInstance.findUnique({
      where: { id: INSTANCE_ID },
      include: { agent: { select: { id: true, slug: true } } },
    });
    dash = (inst?.dashboardState && typeof inst.dashboardState === 'object'
      ? inst.dashboardState
      : {}) || {};
    participants = inst
      ? await prisma.signalCycleParticipant.findMany({
          where: {
            userId: inst.userId,
            cycle: { agentId: inst.agentId },
            status: { in: [...OPEN_PART] },
          },
          include: { cycle: { select: { tradeId: true, status: true } } },
          take: 20,
        })
      : [];
  } catch (e) {
    dbOk = false;
    dbErr = e instanceof Error ? e.message : String(e);
  }

  const copyOpen = new Set(
    participants.filter((p) => p.status === 'OPEN').map((p) => p.cycle.tradeId),
  );
  const copyPending = participants
    .filter((p) => p.status === 'PENDING_ENTRY' || p.status === 'INTENT')
    .map((p) => ({ tradeId: p.cycle.tradeId, status: p.status }));
  const copyAll = new Set(participants.map((p) => p.cycle.tradeId));
  const showcaseOpen = new Set((bot.positions || []).map((p) => p.trade_id).filter(Boolean));
  const expectedMissedShowcase = new Set();
  const suppressedPreArmShowcase = new Set();
  if (inst && showcaseOpen.size > 0) {
    const armedAtMs = Date.parse(dash.relayArmedAt ?? dash.realTradingConfirmedAt ?? '');
    const [missedFillEvents, showcaseCycles] = await Promise.all([
      prisma.signalCycleEvent.findMany({
        where: {
          eventType: 'EXPIRED',
          cycle: {
            agentId: inst.agentId,
            tradeId: { in: [...showcaseOpen] },
          },
          participant: {
            userId: inst.userId,
            status: 'EXPIRED',
          },
        },
        select: {
          payload: true,
          cycle: { select: { tradeId: true } },
        },
      }),
      prisma.signalCycle.findMany({
        where: { agentId: inst.agentId, tradeId: { in: [...showcaseOpen] } },
        select: { tradeId: true, createdAt: true },
      }),
    ]);
    if (Number.isFinite(armedAtMs)) {
      for (const cycle of showcaseCycles) {
        if (cycle.createdAt.getTime() <= armedAtMs) suppressedPreArmShowcase.add(cycle.tradeId);
      }
    }
    for (const event of missedFillEvents) {
      const payload =
        event.payload && typeof event.payload === 'object' && !Array.isArray(event.payload)
          ? event.payload
          : null;
      if (
        payload?.event === 'MISSED_SHOWCASE_FILL' ||
        payload?.reason === 'MISSED_SHOWCASE_FILL'
      ) {
        expectedMissedShowcase.add(event.cycle.tradeId);
      }
    }
  }

  const alerts = [];
  const { verdict, reason } = syncVerdict({
    bot,
    copyOpen,
    copyPending,
    copyAll,
    showcaseOpen,
    expectedMissedShowcase,
    suppressedPreArmShowcase,
    entryEnabled: inst?.status === 'ACTIVE',
    instanceLastError: inst?.lastError ?? null,
    dash,
    alerts,
  });

  const book = dash.exchangeLiveBook || dash.liveBook || null;
  if (!dbOk) {
    alerts.push(`neon_unreachable:${dbErr}`);
  }

  const tick = {
    type: 'tick',
    at: new Date().toISOString(),
    syncVerdict: dbOk ? verdict : bot.ok ? 'WARN' : 'FAIL',
    syncReason: dbOk ? reason : `neon_unreachable; ${reason}`,
    dbOk,
    alerts,
    bot: bot.ok
      ? {
          source: bot.source,
          price: bot.price,
          leverage: bot.leverage,
          open: bot.positions,
          pendingOrders: bot.pendingOrders,
        }
      : { ok: false },
    copy: {
      instanceStatus: inst?.status ?? null,
      relayArmedAt: dash.relayArmedAt ?? dash.realTradingConfirmedAt ?? null,
      lastTickAt: dash.lastTickAt ?? null,
      lastError: inst?.lastError ?? null,
      open: [...copyOpen],
      pending: copyPending,
      expectedMissedShowcase: [...expectedMissedShowcase],
      suppressedPreArmShowcase: [...suppressedPreArmShowcase],
      reconcile: dash.copyRelayReconcile ?? null,
      executorHealth:
        dash.relayExecutor ?? dash.executorHealth ?? dash.relayExecutorHealth ?? null,
      signedWebhook: dash.signedWebhook ?? dash.webhookSequence ?? null,
    },
    bitfinex: book
      ? {
          derivativesUsd: book.derivativesUsd ?? book.derivativesAvailableUsd ?? null,
          positionQty: book.positionQty ?? book.netQty ?? book.qty ?? null,
          openOrders: book.openOrders ?? book.orders ?? null,
          mark: book.mark ?? book.markPrice ?? null,
        }
      : null,
    mirrorDiff: dash.mirrorDiff
      ? {
          at: dash.mirrorDiff.at ?? null,
          entryPolicy: dash.mirrorDiff.entryPolicy ?? null,
          suppressedExpectedSourceOnly:
            dash.mirrorDiff.suppressedExpectedSourceOnly ?? 0,
          total: dash.mirrorDiff.counts?.total ?? 0,
          byType: dash.mirrorDiff.counts?.byType ?? {},
          divergences: Array.isArray(dash.mirrorDiff.divergences)
            ? dash.mirrorDiff.divergences.slice(0, 20)
            : [],
        }
      : null,
  };

  console.log(JSON.stringify(tick));
} finally {
  await prisma.$disconnect();
}
