/**
 * One-shot monitor: signal cycles, hire instances, recent execution events.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const neonPath = path.join(getVaultDir(), '.env.neon');

if (fs.existsSync(neonPath)) {
  for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const key = trimmed.slice(0, idx).trim();
    let val = trimmed.slice(idx + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    process.env[key] = val;
  }
}

const prisma = new PrismaClient();

const BOT_URL = process.env.TRADING_AGENT_BOT_URL?.trim() || resolveHomeBotPublicUrl();

async function fetchBotSnapshot() {
  try {
    const res = await fetch(`${BOT_URL}/api/state`, { signal: AbortSignal.timeout(30_000) });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    const bot = await res.json();
    const lao = bot.last_approve_outcome ?? {};
    const signals = bot.signal_info?.signals ?? [];
    const positions = bot.positions ?? [];
    return {
      ok: true,
      price: bot.price,
      pullback_threshold: bot.pullback_threshold,
      lastApprove: lao,
      activeSignals: signals.filter((s) => ['ORDERED', 'ACTIVE', 'PENDING'].includes(s.status)).length,
      openPositions: positions.filter((p) => p.status === 'OPEN' || !p.status).length,
      regime: bot.regime,
    };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

function num(v) {
  if (v == null) return null;
  return typeof v === 'object' && typeof v.toNumber === 'function' ? v.toNumber() : Number(v);
}

async function main() {
  const now = new Date().toISOString();
  console.log(`\n=== Live copy watch @ ${now} ===\n`);

  const bot = await fetchBotSnapshot();
  console.log('Showcase bot:', JSON.stringify(bot, null, 2));

  const agent = await prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
  if (!agent) {
    console.error('Agent conservative-btc not found');
    process.exit(1);
  }

  const instances = await prisma.tradingAgentInstance.findMany({
    where: {
      agentId: agent.id,
      status: 'ACTIVE',
      exchangeProvider: { not: 'paper' },
    },
    include: { user: { select: { id: true, name: true, platformHandle: true } } },
    orderBy: { hiredAt: 'desc' },
    take: 10,
  });

  console.log('\nActive live hire instances:', instances.length);
  for (const inst of instances) {
    const label = inst.user?.platformHandle ?? inst.user?.name ?? inst.userId;
    console.log(
      `  - ${label} | ${inst.exchangeProvider} | expires=${inst.expiresAt?.toISOString() ?? 'never'} | lastError=${inst.lastError ?? 'none'}`,
    );
  }

  const openParticipants = await prisma.signalCycleParticipant.findMany({
    where: {
      user: { tradingAgentInstances: { some: { agentId: agent.id, status: 'ACTIVE' } } },
      status: { in: ['INTENT', 'PENDING_ENTRY', 'OPEN'] },
    },
    include: {
      cycle: true,
      user: { select: { platformHandle: true, name: true } },
      events: { orderBy: { createdAt: 'asc' } },
    },
    orderBy: { updatedAt: 'desc' },
    take: 5,
  });

  console.log('\nOpen hire participants (any cycle):');
  if (!openParticipants.length) console.log('  (none)');
  for (const p of openParticipants) {
    const who = p.user?.platformHandle ?? p.user?.name ?? p.userId;
    console.log(
      `  ${who} | cycle=${p.cycleId} trade=${p.cycle.tradeId.slice(0, 12)}… | ${p.status} fill=${num(p.fillPrice)}`,
    );
    for (const e of p.events) {
      const payload = e.payload ?? {};
      console.log(
        `    ${e.createdAt.toISOString()} ${e.eventType} ${JSON.stringify(payload).slice(0, 200)}`,
      );
    }
  }

  const cycles = await prisma.signalCycle.findMany({
    where: { agentId: agent.id },
    orderBy: { createdAt: 'desc' },
    take: 8,
    include: {
      participants: {
        include: {
          user: { select: { name: true, platformHandle: true } },
          events: { orderBy: { createdAt: 'desc' }, take: 6 },
        },
      },
    },
  });

  console.log('\nRecent signal cycles:');
  for (const c of cycles) {
    const env = c.intentEnvelope;
    const dir = env?.direction ?? '?';
    const offset = env?.entry?.offset_pct;
    console.log(
      `\n  ${c.id} | trade=${c.tradeId.slice(0, 8)}… | ${c.status} | ${dir} offset=${offset}% | created=${c.createdAt.toISOString()}`,
    );
    for (const p of c.participants) {
      const who = p.user?.platformHandle ?? p.user?.name ?? p.userId;
      console.log(
        `    participant ${who}: ${p.status} fill=${num(p.fillPrice)} pnl=$${num(p.pnlUsd)} venue=${p.venue}`,
      );
      for (const e of p.events.reverse()) {
        const payload = e.payload ?? {};
        const extra = [
          payload.limit_price != null ? `limit=${payload.limit_price}` : null,
          payload.fill_price != null ? `fill=${payload.fill_price}` : null,
          payload.stop_price != null ? `stop=${payload.stop_price}` : null,
          payload.event != null ? String(payload.event) : null,
          payload.exit_reason != null ? String(payload.exit_reason) : null,
          payload.peak_margin_pct != null ? `peak=${payload.peak_margin_pct}%` : null,
          payload.lock_floor_margin_pct != null ? `lock=${payload.lock_floor_margin_pct}%` : null,
        ]
          .filter(Boolean)
          .join(' ');
        console.log(`      ${e.createdAt.toISOString()} ${e.eventType} ${extra}`);
      }
    }
    if (!c.participants.length) console.log('    (no hire participants yet)');
  }

  const openCycles = cycles.filter((c) =>
    ['INTENT', 'PENDING_ENTRY', 'OPEN'].includes(c.status),
  );
  console.log(`\nOpen showcase cycles: ${openCycles.length}`);
  if (bot.ok && bot.lastApprove?.trade_id) {
    const bridged = cycles.some((c) => c.tradeId === bot.lastApprove.trade_id);
    console.log(
      `Bridge check: last approve ${bot.lastApprove.trade_id.slice(0, 8)}… status=${bot.lastApprove.status} → cycle ${bridged ? 'EXISTS' : 'MISSING'}`,
    );
  }

  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
