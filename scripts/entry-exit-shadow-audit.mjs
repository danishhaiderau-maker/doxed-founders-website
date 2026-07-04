/**
 * Focused entry/exit ACTION audit: showcase :7002 vs Live Copy hire.
 * Scores action fidelity (not fill price). Logs JSONL lines + console alerts.
 *
 * Stop: touch logs/.entry-exit-shadow-audit.stop
 */
import { PrismaClient } from '@prisma/client';
import {
  readFileSync,
  existsSync,
  appendFileSync,
  mkdirSync,
  unlinkSync,
} from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { getVaultDir } from './secrets-vault-path.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, '..');
const LOG = resolve(REPO, 'logs', 'entry-exit-shadow-audit.jsonl');
const STOP = resolve(REPO, 'logs', '.entry-exit-shadow-audit.stop');
const LOCAL_BOT = (process.env.SHOWCASE_BOT_LOCAL_URL || 'http://127.0.0.1:7002').replace(/\/$/, '');
const INSTANCE_ID = process.env.LIVE_COPY_INSTANCE_ID || 'cmq6cfwv4001jli0dqx5r31ve';
const POLL_MS = Number(process.env.AUDIT_POLL_MS || 75_000);
const DURATION_MS = Number(process.env.AUDIT_DURATION_MS || 3 * 60 * 60 * 1000);
const MISS_ENTRY_SEC = Number(process.env.AUDIT_MISS_ENTRY_SEC || 180);
const MISS_EXIT_SEC = Number(process.env.AUDIT_MISS_EXIT_SEC || 120);

const OPEN_PART = new Set(['INTENT', 'PENDING_ENTRY', 'OPEN']);
const CLOSED_PART = new Set(['CLOSED', 'CLOSED_LOSS', 'CLOSED_WIN', 'EXITED', 'FAILED', 'REJECTED']);

mkdirSync(dirname(LOG), { recursive: true });

function ts() {
  return new Date().toISOString();
}

function write(rec) {
  const line = JSON.stringify({ at: ts(), ...rec });
  appendFileSync(LOG, line + '\n', 'utf8');
  if (rec.alert) console.log(`[ALERT ${ts()}] ${rec.kind}: ${rec.msg}`);
  else console.log(`[${ts()}] ${rec.kind} showOpen=${rec.showOpen ?? '?'} copyOpen=${rec.copyOpen ?? '?'} matched=${rec.matched ?? '?'}`);
}

function loadDbUrl() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const vault = resolve(getVaultDir(), '.env.neon');
  for (const raw of readFileSync(vault, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    if (k.trim() === 'DATABASE_URL') return rest.join('=').trim().replace(/^"|"$/g, '');
  }
  throw new Error('DATABASE_URL not found');
}

const prisma = new PrismaClient({ datasources: { db: { url: loadDbUrl() } } });

const state = {
  polls: 0,
  alerted: new Set(),
  missEntrySince: new Map(),
  missExitSince: new Map(),
  prevShowOpen: new Set(),
  prevCopyOpen: new Set(),
};

function alertOnce(key, rec) {
  if (state.alerted.has(key)) return;
  state.alerted.add(key);
  write({ ...rec, alert: true });
}

async function fetchShowcase() {
  const r = await fetch(`${LOCAL_BOT}/api/state`, { signal: AbortSignal.timeout(12_000) });
  if (!r.ok) throw new Error(`showcase HTTP ${r.status}`);
  const s = await r.json();
  const positions = (s.positions || []).map((p) => ({
    tradeId: p.trade_id,
    dir: p.dir || p.side,
    entry: p.entry,
    status: p.status || 'OPEN',
  }));
  const pending = (s.pending_orders || s.pending || []).map((p) => ({
    tradeId: p.trade_id,
    status: 'PENDING',
  }));
  return { price: s.price, positions, pending, v2Shadow: s.v2_shadow_only ?? s.a160_v2?.shadow_only ?? null };
}

async function fetchCopy() {
  const inst = await prisma.tradingAgentInstance.findUnique({
    where: { id: INSTANCE_ID },
    select: {
      status: true,
      userId: true,
      agentId: true,
      dashboardState: true,
    },
  });
  if (!inst) throw new Error(`instance ${INSTANCE_ID} not found`);
  const dash = inst.dashboardState && typeof inst.dashboardState === 'object' ? inst.dashboardState : {};
  const participants = await prisma.signalCycleParticipant.findMany({
    where: {
      userId: inst.userId,
      cycle: { agentId: inst.agentId },
      status: { in: [...OPEN_PART] },
    },
    include: {
      cycle: { select: { tradeId: true, status: true, showcaseExitReason: true } },
    },
    orderBy: { updatedAt: 'desc' },
    take: 30,
  });
  const open = participants
    .filter((p) => OPEN_PART.has(String(p.status || '').toUpperCase()))
    .map((p) => ({
      tradeId: p.cycle?.tradeId,
      status: p.status,
      cycleStatus: p.cycle?.status,
      fillPrice: p.fillPrice != null ? Number(p.fillPrice) : null,
      exitReason: p.cycle?.showcaseExitReason,
      updatedAt: p.updatedAt,
    }))
    .filter((p) => p.tradeId);
  const pending = participants
    .filter((p) => String(p.status || '').toUpperCase() === 'PENDING_ENTRY')
    .map((p) => ({ tradeId: p.cycle?.tradeId, status: p.status }))
    .filter((p) => p.tradeId);
  return {
    inst: { ...inst, bitfinexLiveEnabled: dash.bitfinexLiveEnabled === true },
    dash,
    open,
    pending,
  };
}

function matchOpen(showOpen, copyOpen, copyPending) {
  const copyIds = new Set([
    ...copyOpen.map((p) => p.tradeId),
    ...copyPending.map((p) => p.tradeId),
  ]);
  let matched = 0;
  const missEntry = [];
  for (const s of showOpen) {
    if (copyIds.has(s.tradeId)) matched++;
    else missEntry.push(s.tradeId);
  }
  const missExit = copyOpen.filter((c) => !showOpen.some((s) => s.tradeId === c.tradeId)).map((c) => c.tradeId);
  return { matched, missEntry, missExit, copyIds };
}

async function tick() {
  state.polls += 1;
  let showcase;
  try {
    showcase = await fetchShowcase();
  } catch (e) {
    alertOnce('bot-down', { kind: 'BOT_DOWN', msg: String(e.message || e), poll: state.polls });
    return;
  }
  const copy = await fetchCopy();
  const dash = copy.dash || {};
  const showOpen = showcase.positions;
  const { matched, missEntry, missExit } = matchOpen(showOpen, copy.open, copy.pending);

  const now = Date.now();
  for (const tid of missEntry) {
    if (!state.missEntrySince.has(tid)) state.missEntrySince.set(tid, now);
    const age = (now - state.missEntrySince.get(tid)) / 1000;
    if (age >= MISS_ENTRY_SEC) {
      alertOnce(`miss-entry:${tid}`, {
        kind: 'ACTION_MISS_ENTRY',
        msg: `showcase OPEN ${tid.slice(0, 14)} copy flat >${Math.round(age)}s`,
        tradeId: tid,
        ageSec: Math.round(age),
        poll: state.polls,
      });
    }
  }
  for (const tid of [...state.missEntrySince.keys()]) {
    if (!missEntry.includes(tid)) state.missEntrySince.delete(tid);
  }

  for (const tid of missExit) {
    if (!state.missExitSince.has(tid)) state.missExitSince.set(tid, now);
    const age = (now - state.missExitSince.get(tid)) / 1000;
    if (age >= MISS_EXIT_SEC) {
      alertOnce(`miss-exit:${tid}`, {
        kind: 'ACTION_MISS_EXIT',
        msg: `showcase flat ${tid.slice(0, 14)} copy still OPEN >${Math.round(age)}s`,
        tradeId: tid,
        ageSec: Math.round(age),
        poll: state.polls,
      });
    }
  }
  for (const tid of [...state.missExitSince.keys()]) {
    if (!missExit.includes(tid)) state.missExitSince.delete(tid);
  }

  const newShow = showOpen.filter((p) => !state.prevShowOpen.has(p.tradeId));
  const newCopy = copy.open.filter((p) => !state.prevCopyOpen.has(p.tradeId));
  for (const p of newShow) {
    write({ kind: 'SHOWCASE_ENTRY', tradeId: p.tradeId, dir: p.dir, entry: p.entry, poll: state.polls });
  }
  for (const p of newCopy) {
    write({ kind: 'COPY_ENTRY', tradeId: p.tradeId, fill: p.fillPrice, poll: state.polls });
  }

  state.prevShowOpen = new Set(showOpen.map((p) => p.tradeId));
  state.prevCopyOpen = new Set(copy.open.map((p) => p.tradeId));

  write({
    kind: 'POLL',
    poll: state.polls,
    mark: showcase.price,
    showOpen: showOpen.length,
    copyOpen: copy.open.length,
    copyPending: copy.pending.length,
    matched,
    missEntry: missEntry.length,
    missExit: missExit.length,
    bfxLive: copy.inst.bitfinexLiveEnabled === true,
    lastTickSec: dash.lastTickAt ? Math.round((now - new Date(dash.lastTickAt).getTime()) / 1000) : null,
    v2Shadow: showcase.v2Shadow,
  });
}

const endAt = Date.now() + DURATION_MS;
write({ kind: 'START', pollMs: POLL_MS, durationMs: DURATION_MS, instanceId: INSTANCE_ID });

while (Date.now() < endAt) {
  if (existsSync(STOP)) {
    write({ kind: 'STOP', reason: 'stop file' });
    unlinkSync(STOP);
    break;
  }
  try {
    await tick();
  } catch (e) {
    write({ kind: 'ERROR', msg: String(e.message || e), alert: true });
  }
  await new Promise((r) => setTimeout(r, POLL_MS));
}

write({ kind: 'END', polls: state.polls });
await prisma.$disconnect();
