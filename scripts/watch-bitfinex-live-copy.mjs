// Read-only Bitfinex Live Copy watcher.
// Safe: NO direct Bitfinex API calls (would collide nonces with the API's live order placement).
// Reads:
//   - showcase bot :7002 /api/state        (signals + trades + P&L the Live Copy mirrors)
//   - Neon DB via Prisma (read-only)       (live copy instance status, participant fills/P&L, events)
// Loop every POLL_MS. Log -> logs/live-copy-watch.log
// Stop: kill process, or create logs/.live-copy-watch.stop

import { PrismaClient } from '@prisma/client';
import { readFileSync, existsSync, unlinkSync, mkdirSync, appendFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, '..');
const LOG = resolve(REPO, 'logs', 'live-copy-watch.log');
const STOP = resolve(REPO, 'logs', '.live-copy-watch.stop');
const BOT_STATE = 'http://127.0.0.1:7002/api/state';
const POLL_MS = 45_000;
const VAULT_NEON = resolve(REPO, '..', 'doxedcryptofounder-secrets', 'vault', '.env.neon');

mkdirSync(dirname(LOG), { recursive: true });

function log(msg) {
  const line = `[${new Date().toLocaleTimeString('en-AU', { hour12: false })}] ${msg}`;
  console.log(line);
  try { appendFileSync(LOG, line + '\n', 'utf8'); } catch {}
}

function loadDbUrl() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  if (!existsSync(VAULT_NEON)) throw new Error('no DATABASE_URL and no vault/.env.neon');
  for (const raw of readFileSync(VAULT_NEON, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    if (k.trim() === 'DATABASE_URL') return rest.join('=').trim().replace(/^"|"$/g, '');
  }
  throw new Error('DATABASE_URL not found in vault/.env.neon');
}

let prisma;
async function fetchBot() {
  try {
    const r = await fetch(BOT_STATE, { signal: AbortSignal.timeout(8000) });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { log(`bot fetch fail: ${e.message}`); return null; }
}

async function readLiveCopy() {
  const out = { instance: null, participants: [], events: [], err: null };
  try {
    const inst = await prisma.tradingAgentInstance.findFirst({
      orderBy: { updatedAt: 'desc' },
      include: { agent: { select: { slug: true, name: true } } },
    });
    out.instance = inst ? {
      id: inst.id, agentSlug: inst.agent?.slug, status: inst.status,
      lastError: inst.lastError, activatedAt: inst.activatedAt, expiresAt: inst.expiresAt,
      provider: inst.exchangeProvider, dashboardState: inst.dashboardState,
    } : null;

    const cycles = await prisma.signalCycle.findMany({
      orderBy: { createdAt: 'desc' }, take: 8,
      include: { participants: true },
    });
    out.participants = cycles.map(c => {
      const p = c.participants[0] || null;
      return {
        tradeId: c.tradeId, cycleStatus: c.status, showcasePnl: c.showcasePnlUsd,
        exitReason: c.showcaseExitReason, createdAt: c.createdAt,
        pStatus: p?.status, fill: p?.fillPrice, exit: p?.exitPrice,
        pnl: p?.pnlUsd, settlement: p?.settlementStatus,
      };
    });

    out.events = await prisma.signalCycleEvent.findMany({
      orderBy: { createdAt: 'desc' }, take: 10,
      select: { eventType: true, createdAt: true, payload: true },
    });
  } catch (e) { out.err = e.message; }
  return out;
}

let prevInstStatus = null, prevPartKeys = new Set(), prevEventKeys = new Set();

async function tick() {
  const bot = await fetchBot();
  const lc = await readLiveCopy();

  if (bot) {
    const sigs = bot.signals || bot.active_signals || [];
    const pos = bot.positions || [];
    log(`BOT  eq=$${bot.equity} pnl=$${bot.session_pnl_usd} trades=${bot.trade_count} pos=${pos.length} sigs=${sigs.length} live_en=${bot.bitfinex_live_enabled} armed=${bot.live_armed}`);
  } else {
    log('BOT  DOWN/empty');
  }

  const inst = lc.instance;
  if (inst) {
    const ch = prevInstStatus !== null && prevInstStatus !== inst.status ? ` !! STATUS ${prevInstStatus}->${inst.status}` : '';
    log(`LIVE instance=${inst.agentSlug} status=${inst.status}${ch} provider=${inst.provider}${inst.lastError ? ` err=${inst.lastError}` : ''}${inst.expiresAt ? ` expires=${new Date(inst.expiresAt).toISOString().slice(0,16)}` : ''}`);
    prevInstStatus = inst.status;
  } else {
    log('LIVE no TradingAgentInstance found (Live Copy never set up, or DB unreadable)');
  }

  if (lc.err) log(`LIVE db err: ${lc.err}`);

  let openCount = 0;
  for (const p of lc.participants) {
    const key = `${p.tradeId}|${p.pStatus}`;
    const isNew = !prevPartKeys.has(key);
    const open = p.pStatus && !['CLOSED', 'CLOSED_LOSS', 'CLOSED_WIN', 'EXITED', 'FAILED', 'REJECTED'].includes(p.pStatus);
    if (open) openCount++;
    if (isNew) {
      log(`     CYCLE ${p.tradeId} cycle=${p.cycleStatus} pStatus=${p.pStatus} fill=${p.fill} exit=${p.exit} pnl=${p.pnl} showPnl=${p.showcasePnl} settle=${p.settlement}${p.exitReason ? ` reason=${p.exitReason}` : ''}`);
    }
    prevPartKeys.add(key);
  }
  log(`LIVE open_participants=${openCount} recent_cycles=${lc.participants.length}`);

  for (const e of lc.events) {
    const key = `${e.eventType}|${new Date(e.createdAt).getTime()}`;
    if (!prevEventKeys.has(key)) {
      const pl = e.payload ? JSON.stringify(e.payload).slice(0, 120) : '';
      log(`     EVT ${e.eventType} ${pl}`);
    }
    prevEventKeys.add(key);
  }

  if (inst && inst.status === 'ACTIVE' && openCount === 0 && bot && (bot.signals || []).length === 0) {
    log(`OK   Live Copy ACTIVE, no open positions, no pending signals (idle, healthy)`);
  }
}

async function main() {
  log('=== Bitfinex Live Copy watcher START (read-only, Neon+bot) ===');
  process.env.DATABASE_URL = loadDbUrl();
  prisma = new PrismaClient({ log: ['error'] });
  await prisma.$connect();
  log('Neon connected. polling every 45s.');
  while (true) {
    if (existsSync(STOP)) { try { unlinkSync(STOP); } catch {} log('stop file -> exit'); break; }
    try { await tick(); } catch (e) { log(`tick err: ${e.message}`); }
    await new Promise(r => setTimeout(r, POLL_MS));
  }
  await prisma.$disconnect();
  log('=== watcher STOP ===');
}

main().catch(e => { log(`fatal: ${e.message}`); process.exit(1); });
