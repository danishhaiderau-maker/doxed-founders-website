/**
 * Export all Bitfinex live-copy trades from Neon DB to exports/.
 *
 * Usage:
 *   node scripts/export-bitfinex-live-trades.mjs
 *   node scripts/export-bitfinex-live-trades.mjs --userId=<cuid>
 *   node scripts/export-bitfinex-live-trades.mjs --slug=conservative-btc --format=json
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';
import { getVaultDir, repoRoot } from './secrets-vault-path.mjs';

const root = repoRoot;
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

function parseArgs() {
  const out = {
    slug: 'conservative-btc',
    userId: process.env.EXPORT_USER_ID ?? null,
    format: 'csv',
  };
  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith('--slug=')) out.slug = arg.slice(7);
    else if (arg.startsWith('--userId=')) out.userId = arg.slice(9);
    else if (arg.startsWith('--format=')) out.format = arg.slice(9) === 'json' ? 'json' : 'csv';
  }
  return out;
}

function num(v) {
  if (v == null) return null;
  return typeof v === 'object' && typeof v.toNumber === 'function' ? v.toNumber() : Number(v);
}

function csvEscape(value) {
  if (value == null) return '';
  const s = String(value);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function mapRow(p) {
  const intent = (p.cycle.intentEnvelope && typeof p.cycle.intentEnvelope === 'object'
    ? p.cycle.intentEnvelope
    : {}) ;
  const events = p.events
    .slice()
    .sort((a, b) => a.createdAt.getTime() - b.createdAt.getTime())
    .map((e) => {
      const pl = e.payload ?? {};
      return {
        timestamp: e.createdAt.toISOString(),
        eventType: e.eventType,
        limitPrice: num(pl.limit_price),
        fillPrice: num(pl.fill_price),
        exitPrice: num(pl.exit_price),
        exitReason: typeof pl.exit_reason === 'string' ? pl.exit_reason : null,
        pnlUsd: num(pl.pnl_usd),
        pnlMarginPct: num(pl.pnl_margin_pct),
        bitfinexOrderId:
          pl.bitfinex_order_id != null
            ? String(pl.bitfinex_order_id)
            : pl.order_id != null
              ? String(pl.order_id)
              : null,
        detail: typeof pl.event === 'string' ? pl.event : null,
      };
    });

  const filledEvent = events.find((e) => e.eventType === 'FILLED');
  const exitEvent = [...events].reverse().find((e) => e.eventType === 'EXIT');
  const orderEvent = events.find((e) => e.eventType === 'ORDER_PLACED');
  const orderPayload = orderEvent
    ? (p.events.find((e) => e.eventType === 'ORDER_PLACED')?.payload ?? {})
    : {};

  const fillPrice = num(p.fillPrice) ?? filledEvent?.fillPrice ?? null;
  const exitPrice = num(p.exitPrice) ?? exitEvent?.exitPrice ?? null;
  const pnlUsd = num(p.pnlUsd) ?? exitEvent?.pnlUsd ?? null;
  const pnlMarginPct = num(p.pnlMarginPct) ?? exitEvent?.pnlMarginPct ?? null;
  const filledAt = filledEvent?.timestamp ?? null;
  const closedAt =
    p.status === 'CLOSED' || p.status === 'EXPIRED'
      ? exitEvent?.timestamp ?? p.updatedAt.toISOString()
      : null;
  const startMs = filledAt ? Date.parse(filledAt) : p.createdAt.getTime();
  const endMs = closedAt ? Date.parse(closedAt) : Date.now();
  const durationMinutes =
    closedAt || p.status === 'OPEN'
      ? Math.max(0, Math.round((endMs - startMs) / 60_000))
      : null;

  return {
    tradeId: p.cycle.tradeId,
    cycleId: p.cycle.id,
    participantId: p.id,
    status: p.status,
    direction: String(intent.direction ?? '—').toUpperCase(),
    venue: p.venue,
    symbol: 'tBTCF0:USTF0',
    leverage: num(intent.risk?.leverage_hint) ?? 100,
    marginUsd: num(intent.risk?.max_margin_usd) ?? num(orderPayload.margin_usd),
    qtyBtc: num(orderPayload.qty) ?? num(orderPayload.qty_btc),
    limitPrice: num(intent.limit_price) ?? orderEvent?.limitPrice ?? null,
    signalPrice: num(intent.signal_price) ?? null,
    fillPrice,
    exitPrice,
    pnlUsd,
    pnlMarginPct,
    feeUsd: num(p.feeUsd),
    exitReason: exitEvent?.exitReason ?? p.cycle.showcaseExitReason,
    signalCreatedAt: p.cycle.createdAt.toISOString(),
    participantCreatedAt: p.createdAt.toISOString(),
    filledAt,
    closedAt,
    durationMinutes,
    bitfinexOrderId:
      orderEvent?.bitfinexOrderId ?? events.find((e) => e.bitfinexOrderId)?.bitfinexOrderId ?? null,
    regime: intent.regime ?? null,
    strategy: intent.strategy ?? null,
    showcaseExitReason: p.cycle.showcaseExitReason,
    eventsSummary: events
      .map((e) => `${e.timestamp}|${e.eventType}|${e.detail ?? ''}`)
      .join('; '),
    events,
  };
}

const CSV_COLUMNS = [
  'tradeId',
  'cycleId',
  'participantId',
  'status',
  'direction',
  'venue',
  'symbol',
  'leverage',
  'marginUsd',
  'qtyBtc',
  'limitPrice',
  'signalPrice',
  'fillPrice',
  'exitPrice',
  'pnlUsd',
  'pnlMarginPct',
  'feeUsd',
  'exitReason',
  'signalCreatedAt',
  'participantCreatedAt',
  'filledAt',
  'closedAt',
  'durationMinutes',
  'bitfinexOrderId',
  'regime',
  'strategy',
  'showcaseExitReason',
  'eventsSummary',
];

function toCsv(rows) {
  const header = CSV_COLUMNS.join(',');
  const lines = rows.map((row) => CSV_COLUMNS.map((col) => csvEscape(row[col])).join(','));
  return [header, ...lines].join('\n');
}

async function exportForUser(agent, userId, format) {
  const participants = await prisma.signalCycleParticipant.findMany({
    where: {
      userId,
      cycle: { agentId: agent.id },
    },
    include: {
      cycle: {
        select: {
          id: true,
          tradeId: true,
          status: true,
          intentEnvelope: true,
          showcaseExitReason: true,
          createdAt: true,
          closedAt: true,
        },
      },
      events: {
        orderBy: { createdAt: 'asc' },
        select: { eventType: true, payload: true, createdAt: true },
      },
    },
    orderBy: { createdAt: 'desc' },
  });

  const rows = participants.map(mapRow);
  const closedRows = rows.filter((r) => r.status === 'CLOSED' || r.status === 'EXPIRED');
  const totalPnlUsd = closedRows.reduce((sum, r) => sum + (r.pnlUsd ?? 0), 0);

  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: { platformHandle: true, name: true, email: true },
  });

  const payload = {
    exportedAt: new Date().toISOString(),
    agentSlug: agent.slug,
    agentName: agent.name,
    exchange: 'bitfinex',
    userId,
    userLabel: user?.platformHandle ?? user?.name ?? user?.email ?? userId,
    tradeCount: rows.length,
    closedCount: closedRows.length,
    totalPnlUsd: Number(totalPnlUsd.toFixed(4)),
    rows,
  };

  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const label = String(user?.platformHandle ?? user?.name ?? userId.slice(0, 8))
    .replace(/[^a-zA-Z0-9_-]/g, '_')
    .replace(/_undefined$/i, '');
  const ext = format === 'json' ? 'json' : 'csv';
  const filename = `bitfinex-live-trades-${agent.slug}-${label}-${stamp}.${ext}`;
  const outDir = path.join(root, 'exports');
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, filename);

  if (format === 'json') {
    fs.writeFileSync(outPath, JSON.stringify(payload, null, 2), 'utf8');
  } else {
    fs.writeFileSync(outPath, toCsv(rows), 'utf8');
    fs.writeFileSync(
      path.join(outDir, filename.replace('.csv', '.json')),
      JSON.stringify(payload, null, 2),
      'utf8',
    );
  }

  console.log(`Wrote ${outPath} (${rows.length} trades, closed=${closedRows.length}, PnL=$${totalPnlUsd.toFixed(2)})`);
  return outPath;
}

async function main() {
  const { slug, userId, format } = parseArgs();

  const agent = await prisma.tradingAgent.findUnique({ where: { slug } });
  if (!agent) {
    console.error(`Agent not found: ${slug}`);
    process.exit(1);
  }

  const userIds = userId
    ? [userId]
    : (
        await prisma.tradingAgentInstance.findMany({
          where: {
            agentId: agent.id,
            exchangeProvider: 'bitfinex',
          },
          select: { userId: true },
          distinct: ['userId'],
        })
      ).map((r) => r.userId);

  if (!userIds.length) {
    console.log('No Bitfinex live hire instances found.');
    await prisma.$disconnect();
    return;
  }

  console.log(`Exporting ${userIds.length} user(s) for agent ${slug}…`);
  for (const uid of userIds) {
    await exportForUser(agent, uid, format);
  }

  await prisma.$disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
