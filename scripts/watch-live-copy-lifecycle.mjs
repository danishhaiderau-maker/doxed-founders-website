/**
 * Live Copy + showcase lifecycle watcher (~3h sessions).
 * Read-only: bot /api/state + Neon Prisma. No Bitfinex API calls.
 *
 * Scores ACTION fidelity (not fill quality):
 *   ACTION_MISS_ENTRY — showcase OPEN, copy has no OPEN/PENDING/INTENT for trade_id
 *   ACTION_MISS_EXIT  — showcase flat, copy still OPEN (same-id or cross-id orphan)
 *
 * Poll every POLL_MS. Append -> logs/live-copy-lifecycle-watch.log
 * Console: CHANGE or ALERT only.
 * Stop: kill process or touch logs/.live-copy-lifecycle-watch.stop
 */
import { PrismaClient } from '@prisma/client';
import { readFileSync, existsSync, unlinkSync, mkdirSync, appendFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';
import { getVaultDir } from './secrets-vault-path.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, '..');
const LOG = resolve(REPO, 'logs', 'live-copy-lifecycle-watch.log');
const STOP = resolve(REPO, 'logs', '.live-copy-lifecycle-watch.stop');
const LOCAL_BOT = (process.env.SHOWCASE_BOT_LOCAL_URL || 'http://127.0.0.1:7002').replace(/\/$/, '');
const FALLBACK_BOT = resolveHomeBotPublicUrl(undefined, REPO);
const POLL_MS = Number(process.env.LIVE_COPY_WATCH_POLL_MS || 60_000);
const INSTANCE_ID = process.env.LIVE_COPY_INSTANCE_ID || 'cmq6cfwv4001jli0dqx5r31ve';
const DELTA_BTC_TOL = Number(process.env.COPY_RELAY_RECONCILE_ALERT_BTC || 0.001);
const MISSED_ENTRY_SEC = 120;
const MISSED_EXIT_SEC = 60;
const STALE_TICK_SEC = 120;

const EVENT_TYPES = [
  'FILLED',
  'EXIT',
  'SHOWCASE_MIRROR',
  'MIRROR_CATCHUP_ENTRY',
  'MIRROR_CATCHUP_SKIPPED',
  'ACTION_MISS_ENTRY',
  'MIRROR_DIFF',
];
const OPEN_PART_STATUSES = new Set(['INTENT', 'PENDING_ENTRY', 'OPEN']);
const CLOSED_PART_STATUSES = new Set([
  'CLOSED',
  'CLOSED_LOSS',
  'CLOSED_WIN',
  'EXITED',
  'FAILED',
  'REJECTED',
]);
const OK_EXIT_REASONS = new Set([
  'SHOWCASE_MIRROR',
  'SHOWCASE_MIRROR_ALREADY_FLAT',
  'SHOWCASE_VANISHED',
  'EXCHANGE_STOP',
]);

mkdirSync(dirname(LOG), { recursive: true });

function ts() {
  return new Date().toISOString().slice(11, 19);
}

function append(msg) {
  const line = `[${ts()}] ${msg}`;
  try {
    appendFileSync(LOG, line + '\n', 'utf8');
  } catch {
    /* ignore */
  }
  return line;
}

function logConsole(msg) {
  console.log(append(msg));
}

function loadDbUrl() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const vault = resolve(getVaultDir(), '.env.neon');
  if (!existsSync(vault)) throw new Error('no DATABASE_URL and no vault/.env.neon');
  for (const raw of readFileSync(vault, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    if (k.trim() === 'DATABASE_URL') return rest.join('=').trim().replace(/^"|"$/g, '');
  }
  throw new Error('DATABASE_URL not found in vault/.env.neon');
}

function pickChaseStep(order) {
  return (
    order?.chase_step ??
    order?.chaseStep ??
    order?.limit_chase_step ??
    order?.chase_bucket ??
    order?.chase_execution_bucket ??
    null
  );
}

function summarizeBot(bot, source) {
  if (!bot) return { source, ok: false };
  const price = bot.price ?? bot.mark ?? null;
  const positions = (bot.positions || []).filter(
    (p) => p && (p.status === 'OPEN' || !p.status),
  );
  const orders = bot.orders || bot.pending_orders || [];
  const pending = orders.filter((o) => o && !['FILLED', 'CANCELLED', 'EXPIRED'].includes(o.status));
  const trades = bot.trades || [];
  const closedRecent = trades.filter((t) => t?.status === 'CLOSED' || t?.exit_reason).length;
  return {
    ok: true,
    source,
    price,
    openPositions: positions.map((p) => ({
      trade_id: p.trade_id,
      entry: p.entry,
      dir: p.dir || p.direction,
    })),
    pendingOrders: pending.map((o) => ({
      trade_id: o.trade_id,
      limit: o.limit_price ?? o.limit,
      chase: pickChaseStep(o),
    })),
    closedRecent,
    equity: bot.equity,
    sessionPnl: bot.session_pnl_usd,
    tradeCount: bot.trade_count,
  };
}

async function fetchBot() {
  for (const [base, label] of [
    [LOCAL_BOT, 'local:7002'],
    [FALLBACK_BOT, 'tunnel'],
  ]) {
    try {
      const r = await fetch(`${base}/api/state`, { signal: AbortSignal.timeout(12_000) });
      if (!r.ok) continue;
      return summarizeBot(await r.json(), label);
    } catch {
      /* try next */
    }
  }
  return { ok: false, source: 'unreachable' };
}

function mirrorDiffSummary(dash) {
  const md = dash?.mirrorDiff;
  if (!md || typeof md !== 'object') return 'none';
  const counts = md.counts?.byType || {};
  const parts = [
    `showcase pos=${md.showcaseOpenPositions ?? '?'} pend=${md.showcasePendingOrders ?? '?'}`,
    `copy open=${md.copyOpenLots ?? '?'} pend=${md.copyPendingOrders ?? '?'}`,
    `div=${md.counts?.total ?? 0}`,
  ];
  const types = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${k}:${n}`)
    .join(',');
  if (types) parts.push(types);
  return parts.join(' | ');
}

function reconcileDelta(prev, cur) {
  if (!prev || !cur) return null;
  const a = prev.updatedAt || prev.at;
  const b = cur.updatedAt || cur.at;
  if (a === b) return null;
  const d0 = Number(prev.deltaBtc ?? 0);
  const d1 = Number(cur.deltaBtc ?? 0);
  return d1 - d0;
}

function snapshotKey(bot, inst, parts, events) {
  return JSON.stringify({
    bot: bot?.ok
      ? {
          price: bot.price,
          open: bot.openPositions?.map((p) => p.trade_id),
          pend: bot.pendingOrders?.map((o) => `${o.trade_id}:${o.limit}:${o.chase}`),
        }
      : 'down',
    inst: inst
      ? {
          status: inst.status,
          err: inst.lastError,
          orphan: (inst.dashboardState?.orphanOrderIds || []).length,
          delta: inst.dashboardState?.copyRelayReconcile?.deltaBtc,
          tick: inst.dashboardState?.lastTickAt,
        }
      : null,
    parts: parts.map((p) => `${p.tradeId}|${p.pStatus}`),
    ev: events.map((e) => `${e.eventType}|${e.createdAt}`),
  });
}

let prisma;
let prevKey = null;
let prevReconcile = null;
let watchStartedMs = Date.now();
/** @type {Map<string, number>} showcase trade_id -> first seen open (ms) */
const showcaseOpenSince = new Map();
/** @type {Map<string, number>} showcase trade_id -> closed at (ms) */
const showcaseClosedAt = new Map();
/** @type {Set<string>} */
const alerted = new Set();
/** @type {Set<string>} */
const seenEventKeys = new Set();

function alertOnce(key, msg) {
  if (alerted.has(key)) return;
  alerted.add(key);
  logConsole(`ALERT: ${msg}`);
}

async function resolveInstance() {
  let inst = await prisma.tradingAgentInstance.findUnique({
    where: { id: INSTANCE_ID },
    include: { agent: { select: { slug: true, name: true } }, user: { select: { id: true, name: true, platformHandle: true } } },
  });
  if (inst) return inst;

  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) return null;
  return prisma.tradingAgentInstance.findFirst({
    where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
    orderBy: { updatedAt: 'desc' },
    include: { agent: { select: { slug: true, name: true } }, user: { select: { id: true, name: true, platformHandle: true } } },
  });
}

async function readCopyState(userId, agentId) {
  const participants = await prisma.signalCycleParticipant.findMany({
    where: {
      userId,
      cycle: { agentId },
      status: { in: [...OPEN_PART_STATUSES] },
    },
    include: { cycle: { select: { tradeId: true, status: true, showcaseExitReason: true } } },
    orderBy: { updatedAt: 'desc' },
    take: 10,
  });

  const recentCycles = await prisma.signalCycle.findMany({
    where: { agentId, participants: { some: { userId } } },
    orderBy: { updatedAt: 'desc' },
    take: 8,
    include: {
      participants: { where: { userId }, select: { status: true, fillPrice: true, exitPrice: true, pnlUsd: true } },
    },
  });

  const eventWhere = {
    OR: [
      { participant: { userId } },
      { cycle: { participants: { some: { userId } } } },
    ],
    AND: {
      OR: [
        { eventType: { in: EVENT_TYPES } },
        { eventType: { startsWith: 'RECONCILE' } },
      ],
    },
  };

  const events = await prisma.signalCycleEvent.findMany({
    where: eventWhere,
    orderBy: { createdAt: 'desc' },
    take: 5,
    select: { eventType: true, createdAt: true, payload: true, cycleId: true },
  });

  return {
    participants: participants.map((p) => ({
      tradeId: p.cycle.tradeId,
      cycleStatus: p.cycle.status,
      pStatus: p.status,
      fill: p.fillPrice != null ? Number(p.fillPrice) : null,
    })),
    recentCycles,
    events,
  };
}

function formatEvent(e) {
  const pl = e.payload && typeof e.payload === 'object' ? e.payload : {};
  const bits = [
    e.eventType,
    new Date(e.createdAt).toISOString().slice(11, 19),
    pl.exit_reason != null ? `reason=${pl.exit_reason}` : null,
    pl.showcase_exit_price != null ? `show_exit=${pl.showcase_exit_price}` : null,
    pl.exit_slippage_usd != null ? `slip=$${pl.exit_slippage_usd}` : null,
    pl.trade_id != null ? `tid=${String(pl.trade_id).slice(0, 10)}` : null,
    pl.diff_type != null ? `diff=${pl.diff_type}` : null,
  ].filter(Boolean);
  return bits.join(' ');
}

function checkAlerts(bot, inst, copy, nowMs) {
  const dash = inst?.dashboardState || {};
  const orphanN = Array.isArray(dash.orphanOrderIds) ? dash.orphanOrderIds.length : 0;
  if (orphanN > 0) alertOnce(`orphan:${inst.id}`, `orphanOrders=${orphanN} on instance ${inst.id}`);

  const recon = dash.copyRelayReconcile;
  if (recon && Math.abs(Number(recon.deltaBtc ?? 0)) > DELTA_BTC_TOL) {
    alertOnce(
      `delta:${recon.updatedAt}`,
      `deltaBtc=${Number(recon.deltaBtc).toFixed(6)} (tol=${DELTA_BTC_TOL}) exchange=${recon.exchangePositionQty} ledger=${recon.ledgerOpenQty}`,
    );
  }

  if (inst?.lastError) alertOnce(`err:${inst.lastError}`, `lastError=${inst.lastError}`);

  const tickAt = dash.lastTickAt ? Date.parse(dash.lastTickAt) : NaN;
  if (Number.isFinite(tickAt) && nowMs - tickAt > STALE_TICK_SEC * 1000) {
    alertOnce(`stale:${Math.floor(tickAt / 60_000)}`, `lastTickAt stale ${Math.round((nowMs - tickAt) / 1000)}s`);
  }

  if (!bot?.ok) alertOnce('bot:down', 'showcase bot unreachable (local + tunnel)');

  const copyOpenIds = new Set(copy.participants.filter((p) => OPEN_PART_STATUSES.has(p.pStatus)).map((p) => p.tradeId));
  const showcaseOpenIds = new Set((bot?.openPositions || []).map((p) => p.trade_id).filter(Boolean));

  for (const tid of showcaseOpenIds) {
    if (!showcaseOpenSince.has(tid)) showcaseOpenSince.set(tid, nowMs);
    // PENDING_ENTRY / INTENT count as entry action taken (not an action miss).
    if (!copyOpenIds.has(tid) && nowMs - showcaseOpenSince.get(tid) > MISSED_ENTRY_SEC * 1000) {
      alertOnce(
        `action-miss-entry:${tid}`,
        `ACTION_MISS_ENTRY showcase OPEN trade_id=${tid} but copy has no position and no working order >${MISSED_ENTRY_SEC}s`,
      );
    }
  }
  for (const tid of [...showcaseOpenSince.keys()]) {
    if (!showcaseOpenIds.has(tid)) {
      if (!showcaseClosedAt.has(tid)) showcaseClosedAt.set(tid, nowMs);
      showcaseOpenSince.delete(tid);
    }
  }

  for (const [tid, closedMs] of showcaseClosedAt) {
    if (copyOpenIds.has(tid) && nowMs - closedMs > MISSED_EXIT_SEC * 1000) {
      alertOnce(
        `action-miss-exit:${tid}`,
        `ACTION_MISS_EXIT showcase CLOSED trade_id=${tid} but copy still OPEN >${MISSED_EXIT_SEC}s`,
      );
    }
    if (!copyOpenIds.has(tid) && nowMs - closedMs > MISSED_EXIT_SEC * 1000) {
      showcaseClosedAt.delete(tid);
    }
  }

  // Cross-ID orphan: copy OPEN on a trade_id that is not an open showcase position.
  // Same-ID miss-exit above never fires when copy filled a different trade than showcase.
  // SHOWCASE_POSITION_ABSENT (policy v3+) market-closes after 2 consecutive ticks.
  const mdDivs = Array.isArray(dash?.mirrorDiff?.divergences) ? dash.mirrorDiff.divergences : [];
  for (const d of mdDivs) {
    if (d?.type !== 'COPY_POSITION_NO_SHOWCASE' || !d.tradeId) continue;
    alertOnce(
      `action-miss-exit-crossid:${d.tradeId}`,
      `ACTION_MISS_EXIT COPY_POSITION_NO_SHOWCASE trade_id=${d.tradeId} — copy OPEN with no matching showcase position (SHOWCASE_POSITION_ABSENT should fire within ~2 ticks)`,
    );
  }

  for (const e of copy.events) {
    const ek = `${e.eventType}|${e.createdAt}|${e.cycleId}`;
    if (seenEventKeys.has(ek)) continue;
    seenEventKeys.add(ek);
    if (Date.parse(e.createdAt) < watchStartedMs - 5000) continue;

    if (e.eventType === 'EXIT') {
      const exitReason = e.payload?.exit_reason;
      if (exitReason && !OK_EXIT_REASONS.has(String(exitReason))) {
        alertOnce(
          `bad-exit:${e.cycleId}:${exitReason}`,
          `new EXIT exit_reason=${exitReason} (expected SHOWCASE_MIRROR/VANISHED/EXCHANGE_STOP) cycle=${e.cycleId?.slice(0, 10)}`,
        );
      }
    }
    if (e.eventType === 'ACTION_MISS_ENTRY' || e.eventType === 'MIRROR_CATCHUP_SKIPPED') {
      const tid = e.payload?.trade_id ?? '?';
      const reason = e.payload?.reason ?? e.eventType;
      alertOnce(
        `neon-action-miss-entry:${e.cycleId}:${reason}`,
        `ACTION_MISS_ENTRY neon event reason=${reason} tid=${String(tid).slice(0, 12)} cycle=${e.cycleId?.slice(0, 10)}`,
      );
    }
  }

  for (const [tid, closedMs] of showcaseClosedAt) {
    if (!copyOpenIds.has(tid)) continue;
    const exitEvt = copy.events.find(
      (e) =>
        e.eventType === 'EXIT' &&
        Date.parse(e.createdAt) >= watchStartedMs - 5000 &&
        copy.recentCycles.some((c) => c.id === e.cycleId && c.tradeId === tid),
    );
    const exitMs = exitEvt ? Date.parse(exitEvt.createdAt) : null;
    if (exitMs != null && exitMs - closedMs > MISSED_EXIT_SEC * 1000) {
      alertOnce(
        `slow-exit:${tid}`,
        `SHOWCASE closed ${Math.round((exitMs - closedMs) / 1000)}s before copy EXIT for ${tid.slice(0, 10)}`,
      );
    }
  }
}

async function tick() {
  const nowMs = Date.now();
  const bot = await fetchBot();
  const inst = await resolveInstance();
  const copy = inst
    ? await readCopyState(inst.userId, inst.agentId)
    : { participants: [], recentCycles: [], events: [] };

  const dash = inst?.dashboardState || {};
  const recon = dash.copyRelayReconcile || null;
  const reconDelta = reconcileDelta(prevReconcile, recon);
  if (recon) prevReconcile = recon;

  const parts = copy.participants;
  const key = snapshotKey(bot, inst, parts, copy.events);
  const changed = key !== prevKey;
  prevKey = key;

  checkAlerts(bot, inst, copy, nowMs);

  if (!changed) {
    append(`... steady (bot=${bot.ok ? bot.source : 'DOWN'} inst=${inst?.status ?? 'none'} open=${parts.length})`);
    return { bot, inst, copy, changed: false };
  }

  if (bot.ok) {
    logConsole(
      `BOT [${bot.source}] mark=$${bot.price} open=${bot.openPositions.length} pend=${bot.pendingOrders.length} closed_trades=${bot.closedRecent} eq=$${bot.equity} pnl=$${bot.sessionPnl}`,
    );
    for (const o of bot.pendingOrders) {
      logConsole(`     PEND ${o.trade_id?.slice(0, 12)} limit=${o.limit} chase=${o.chase ?? '-'}`);
    }
    for (const p of bot.openPositions) {
      logConsole(`     OPEN ${p.trade_id?.slice(0, 12)} ${p.dir} entry=${p.entry}`);
    }
  } else {
    logConsole(`BOT DOWN (tried local + ${FALLBACK_BOT})`);
  }

  if (inst) {
    const user = inst.user?.platformHandle || inst.user?.name || inst.userId;
    logConsole(
      `COPY user=${user} inst=${inst.id.slice(0, 12)} status=${inst.status} provider=${inst.exchangeProvider}${inst.lastError ? ` err=${inst.lastError}` : ''}${inst.expiresAt ? ` exp=${inst.expiresAt.toISOString().slice(0, 16)}` : ''}`,
    );
    logConsole(`     mirrorDiff: ${mirrorDiffSummary(dash)}`);
    logConsole(
      `     orphanOrders=${Array.isArray(dash.orphanOrderIds) ? dash.orphanOrderIds.length : 0} lastTickAt=${dash.lastTickAt ?? 'n/a'} reconcile deltaBtc=${recon?.deltaBtc ?? 'n/a'}${reconDelta != null ? ` (Δ${reconDelta.toFixed(6)})` : ''}`,
    );
  } else {
    logConsole('COPY no Bitfinex instance found');
  }

  for (const p of parts) {
    logConsole(
      `     PART ${p.tradeId?.slice(0, 12)} cycle=${p.cycleStatus} status=${p.pStatus} fill=${p.fill ?? '-'}`,
    );
  }

  for (const e of copy.events) {
    logConsole(`     EVT ${formatEvent(e)}`);
  }

  const showcaseOpen = new Set((bot.openPositions || []).map((p) => p.trade_id));
  const copyOpen = new Set(parts.filter((p) => OPEN_PART_STATUSES.has(p.pStatus)).map((p) => p.tradeId));
  const matched = [...showcaseOpen].filter((t) => copyOpen.has(t));
  const missCopy = [...showcaseOpen].filter((t) => !copyOpen.has(t));
  if (showcaseOpen.size || copyOpen.size) {
    logConsole(
      `MATCH open showcase=${showcaseOpen.size} copy=${copyOpen.size} matched=${matched.length}${missCopy.length ? ` MISSING_ON_COPY=${missCopy.map((t) => t.slice(0, 10)).join(',')}` : ''}`,
    );
  }

  return { bot, inst, copy, changed: true };
}

async function main() {
  append('=== live-copy lifecycle watcher START ===');
  watchStartedMs = Date.now();
  logConsole(`polling every ${POLL_MS / 1000}s | instance=${INSTANCE_ID} | log=${LOG}`);
  process.env.DATABASE_URL = loadDbUrl();
  prisma = new PrismaClient({ log: ['error'] });
  await prisma.$connect();
  logConsole('Neon connected');

  await tick();

  while (true) {
    if (existsSync(STOP)) {
      try {
        unlinkSync(STOP);
      } catch {
        /* ignore */
      }
      logConsole('stop file -> exit');
      break;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
    try {
      await tick();
    } catch (e) {
      logConsole(`tick err: ${e.message}`);
    }
  }

  await prisma.$disconnect();
  append('=== watcher STOP ===');
}

main().catch((e) => {
  logConsole(`fatal: ${e.message}`);
  process.exit(1);
});

export { tick, fetchBot, resolveInstance, readCopyState };
