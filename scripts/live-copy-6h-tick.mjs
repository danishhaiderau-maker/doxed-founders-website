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
const LOCAL_BOT = (process.env.SHOWCASE_BOT_LOCAL_URL || 'http://127.0.0.1:7002').replace(/\/$/, '');
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

function syncVerdict({ bot, copyOpen, copyPending, copyAll, showcaseOpen, dash, alerts }) {
  if (!bot.ok) return { verdict: 'FAIL', reason: 'showcase_unreachable' };
  if (dash?.lastError) alerts.push(`lastError:${dash.lastError}`);
  const md = dash?.mirrorDiff;
  const mdTypes = md?.counts?.byType || {};
  if (mdTypes.COPY_ORDER_NO_SHOWCASE > 0) {
    alerts.push(`COPY_ORDER_NO_SHOWCASE:${mdTypes.COPY_ORDER_NO_SHOWCASE}`);
  }
  if (mdTypes.COPY_POSITION_NO_SHOWCASE > 0) {
    alerts.push(`COPY_POSITION_NO_SHOWCASE:${mdTypes.COPY_POSITION_NO_SHOWCASE}`);
  }
  if (md?.counts?.total > 0) alerts.push(`MIRROR_DIFF:${md.counts.total}`);

  const missEntry = [...showcaseOpen].filter((t) => !copyAll.has(t));
  const orphan = [...copyOpen].filter((t) => !showcaseOpen.has(t));
  const pendingWorking = [...showcaseOpen].filter((t) => !copyOpen.has(t) && copyAll.has(t));

  if (missEntry.length) alerts.push(`missEntry:${missEntry.map((t) => t.slice(0, 10)).join(',')}`);
  if (orphan.length) alerts.push(`orphan:${orphan.map((t) => t.slice(0, 10)).join(',')}`);

  const book = dash?.exchangeLiveBook || dash?.liveBook;
  const derivFree = book?.derivativesUsd ?? book?.derivativesAvailableUsd ?? null;
  if (derivFree != null && derivFree < 1 && missEntry.length) {
    alerts.push(`margin_block:$${derivFree}`);
  }

  if (alerts.some((a) => /missEntry|orphan|margin_block|COPY_ORDER|showcase_unreachable/i.test(a))) {
    return { verdict: 'FAIL', reason: alerts.join('; ') };
  }
  if (alerts.length || pendingWorking.length) {
    return { verdict: 'WARN', reason: alerts.join('; ') || 'pending_working' };
  }
  if (showcaseOpen.size === 0 && copyOpen.size === 0) {
    return { verdict: 'PASS', reason: 'both_flat' };
  }
  const matched = [...showcaseOpen].filter((t) => copyOpen.has(t)).length;
  if (matched === showcaseOpen.size && orphan.length === 0) {
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

  const alerts = [];
  const { verdict, reason } = syncVerdict({
    bot,
    copyOpen,
    copyPending,
    copyAll,
    showcaseOpen,
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
      lastTickAt: dash.lastTickAt ?? null,
      lastError: inst?.lastError ?? null,
      open: [...copyOpen],
      pending: copyPending,
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
          total: dash.mirrorDiff.counts?.total ?? 0,
          byType: dash.mirrorDiff.counts?.byType ?? {},
        }
      : null,
  };

  console.log(JSON.stringify(tick));
} finally {
  await prisma.$disconnect();
}
