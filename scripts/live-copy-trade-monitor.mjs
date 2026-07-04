/**
 * Active Live Copy trade monitor (showcase :7002 vs Cheetah hire).
 * Polls bot + Neon, tails lifecycle log keywords, writes status lines.
 *
 * Scores ACTION fidelity (not fill quality):
 *   ACTION_MISS_ENTRY — showcase OPEN, copy has no OPEN/PENDING/INTENT for that trade_id
 *   ACTION_MISS_EXIT  — showcase flat, copy still OPEN (same trade_id or cross-ID orphan)
 * Fill price / capture rate differences are acceptable and not alerted as failures.
 *
 * Optional safe operational close only if orphan OPEN persists > ORPHAN_FORCE_SEC
 * after auto-heal window (SHOWCASE_POSITION_ABSENT) and FORCE_ORPHAN_CLOSE=1.
 *
 * Stop: touch logs/.live-copy-trade-monitor.stop
 */
import { PrismaClient } from '@prisma/client';
import {
  readFileSync,
  existsSync,
  unlinkSync,
  mkdirSync,
  appendFileSync,
  writeFileSync,
} from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';
import { getVaultDir } from './secrets-vault-path.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, '..');
const LOG = resolve(REPO, 'logs', 'live-copy-trade-monitor.log');
const SUMMARY = resolve(REPO, 'logs', 'live-copy-trade-monitor-summary.json');
const STOP = resolve(REPO, 'logs', '.live-copy-trade-monitor.stop');
const LIFECYCLE_LOG = resolve(REPO, 'logs', 'live-copy-lifecycle-watch.log');
const LOCAL_BOT = (process.env.SHOWCASE_BOT_LOCAL_URL || 'http://127.0.0.1:7002').replace(/\/$/, '');
const FALLBACK_BOT = resolveHomeBotPublicUrl(undefined, REPO);
const POLL_MS = Number(process.env.MONITOR_POLL_MS || 75_000);
const DURATION_MS = Number(process.env.MONITOR_DURATION_MS || 2.5 * 60 * 60 * 1000);
const INSTANCE_ID = process.env.LIVE_COPY_INSTANCE_ID || 'cmq6cfwv4001jli0dqx5r31ve';
const ORPHAN_ALERT_SEC = 60;
const ORPHAN_FORCE_SEC = Number(process.env.ORPHAN_FORCE_SEC || 180);
const FORCE_ORPHAN_CLOSE = process.env.FORCE_ORPHAN_CLOSE === '1';
const STALE_TICK_SEC = 120;
const PENDING_LEAK_SEC = 300;

const OPEN_PART = new Set(['INTENT', 'PENDING_ENTRY', 'OPEN']);
const CLOSED_PART = new Set([
  'CLOSED',
  'CLOSED_LOSS',
  'CLOSED_WIN',
  'EXITED',
  'FAILED',
  'REJECTED',
]);

mkdirSync(dirname(LOG), { recursive: true });

function ts() {
  return new Date().toISOString();
}

function log(msg, { alert = false } = {}) {
  const line = `[${ts()}] ${alert ? 'ALERT ' : ''}${msg}`;
  appendFileSync(LOG, line + '\n', 'utf8');
  console.log(line);
  return line;
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
  throw new Error('DATABASE_URL not found');
}

/** @type {import('@prisma/client').PrismaClient} */
let prisma;

const state = {
  startedAt: Date.now(),
  polls: 0,
  entries: [],
  exits: [],
  alerts: [],
  orphansSeen: [],
  showcasePositionAbsentFired: false,
  showcasePositionAbsentEvents: [],
  forceCloses: [],
  bitfinexLiveOn: false,
  tunnelDownCount: 0,
  lastTickStale: false,
  pendingLeaks: [],
  missEntries: [],
  missExits: [],
  /** Neon ACTION_MISS_ENTRY / MIRROR_CATCHUP_SKIPPED events seen this session */
  actionMissEntryEvents: [],
  actionMissExitEvents: [],
  lifecycleKeywords: {
    SHOWCASE_MIRROR: 0,
    SHOWCASE_POSITION_ABSENT: 0,
    COPY_POSITION_NO_SHOWCASE: 0,
    SHOWCASE_VANISHED: 0,
    ACTION_MISS_ENTRY: 0,
    ACTION_MISS_EXIT: 0,
    MIRROR_CATCHUP_ENTRY: 0,
    MIRROR_CATCHUP_SKIPPED: 0,
    mirrorDiff: 0,
    orphan: 0,
    errors: 0,
  },
  lifecycleLogOffset: 0,
  prevShowcaseOpen: new Set(),
  prevCopyOpen: new Set(),
  /** tradeId -> firstSeenMs for copy OPEN with no showcase match */
  orphanSince: new Map(),
  /** tradeId -> firstSeenMs for PENDING_ENTRY */
  pendingSince: new Map(),
  /** tradeId -> firstSeenMs showcase open missing on copy */
  missEntrySince: new Map(),
  /** tradeId -> firstSeenMs showcase closed but copy still open */
  missExitSince: new Map(),
  alerted: new Set(),
  seenEventKeys: new Set(),
  lastStatus: null,
};

function alertOnce(key, msg) {
  if (state.alerted.has(key)) return;
  state.alerted.add(key);
  state.alerts.push({ at: ts(), key, msg });
  log(msg, { alert: true });
}

function alertRepeat(key, msg, everyMs = 120_000) {
  const bucket = `${key}:${Math.floor(Date.now() / everyMs)}`;
  if (state.alerted.has(bucket)) return;
  state.alerted.add(bucket);
  state.alerts.push({ at: ts(), key, msg });
  log(msg, { alert: true });
}

async function fetchBotFull() {
  for (const [base, label] of [
    [LOCAL_BOT, 'local:7002'],
    [FALLBACK_BOT, 'tunnel'],
  ]) {
    try {
      const r = await fetch(`${base}/api/state`, { signal: AbortSignal.timeout(12_000) });
      if (!r.ok) continue;
      const bot = await r.json();
      const positions = (bot.positions || []).filter((p) => p && (p.status === 'OPEN' || !p.status));
      const orders = bot.orders || bot.pending_orders || [];
      const pending = orders.filter((o) => o && !['FILLED', 'CANCELLED', 'EXPIRED'].includes(o.status));
      const trades = bot.trades || [];
      const closed = trades.filter((t) => t?.status === 'CLOSED' || t?.exit_reason);
      return {
        ok: true,
        source: label,
        price: bot.price ?? bot.mark ?? null,
        equity: bot.equity,
        sessionPnl: bot.session_pnl_usd,
        bitfinexLiveEnabled: Boolean(bot.bitfinex_live_enabled),
        liveArmed: Boolean(bot.live_armed),
        openPositions: positions.map((p) => ({
          trade_id: p.trade_id,
          entry: p.entry,
          dir: p.dir || p.direction,
        })),
        pendingOrders: pending.map((o) => ({
          trade_id: o.trade_id,
          limit: o.limit_price ?? o.limit,
        })),
        closedRecent: closed.slice(0, 8).map((t) => ({
          trade_id: t.trade_id,
          exit_reason: t.exit_reason,
          pnl: t.pnl_usd ?? t.pnl,
        })),
        closedCount: closed.length,
      };
    } catch {
      /* next */
    }
  }
  return { ok: false, source: 'unreachable' };
}

async function resolveInstance() {
  return prisma.tradingAgentInstance.findUnique({
    where: { id: INSTANCE_ID },
    include: {
      agent: { select: { id: true, slug: true } },
      user: { select: { id: true, platformHandle: true, name: true } },
    },
  });
}

async function readCopy(userId, agentId) {
  const participants = await prisma.signalCycleParticipant.findMany({
    where: {
      userId,
      cycle: { agentId },
      status: { in: [...OPEN_PART] },
    },
    include: {
      cycle: {
        select: {
          id: true,
          tradeId: true,
          status: true,
          showcaseExitReason: true,
          updatedAt: true,
        },
      },
    },
    orderBy: { updatedAt: 'desc' },
    take: 20,
  });

  const events = await prisma.signalCycleEvent.findMany({
    where: {
      OR: [
        { participant: { userId } },
        { cycle: { participants: { some: { userId } } } },
      ],
      eventType: {
        in: [
          'FILLED',
          'EXIT',
          'SHOWCASE_MIRROR',
          'MIRROR_CATCHUP_ENTRY',
          'MIRROR_CATCHUP_SKIPPED',
          'ACTION_MISS_ENTRY',
          'MIRROR_DIFF',
          'SHOWCASE_POSITION_ABSENT',
        ],
      },
    },
    orderBy: { createdAt: 'desc' },
    take: 30,
    select: {
      eventType: true,
      createdAt: true,
      payload: true,
      cycleId: true,
      participantId: true,
    },
  });

  return {
    participants: participants.map((p) => ({
      id: p.id,
      pStatus: p.status,
      fill: p.fillPrice != null ? Number(p.fillPrice) : null,
      tradeId: p.cycle.tradeId,
      cycleId: p.cycle.id,
      cycleStatus: p.cycle.status,
      showcaseExitReason: p.cycle.showcaseExitReason,
    })),
    events,
  };
}

function scanLifecycleLog() {
  if (!existsSync(LIFECYCLE_LOG)) return;
  const buf = readFileSync(LIFECYCLE_LOG, 'utf8');
  const chunk = buf.slice(state.lifecycleLogOffset);
  state.lifecycleLogOffset = buf.length;
  if (!chunk) return;

  const patterns = [
    ['SHOWCASE_POSITION_ABSENT', /SHOWCASE_POSITION_ABSENT/i],
    ['SHOWCASE_MIRROR', /SHOWCASE_MIRROR/],
    ['COPY_POSITION_NO_SHOWCASE', /COPY_POSITION_NO_SHOWCASE/],
    ['SHOWCASE_VANISHED', /SHOWCASE_VANISHED/],
    ['ACTION_MISS_ENTRY', /ACTION_MISS.*ENTRY|ACTION-MISS.*ENTRY/i],
    ['ACTION_MISS_EXIT', /ACTION_MISS.*EXIT|ACTION-MISS.*EXIT/i],
    ['MIRROR_CATCHUP_ENTRY', /MIRROR_CATCHUP_ENTRY|MIRROR-CATCHUP/],
    ['MIRROR_CATCHUP_SKIPPED', /MIRROR_CATCHUP_SKIPPED/],
    ['mirrorDiff', /mirrorDiff|MIRROR_DIFF/],
    ['orphan', /orphan|ORPHAN/i],
    ['errors', /\b(ALERT|tick err|fatal|lastError)/i],
  ];
  for (const line of chunk.split(/\r?\n/)) {
    if (!line.trim()) continue;
    for (const [key, re] of patterns) {
      if (re.test(line)) state.lifecycleKeywords[key] += 1;
    }
    if (/SHOWCASE_POSITION_ABSENT/i.test(line)) {
      state.showcasePositionAbsentFired = true;
      state.showcasePositionAbsentEvents.push(line.slice(0, 240));
      log(`LIFECYCLE: ${line.slice(0, 200)}`);
    }
    if (/\bALERT\b/.test(line)) {
      alertRepeat(`lc:${line.slice(0, 80)}`, `lifecycle: ${line.replace(/^\[[^\]]+\]\s*/, '').slice(0, 200)}`);
    }
  }
}

async function forceCloseOrphan(part) {
  const cycle = await prisma.signalCycle.findUnique({ where: { id: part.cycleId } });
  if (!cycle) {
    log(`force-close skip: cycle missing ${part.cycleId}`);
    return false;
  }
  if (cycle.status === 'CLOSED' || cycle.status === 'EXPIRED') {
    log(`force-close skip: cycle already ${cycle.status} tid=${part.tradeId}`);
    return false;
  }
  if (!FORCE_ORPHAN_CLOSE) {
    log(
      `ORPHAN PERSISTED >${ORPHAN_FORCE_SEC}s tid=${part.tradeId} cycle=${part.cycleId} — would force CLOSED (set FORCE_ORPHAN_CLOSE=1 to act)`,
      { alert: true },
    );
    return false;
  }
  const updated = await prisma.signalCycle.update({
    where: { id: part.cycleId },
    data: {
      status: 'CLOSED',
      closedAt: new Date(),
      showcaseExitReason: 'OPERATOR_FORCE_MIRROR_EXIT_CROSS_ID',
    },
  });
  const rec = {
    at: ts(),
    cycleId: updated.id,
    tradeId: updated.tradeId,
    participantId: part.id,
    fill: part.fill,
  };
  state.forceCloses.push(rec);
  log(
    `FORCE CLOSED cycle=${updated.id} tid=${updated.tradeId} (participant still OPEN until Railway exitPendingCycles)`,
    { alert: true },
  );
  return true;
}

async function tick() {
  const now = Date.now();
  state.polls += 1;
  scanLifecycleLog();

  const bot = await fetchBotFull();
  const inst = await resolveInstance();
  const dash = inst?.dashboardState || {};
  const copy = inst ? await readCopy(inst.userId, inst.agentId) : { participants: [], events: [] };

  if (!bot.ok) {
    state.tunnelDownCount += 1;
    alertRepeat('bot:down', 'showcase bot unreachable (local:7002 + tunnel)');
  } else if (bot.bitfinexLiveEnabled || bot.liveArmed) {
    state.bitfinexLiveOn = true;
    alertRepeat(
      'bitfinex-live-on',
      `Bitfinex Live ON on :7002 (bitfinex_live_enabled=${bot.bitfinexLiveEnabled} live_armed=${bot.liveArmed}) — MUST stay OFF`,
    );
  }

  if (inst?.lastError) {
    alertRepeat(`hire-err:${inst.lastError}`, `hire lastError=${inst.lastError}`);
  }

  const tickAt = dash.lastTickAt ? Date.parse(dash.lastTickAt) : NaN;
  if (Number.isFinite(tickAt) && now - tickAt > STALE_TICK_SEC * 1000) {
    state.lastTickStale = true;
    alertRepeat(
      'tick-stale',
      `hire tick stalled ${Math.round((now - tickAt) / 1000)}s (lastTickAt=${dash.lastTickAt})`,
    );
  } else {
    state.lastTickStale = false;
  }

  const showcaseOpen = new Set((bot.openPositions || []).map((p) => p.trade_id).filter(Boolean));
  const copyOpenParts = copy.participants.filter((p) => p.pStatus === 'OPEN');
  const copyPendingParts = copy.participants.filter((p) => p.pStatus === 'PENDING_ENTRY' || p.pStatus === 'INTENT');
  const copyOpen = new Set(copyOpenParts.map((p) => p.tradeId));
  const copyAllActive = new Set(copy.participants.map((p) => p.tradeId));

  // Entry / exit transitions (showcase)
  for (const tid of showcaseOpen) {
    if (!state.prevShowcaseOpen.has(tid)) {
      state.entries.push({ at: ts(), side: 'showcase', tradeId: tid });
      log(`ENTRY showcase ${tid}`);
    }
  }
  for (const tid of state.prevShowcaseOpen) {
    if (!showcaseOpen.has(tid)) {
      state.exits.push({ at: ts(), side: 'showcase', tradeId: tid });
      log(`EXIT showcase ${tid}`);
      if (!state.missExitSince.has(tid)) state.missExitSince.set(tid, now);
    }
  }
  state.prevShowcaseOpen = new Set(showcaseOpen);

  for (const tid of copyOpen) {
    if (!state.prevCopyOpen.has(tid)) {
      state.entries.push({ at: ts(), side: 'copy', tradeId: tid });
      log(`ENTRY copy ${tid}`);
    }
  }
  for (const tid of state.prevCopyOpen) {
    if (!copyOpen.has(tid)) {
      state.exits.push({ at: ts(), side: 'copy', tradeId: tid });
      log(`EXIT copy ${tid}`);
      state.orphanSince.delete(tid);
      state.missExitSince.delete(tid);
    }
  }
  state.prevCopyOpen = new Set(copyOpen);

  // ACTION_MISS_ENTRY: showcase OPEN, copy has no OPEN/PENDING/INTENT for that trade_id.
  // PENDING_ENTRY / INTENT = entry action already taken (order working) — not a miss.
  for (const tid of showcaseOpen) {
    if (copyOpen.has(tid) || copyAllActive.has(tid)) {
      state.missEntrySince.delete(tid);
      continue;
    }
    if (!state.missEntrySince.has(tid)) state.missEntrySince.set(tid, now);
    const age = (now - state.missEntrySince.get(tid)) / 1000;
    if (age > 120) {
      alertRepeat(
        `action-miss-entry:${tid}`,
        `ACTION_MISS_ENTRY showcase OPEN ${tid.slice(0, 12)} — copy has no position and no working order for ${Math.round(age)}s`,
      );
      if (!state.missEntries.find((m) => m.tradeId === tid)) {
        state.missEntries.push({
          kind: 'ACTION_MISS_ENTRY',
          tradeId: tid,
          since: ts(),
          ageSec: Math.round(age),
        });
      }
    }
  }
  for (const tid of [...state.missEntrySince.keys()]) {
    if (!showcaseOpen.has(tid)) state.missEntrySince.delete(tid);
  }

  // ACTION_MISS_EXIT: showcase flat, copy still OPEN same trade_id
  for (const [tid, since] of state.missExitSince) {
    if (!copyOpen.has(tid)) {
      state.missExitSince.delete(tid);
      continue;
    }
    const age = (now - since) / 1000;
    if (age > 60) {
      alertRepeat(
        `action-miss-exit:${tid}`,
        `ACTION_MISS_EXIT showcase flat but copy OPEN same tid=${tid.slice(0, 12)} for ${Math.round(age)}s (SHOWCASE_MIRROR / SHOWCASE_POSITION_ABSENT should fire)`,
      );
      if (!state.missExits.find((m) => m.tradeId === tid)) {
        state.missExits.push({
          kind: 'ACTION_MISS_EXIT',
          tradeId: tid,
          since: ts(),
          ageSec: Math.round(age),
        });
      }
    }
  }

  // Cross-ID orphan: copy OPEN, trade_id not in showcase open
  const mdDivs = Array.isArray(dash?.mirrorDiff?.divergences) ? dash.mirrorDiff.divergences : [];
  for (const d of mdDivs) {
    if (d?.type === 'COPY_POSITION_NO_SHOWCASE' && d.tradeId) {
      alertRepeat(
        `md-orphan:${d.tradeId}`,
        `mirrorDiff COPY_POSITION_NO_SHOWCASE tid=${d.tradeId}`,
      );
    }
  }

  for (const part of copyOpenParts) {
    const tid = part.tradeId;
    if (showcaseOpen.has(tid)) {
      state.orphanSince.delete(tid);
      continue;
    }
    // cycle already CLOSED → exit path should be running
    if (part.cycleStatus === 'CLOSED' || part.cycleStatus === 'EXPIRED') {
      alertRepeat(
        `exit-pending:${tid}`,
        `copy OPEN tid=${tid.slice(0, 12)} cycle already ${part.cycleStatus} — waiting exitPendingCycles`,
      );
      continue;
    }
    if (!state.orphanSince.has(tid)) {
      state.orphanSince.set(tid, now);
      state.orphansSeen.push({ tradeId: tid, cycleId: part.cycleId, since: ts() });
      log(`ORPHAN start tid=${tid} cycle=${part.cycleId} fill=${part.fill}`, { alert: true });
    }
    const age = (now - state.orphanSince.get(tid)) / 1000;
    if (age > ORPHAN_ALERT_SEC) {
      alertRepeat(
        `orphan:${tid}`,
        `orphan OPEN copy tid=${tid.slice(0, 12)} no showcase match for ${Math.round(age)}s (auto-heal ~2 ticks)`,
      );
    }
    if (age > ORPHAN_FORCE_SEC) {
      await forceCloseOrphan(part);
      state.orphanSince.delete(tid);
    }
  }
  for (const tid of [...state.orphanSince.keys()]) {
    if (!copyOpen.has(tid)) state.orphanSince.delete(tid);
  }

  // PENDING_ENTRY leaks
  const pendingIds = new Set(copyPendingParts.map((p) => p.tradeId));
  for (const part of copyPendingParts) {
    if (!state.pendingSince.has(part.tradeId)) state.pendingSince.set(part.tradeId, now);
    const age = (now - state.pendingSince.get(part.tradeId)) / 1000;
    if (age > PENDING_LEAK_SEC) {
      alertRepeat(
        `pending-leak:${part.tradeId}`,
        `PENDING_ENTRY leak tid=${part.tradeId?.slice(0, 12)} age=${Math.round(age)}s cycle=${part.cycleStatus}`,
      );
      if (!state.pendingLeaks.find((p) => p.tradeId === part.tradeId)) {
        state.pendingLeaks.push({ tradeId: part.tradeId, ageSec: Math.round(age) });
      }
    }
  }
  for (const tid of [...state.pendingSince.keys()]) {
    if (!pendingIds.has(tid)) state.pendingSince.delete(tid);
  }

  // New events of interest
  for (const e of copy.events) {
    const ek = `${e.eventType}|${e.createdAt}|${e.cycleId}`;
    if (state.seenEventKeys.has(ek)) continue;
    state.seenEventKeys.add(ek);
    if (Date.parse(e.createdAt) < state.startedAt - 5000) continue;
    const pl = e.payload && typeof e.payload === 'object' ? e.payload : {};
    const reason = pl.exit_reason || pl.reason || '';
    if (e.eventType === 'SHOWCASE_POSITION_ABSENT' || String(reason).includes('SHOWCASE_POSITION_ABSENT')) {
      state.showcasePositionAbsentFired = true;
      state.showcasePositionAbsentEvents.push({
        at: e.createdAt,
        cycleId: e.cycleId,
        payload: pl,
      });
      log(`EVENT SHOWCASE_POSITION_ABSENT cycle=${e.cycleId} ${JSON.stringify(pl).slice(0, 160)}`);
    }
    if (e.eventType === 'ACTION_MISS_ENTRY' || e.eventType === 'MIRROR_CATCHUP_SKIPPED') {
      state.lifecycleKeywords.ACTION_MISS_ENTRY += 1;
      if (e.eventType === 'MIRROR_CATCHUP_SKIPPED') state.lifecycleKeywords.MIRROR_CATCHUP_SKIPPED += 1;
      const rec = {
        at: e.createdAt,
        cycleId: e.cycleId,
        tradeId: pl.trade_id,
        reason: pl.reason || reason || e.eventType,
      };
      state.actionMissEntryEvents.push(rec);
      log(
        `ACTION_MISS_ENTRY ${new Date(e.createdAt).toISOString().slice(11, 19)} reason=${rec.reason} tid=${pl.trade_id || '-'}`,
        { alert: true },
      );
    }
    if (e.eventType === 'MIRROR_CATCHUP_ENTRY') {
      state.lifecycleKeywords.MIRROR_CATCHUP_ENTRY += 1;
      log(
        `EVENT MIRROR_CATCHUP_ENTRY ${new Date(e.createdAt).toISOString().slice(11, 19)} tid=${pl.trade_id || '-'} slip=$${pl.slip_usd ?? '?'}`,
      );
    }
    if (e.eventType === 'EXIT' || e.eventType === 'FILLED' || e.eventType === 'SHOWCASE_MIRROR') {
      log(
        `EVENT ${e.eventType} ${new Date(e.createdAt).toISOString().slice(11, 19)} reason=${reason || '-'} tid=${pl.trade_id || '-'}`,
      );
    }
  }

  const matched = [...showcaseOpen].filter((t) => copyOpen.has(t));
  const missCopy = [...showcaseOpen].filter((t) => !copyOpen.has(t));
  const orphanCopy = [...copyOpen].filter((t) => !showcaseOpen.has(t));
  const mdSummary = (() => {
    const md = dash.mirrorDiff;
    if (!md) return 'none';
    const counts = md.counts?.byType || {};
    const types = Object.entries(counts)
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${k}:${n}`)
      .join(',');
    return `show=${md.showcaseOpenPositions ?? '?'} copyOpen=${md.copyOpenLots ?? '?'} div=${md.counts?.total ?? 0}${types ? ` ${types}` : ''}`;
  })();

  // Action-match snapshot (not fill-match): pending counts as entry action taken.
  const actionEntryPending = [...showcaseOpen].filter(
    (t) => !copyOpen.has(t) && copyAllActive.has(t),
  ).length;
  const actionEntryMiss = [...showcaseOpen].filter((t) => !copyAllActive.has(t)).length;
  const actionExitMiss = orphanCopy.length;

  const status = [
    `poll#${state.polls}`,
    bot.ok ? `bot=${bot.source} mark=$${bot.price} showOpen=${showcaseOpen.size}` : 'bot=DOWN',
    `copyOpen=${copyOpen.size} pend=${copyPendingParts.length}`,
    `actionMatch entry=${matched.length} pend=${actionEntryPending} missEntry=${actionEntryMiss} missExit=${actionExitMiss}`,
    missCopy.length ? `missCopy=${missCopy.map((t) => t.slice(0, 10)).join(',')}` : 'missCopy=0',
    orphanCopy.length ? `orphan=${orphanCopy.map((t) => t.slice(0, 10)).join(',')}` : 'orphan=0',
    `tick=${dash.lastTickAt ? Math.round((now - Date.parse(dash.lastTickAt)) / 1000) + 's' : 'n/a'}`,
    `bfxLive=${bot.ok ? bot.bitfinexLiveEnabled : '?'}`,
    `absentFired=${state.showcasePositionAbsentFired}`,
    `entries=${state.entries.length} exits=${state.exits.length} alerts=${state.alerts.length}`,
    `md=${mdSummary}`,
  ].join(' | ');

  state.lastStatus = status;
  log(status);

  writeFileSync(
    SUMMARY,
    JSON.stringify(
      {
        ...state,
        orphanSince: Object.fromEntries(state.orphanSince),
        pendingSince: Object.fromEntries(state.pendingSince),
        missEntrySince: Object.fromEntries(state.missEntrySince),
        missExitSince: Object.fromEntries(state.missExitSince),
        prevShowcaseOpen: [...state.prevShowcaseOpen],
        prevCopyOpen: [...state.prevCopyOpen],
        alerted: [...state.alerted].slice(-50),
        seenEventKeys: [...state.seenEventKeys].slice(-50),
        updatedAt: ts(),
        elapsedMin: Math.round((now - state.startedAt) / 60000),
      },
      null,
      2,
    ),
    'utf8',
  );
}

function writeFinalSummary() {
  const elapsedMin = Math.round((Date.now() - state.startedAt) / 60000);
  const actionMissEntryN =
    state.missEntries.length + state.actionMissEntryEvents.length;
  const actionMissExitN = state.missExits.length + state.orphansSeen.length;
  const overall =
    state.bitfinexLiveOn
      ? 'NOT_OK_BITFINEX_LIVE_ON'
      : actionMissEntryN > 0 || actionMissExitN > 0
        ? 'DEGRADED_ACTION_MISS'
        : state.forceCloses.length
          ? 'DEGRADED_FORCE_CLOSE_USED'
          : state.alerts.some((a) => /tick stalled|unreachable|lastError/i.test(a.msg))
            ? 'DEGRADED_ALERTS'
            : 'ACTION_MATCH_OK';

  const summary = {
    overall,
    scoring: 'ACTION_MATCH (not fill quality)',
    elapsedMin,
    polls: state.polls,
    entries: state.entries,
    exits: state.exits,
    entryCount: state.entries.length,
    exitCount: state.exits.length,
    actionMissEntry: state.missEntries,
    actionMissExit: state.missExits,
    actionMissEntryEvents: state.actionMissEntryEvents,
    actionMissExitEvents: state.actionMissExitEvents,
    orphansSeen: state.orphansSeen,
    missEntries: state.missEntries,
    missExits: state.missExits,
    pendingLeaks: state.pendingLeaks,
    showcasePositionAbsentFired: state.showcasePositionAbsentFired,
    showcasePositionAbsentEvents: state.showcasePositionAbsentEvents,
    forceCloses: state.forceCloses,
    bitfinexLiveOn: state.bitfinexLiveOn,
    tunnelDownCount: state.tunnelDownCount,
    lifecycleKeywords: state.lifecycleKeywords,
    alertCount: state.alerts.length,
    alerts: state.alerts.slice(-40),
    lastStatus: state.lastStatus,
    finishedAt: ts(),
  };
  writeFileSync(SUMMARY, JSON.stringify(summary, null, 2), 'utf8');
  log('=== MONITOR SUMMARY (ACTION MATCH) ===');
  log(`overall=${overall} elapsed=${elapsedMin}m polls=${state.polls}`);
  log(`entries=${state.entries.length} exits=${state.exits.length}`);
  log(
    `ACTION_MISS_ENTRY=${actionMissEntryN} ACTION_MISS_EXIT=${actionMissExitN} orphansSeen=${state.orphansSeen.length}`,
  );
  log(`SHOWCASE_POSITION_ABSENT fired=${state.showcasePositionAbsentFired}`);
  log(`forceCloses=${state.forceCloses.length} alerts=${state.alerts.length} bfxLiveOn=${state.bitfinexLiveOn}`);
  log(`lifecycle keywords: ${JSON.stringify(state.lifecycleKeywords)}`);
  return summary;
}

async function main() {
  if (existsSync(LIFECYCLE_LOG)) {
    state.lifecycleLogOffset = readFileSync(LIFECYCLE_LOG, 'utf8').length;
  }
  log('=== live-copy trade monitor START ===');
  log(
    `poll=${POLL_MS / 1000}s duration=${DURATION_MS / 3600000}h instance=${INSTANCE_ID} forceOrphan=${FORCE_ORPHAN_CLOSE} orphanForceSec=${ORPHAN_FORCE_SEC}`,
  );
  process.env.DATABASE_URL = loadDbUrl();
  prisma = new PrismaClient({ log: ['error'] });
  await prisma.$connect();
  log('Neon connected; lifecycle watcher expected at logs/live-copy-lifecycle-watch.log');

  await tick();

  const endAt = Date.now() + DURATION_MS;
  while (Date.now() < endAt) {
    if (existsSync(STOP)) {
      try {
        unlinkSync(STOP);
      } catch {
        /* ignore */
      }
      log('stop file -> exit early');
      break;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
    try {
      await tick();
    } catch (e) {
      log(`tick err: ${e.message}`, { alert: true });
    }
  }

  const summary = writeFinalSummary();
  await prisma.$disconnect();
  log('=== monitor STOP ===');
  console.log(JSON.stringify({ overall: summary.overall, polls: summary.polls, alerts: summary.alertCount }, null, 2));
}

main().catch((e) => {
  log(`fatal: ${e.message}`, { alert: true });
  process.exit(1);
});
