/**
 * JSON probe for live-copy-6h-lifecycle-watch.ps1 — stdout only (single JSON object).
 * Read-only: :7002/api/state + Neon (LIVE_COPY_INSTANCE_ID).
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
const OPEN_PART = ['INTENT', 'PENDING_ENTRY', 'OPEN'];

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

async function fetchShowcase() {
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
        }));
      const pendingOrders = (s.orders || s.pending_orders || []).filter(
        (o) => o && !['FILLED', 'CANCELLED', 'EXPIRED'].includes(o.status),
      );
      return {
        ok: true,
        source,
        positions,
        pending: pendingOrders.length,
      };
    } catch {
      /* try next */
    }
  }
  return { ok: false, source: 'unreachable', positions: [], pending: 0 };
}

function payloadStr(p) {
  if (p == null) return '';
  if (typeof p === 'string') return p;
  try {
    return JSON.stringify(p);
  } catch {
    return String(p);
  }
}

const prisma = new PrismaClient({ datasources: { db: { url: loadDbUrl() } } });

try {
  const showcase = await fetchShowcase();
  const inst = await prisma.tradingAgentInstance.findUnique({
    where: { id: INSTANCE_ID },
    include: { agent: { select: { id: true, slug: true } } },
  });

  const dash =
    inst?.dashboardState && typeof inst.dashboardState === 'object' ? inst.dashboardState : {};

  const openParticipants = inst
    ? await prisma.signalCycleParticipant.findMany({
        where: {
          userId: inst.userId,
          cycle: { agentId: inst.agentId },
          status: { in: OPEN_PART },
        },
        include: { cycle: { select: { tradeId: true, status: true, updatedAt: true } } },
        orderBy: { updatedAt: 'desc' },
        take: 30,
      })
    : [];

  const recentEvents = inst
    ? await prisma.signalCycleEvent.findMany({
        where: {
          createdAt: { gt: new Date(Date.now() - 6 * 60 * 60 * 1000) },
          OR: [
            { participant: { userId: inst.userId } },
            { cycle: { agentId: inst.agentId, participants: { some: { userId: inst.userId } } } },
          ],
        },
        orderBy: { createdAt: 'desc' },
        take: 40,
        select: { eventType: true, payload: true, createdAt: true },
      })
    : [];

  const recentCycles = inst
    ? await prisma.signalCycle.findMany({
        where: { agentId: inst.agentId, participants: { some: { userId: inst.userId } } },
        orderBy: { updatedAt: 'desc' },
        take: 12,
        select: { tradeId: true, status: true, updatedAt: true },
      })
    : [];

  const showIds = new Set(showcase.positions.map((p) => p.trade_id).filter(Boolean));
  const copyOpenIds = openParticipants.filter((p) => p.status === 'OPEN').map((p) => p.cycle.tradeId);
  const copyOpenSet = new Set(copyOpenIds);
  let matched = 0;
  for (const id of showIds) {
    if (copyOpenSet.has(id)) matched += 1;
  }

  const book = dash.exchangeLiveBook || dash.liveBook || {};
  const reconcileDeltaBtc =
    dash.reconcileDeltaBtc ?? book.positionQty ?? book.netQty ?? book.qty ?? null;

  const out = {
    at: new Date().toISOString(),
    showcase: {
      local7002: {
        ok: showcase.ok,
        source: showcase.source,
        positions: showcase.positions,
        pending: showcase.pending,
      },
    },
    copy: {
      instance: {
        id: inst?.id ?? null,
        lastError: inst?.lastError ?? null,
        lastTickAt: dash.lastTickAt ?? null,
        reconcileDeltaBtc,
      },
      openParticipants: openParticipants.map((p) => ({
        status: p.status,
        tradeId: p.cycle.tradeId,
        cycleStatus: p.cycle.status,
      })),
      recentEvents: recentEvents.map((e) => ({
        type: e.eventType,
        payload: payloadStr(e.payload),
        at: e.createdAt.toISOString(),
      })),
      recentCycles: recentCycles.map((c) => ({
        tradeId: c.tradeId,
        cycleStatus: c.status,
        updatedAt: c.updatedAt.toISOString(),
      })),
    },
    reconcile: {
      showcaseOpen: showIds.size,
      copyOpen: copyOpenSet.size,
      matched,
    },
  };

  process.stdout.write(JSON.stringify(out));
} finally {
  await prisma.$disconnect();
}
