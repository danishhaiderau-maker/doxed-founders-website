#!/usr/bin/env node
/**
 * Read-only Bitfinex cost-basis diagnostic.
 *
 * Pulls authenticated exchange trades, orders, position and margin-ledger rows,
 * then maps exchange order ids back to showcase participant events. Credentials
 * are decrypted in memory and are never printed.
 */
import { createDecipheriv, createHash, createHmac } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { PrismaClient } from '@prisma/client';
import { getVaultDir } from './secrets-vault-path.mjs';

const SYMBOL = 'tBTCF0:USTF0';
const prisma = new PrismaClient();

function loadEnvFile(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

loadEnvFile(path.join(getVaultDir(), '.env.neon'));
loadEnvFile(path.join(getVaultDir(), '.env.vercel.check'));

function decryptWith(secret, iv, tag, data) {
  const key = createHash('sha256').update(secret).digest();
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(data), decipher.final()]).toString('utf8');
}

function decryptCredential(payload) {
  const [ivB64, tagB64, dataB64] = String(payload ?? '').split(':');
  if (!ivB64 || !tagB64 || !dataB64) throw new Error('Invalid credential payload');
  const iv = Buffer.from(ivB64, 'base64');
  const tag = Buffer.from(tagB64, 'base64');
  const data = Buffer.from(dataB64, 'base64');
  const candidates = [
    process.env.CREDENTIAL_ENCRYPTION_KEY?.trim(),
    process.env.JWT_SECRET?.trim(),
  ].filter(Boolean);
  for (const secret of [...new Set(candidates)]) {
    try {
      return JSON.parse(decryptWith(secret, iv, tag, data));
    } catch {
      // Try legacy key.
    }
  }
  throw new Error('Credential decryption failed');
}

let lastNonce = 0n;
async function authPost(creds, apiPath, body = {}) {
  const now = BigInt(Date.now()) * 1000n;
  lastNonce = lastNonce >= now ? lastNonce + 1n : now;
  const nonce = lastNonce.toString();
  const bodyText = JSON.stringify(body);
  const signature = createHmac('sha384', creds.apiSecret)
    .update(`/api/${apiPath}${nonce}${bodyText}`)
    .digest('hex');
  const response = await fetch(`https://api.bitfinex.com/${apiPath}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'bfx-apikey': creds.apiKey,
      'bfx-nonce': nonce,
      'bfx-signature': signature,
    },
    body: bodyText,
    signal: AbortSignal.timeout(20_000),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`Bitfinex ${apiPath}: ${JSON.stringify(parsed).slice(0, 300)}`);
  }
  return parsed;
}

function asPayload(value) {
  return value && typeof value === 'object' ? value : {};
}

function eventOrderIds(event) {
  const payload = asPayload(event.payload);
  return [
    payload.bitfinexOrderId,
    payload.bitfinex_order_id,
    payload.stopOrderId,
    payload.stop_order_id,
    payload.cancelledOrderId,
    payload.cancelled_order_id,
    payload.replacedOrderId,
    payload.replaced_order_id,
  ]
    .map(Number)
    .filter((id) => Number.isFinite(id) && id > 0);
}

function parseTrade(row, orderToTradeId) {
  if (!Array.isArray(row) || row.length < 11) return null;
  const orderId = Number(row[3] ?? 0);
  const amount = Number(row[4] ?? 0);
  const price = Number(row[5] ?? 0);
  if (!Number.isFinite(amount) || !Number.isFinite(price) || price <= 0) return null;
  return {
    exchangeTradeId: String(row[0]),
    orderId,
    tradeId: orderToTradeId.get(orderId) ?? null,
    at: new Date(Number(row[2] ?? 0)).toISOString(),
    amountBtc: amount,
    side: amount > 0 ? 'BUY' : 'SELL',
    price,
    orderType: String(row[6] ?? ''),
    orderPrice: Number(row[7] ?? 0),
    maker: Boolean(row[8]),
    feeUsd: Number(row[9] ?? 0),
    feeCurrency: String(row[10] ?? ''),
  };
}

function reconstructBasis(trades) {
  let position = 0;
  let average = 0;
  let realized = 0;
  const steps = [];
  for (const trade of trades) {
    const amount = trade.amountBtc;
    const price = trade.price;
    const sameDirection =
      position === 0 || Math.sign(position) === Math.sign(amount);
    if (sameDirection) {
      const nextPosition = position + amount;
      average =
        position === 0
          ? price
          : (Math.abs(position) * average + Math.abs(amount) * price) /
            Math.abs(nextPosition);
      position = nextPosition;
    } else {
      const closeQty = Math.min(Math.abs(position), Math.abs(amount));
      realized +=
        position > 0
          ? (price - average) * closeQty
          : (average - price) * closeQty;
      const nextPosition = position + amount;
      if (Math.abs(nextPosition) < 1e-10) {
        position = 0;
        average = 0;
      } else if (Math.sign(nextPosition) !== Math.sign(position)) {
        position = nextPosition;
        average = price;
      } else {
        position = nextPosition;
      }
    }
    steps.push({
      at: trade.at,
      tradeId: trade.tradeId,
      orderId: trade.orderId,
      fill: `${trade.side} ${Math.abs(trade.amountBtc).toFixed(5)} @ ${trade.price}`,
      positionBtc: Number(position.toFixed(8)),
      reconstructedAverage: Number(average.toFixed(4)),
      reconstructedRealizedUsd: Number(realized.toFixed(4)),
    });
  }
  return {
    positionBtc: Number(position.toFixed(8)),
    averagePrice: Number(average.toFixed(4)),
    realizedPnlUsd: Number(realized.toFixed(4)),
    steps,
  };
}

function parsePosition(rows) {
  if (!Array.isArray(rows)) return null;
  const row = rows.find((item) => Array.isArray(item) && String(item[0]) === SYMBOL);
  if (!row || Math.abs(Number(row[2] ?? 0)) < 1e-8) return null;
  return {
    symbol: String(row[0]),
    amountBtc: Number(row[2] ?? 0),
    basePrice: Number(row[3] ?? 0),
    unrealizedPnlUsd: Number(row[6] ?? 0),
    unrealizedPnlPct: Number(row[7] ?? 0),
  };
}

function parseLedgerRow(row) {
  if (!Array.isArray(row) || row.length < 8) return null;
  return {
    ledgerId: String(row[0] ?? ''),
    currency: String(row[1] ?? ''),
    at: new Date(Number(row[3] ?? 0)).toISOString(),
    amountUsd: Number(row[4] ?? 0),
    balanceUsd: Number(row[5] ?? 0),
    description: String(row[7] ?? ''),
  };
}

async function main() {
  const handle = String(process.argv[2] ?? 'Bitbro4crypto').trim().toLowerCase();
  const agent = await prisma.tradingAgent.findUnique({
    where: { slug: 'conservative-btc' },
    select: { id: true },
  });
  if (!agent) throw new Error('conservative-btc agent not found');
  const instances = await prisma.tradingAgentInstance.findMany({
    where: {
      agentId: agent.id,
      exchangeProvider: 'bitfinex',
      status: 'ACTIVE',
    },
    include: {
      user: { select: { platformHandle: true, name: true } },
    },
    orderBy: { updatedAt: 'desc' },
  });
  const instance =
    instances.find((row) =>
      [row.user?.platformHandle, row.user?.name]
        .filter(Boolean)
        .some((value) => String(value).trim().toLowerCase() === handle),
    ) ?? instances[0];
  if (!instance) throw new Error('No active Bitfinex live-copy instance found');

  const dashboard = asPayload(instance.dashboardState);
  const lastRelayTickMs = Date.parse(String(dashboard.lastTickAt ?? ''));
  if (
    Number.isFinite(lastRelayTickMs) &&
    Date.now() - lastRelayTickMs < 5 * 60_000
  ) {
    throw new Error(
      'Refusing direct Bitfinex API access while the production relay owns this API key. ' +
        'Use the production exchange snapshot/ledger path or stop the relay and wait for a safe flat boundary.',
    );
  }
  const sessionStartedAt = new Date(
    (typeof dashboard.sessionStartedAt === 'string' && dashboard.sessionStartedAt) ||
      instance.activatedAt?.toISOString() ||
      instance.hiredAt.toISOString(),
  );
  const credential = await prisma.integrationCredential.findUnique({
    where: {
      userId_provider: {
        userId: instance.userId,
        provider: 'exchange:bitfinex',
      },
    },
    select: { token: true },
  });
  if (!credential?.token) throw new Error('Bitfinex credential not connected');
  const creds = decryptCredential(credential.token);
  if (!creds.apiKey || !creds.apiSecret) throw new Error('Incomplete Bitfinex credential');

  const participants = await prisma.signalCycleParticipant.findMany({
    where: {
      userId: instance.userId,
      cycle: { agentId: agent.id },
      OR: [
        { createdAt: { gte: sessionStartedAt } },
        { updatedAt: { gte: sessionStartedAt } },
        { events: { some: { createdAt: { gte: sessionStartedAt } } } },
      ],
    },
    include: {
      cycle: { select: { tradeId: true } },
      events: { orderBy: { createdAt: 'asc' } },
    },
  });
  const orderToTradeId = new Map();
  for (const participant of participants) {
    for (const event of participant.events) {
      for (const orderId of eventOrderIds(event)) {
        orderToTradeId.set(orderId, participant.cycle.tradeId);
      }
    }
  }

  const range = {
    start: sessionStartedAt.getTime(),
    end: Date.now(),
    limit: 250,
    sort: 1,
  };
  const positionRows = await authPost(creds, 'v2/auth/r/positions');
  const activeOrders = await authPost(creds, 'v2/auth/r/orders', { sym: SYMBOL });
  const tradeRows = await authPost(
    creds,
    `v2/auth/r/trades/${SYMBOL}/hist`,
    range,
  );
  const orderRows = await authPost(
    creds,
    `v2/auth/r/orders/${SYMBOL}/hist`,
    range,
  );
  const ledgerRows = await authPost(creds, 'v2/auth/r/ledgers/hist', {
    wallet: 'margin',
    ...range,
  });

  const trades = (Array.isArray(tradeRows) ? tradeRows : [])
    .map((row) => parseTrade(row, orderToTradeId))
    .filter(Boolean)
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const basis = reconstructBasis(trades);
  const closeLedger = (Array.isArray(ledgerRows) ? ledgerRows : [])
    .map(parseLedgerRow)
    .filter(
      (row) =>
        row &&
        /position/i.test(row.description) &&
        !/fee|funding|transfer|commission/i.test(row.description),
    );
  const feeLedger = (Array.isArray(ledgerRows) ? ledgerRows : [])
    .map(parseLedgerRow)
    .filter(
      (row) =>
        row &&
        row.amountUsd < 0 &&
        /fee|funding|commission/i.test(row.description),
    );

  const participantRealizedUsd = participants
    .filter((row) => row.status === 'CLOSED')
    .reduce((sum, row) => sum + Number(row.pnlUsd ?? 0), 0);
  const exchangeRealizedUsd = closeLedger.reduce(
    (sum, row) => sum + row.amountUsd,
    0,
  );
  const exchangeFeesUsd = feeLedger.reduce(
    (sum, row) => sum + Math.abs(row.amountUsd),
    0,
  );
  const currentPosition = parsePosition(positionRows);

  const result = {
    at: new Date().toISOString(),
    account: instance.user?.platformHandle ?? instance.user?.name ?? 'active-user',
    sessionStartedAt: sessionStartedAt.toISOString(),
    currentPosition,
    activeOrderCount: Array.isArray(activeOrders) ? activeOrders.length : 0,
    exchangeTradeCount: trades.length,
    mappedExchangeTradeCount: trades.filter((trade) => trade.tradeId).length,
    exchangeOrderHistoryCount: Array.isArray(orderRows) ? orderRows.length : 0,
    pnlComparison: {
      participantRealizedUsd: Number(participantRealizedUsd.toFixed(4)),
      exchangePositionCloseLedgerUsd: Number(exchangeRealizedUsd.toFixed(4)),
      exchangeFeesUsd: Number(exchangeFeesUsd.toFixed(4)),
      exchangeUnrealizedUsd: Number((currentPosition?.unrealizedPnlUsd ?? 0).toFixed(4)),
      participantBasedSessionPnlUsd: Number(
        (
          participantRealizedUsd +
          (currentPosition?.unrealizedPnlUsd ?? 0) -
          exchangeFeesUsd
        ).toFixed(4),
      ),
      exchangeBasedSessionPnlUsd: Number(
        (
          exchangeRealizedUsd +
          (currentPosition?.unrealizedPnlUsd ?? 0) -
          exchangeFeesUsd
        ).toFixed(4),
      ),
    },
    reconstructedBasis: {
      positionBtc: basis.positionBtc,
      averagePrice: basis.averagePrice,
      realizedPnlUsd: basis.realizedPnlUsd,
    },
    trades,
    basisSteps: basis.steps,
    positionCloseLedger: closeLedger,
    feeLedger,
  };
  console.log(JSON.stringify(result, null, 2));
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
