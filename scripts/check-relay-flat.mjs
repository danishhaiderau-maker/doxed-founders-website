#!/usr/bin/env node
/**
 * Read-only flat-boundary check for the showcase-to-Bitfinex relay.
 * Uses the executor's fresh raw Bitfinex reconciliation and does not print credentials.
 */
import fs from 'node:fs';
import https from 'node:https';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import prismaPackage from '../node_modules/.prisma/client/default.js';
import { getVaultDir } from './secrets-vault-path.mjs';
import { resolveHomeBotPublicUrl } from './home-bot-config.mjs';

for (const envFile of [
  path.join(getVaultDir(), '.env.neon'),
  path.join(getVaultDir(), 'home-bot.env'),
]) {
  if (!fs.existsSync(envFile)) continue;
  for (const raw of fs.readFileSync(envFile, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const splitAt = line.indexOf('=');
    if (splitAt < 1) continue;
    const key = line.slice(0, splitAt).trim();
    let value = line.slice(splitAt + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

const { PrismaClient } = prismaPackage;
const prisma = new PrismaClient();
const dedicatedAdminToken = process.env.BOT_ADMIN_TOKEN?.trim() || '';
const adminToken =
  dedicatedAdminToken
  || process.env.BOT_CONTROL_SECRET?.trim()
  || '';
const CANONICAL_FLY_OWNER_URL = 'https://doxed-btc-bot.fly.dev';
const requireCanonicalFlyOwner =
  process.env.REQUIRE_CANONICAL_FLY_OWNER === 'YES';
const durableOnlyRecovery =
  process.env.DURABLE_RELAYS_ONLY_RECOVERY === 'YES';
const platformApiUrl = process.env.PLATFORM_API_URL?.trim() || '';
if (durableOnlyRecovery && requireCanonicalFlyOwner) {
  throw new Error(
    'DURABLE_RELAYS_ONLY_RECOVERY cannot be combined with REQUIRE_CANONICAL_FLY_OWNER=YES',
  );
}
const ownerFetchTimeoutMs = Math.max(
  1_000,
  Number.parseInt(process.env.OWNER_STATE_TIMEOUT_MS ?? '15000', 10) || 15_000,
);
const ownerFetchAttempts = Math.max(
  1,
  Math.min(
    5,
    Number.parseInt(process.env.OWNER_STATE_FETCH_ATTEMPTS ?? '3', 10) || 3,
  ),
);
const prismaProofAttempts = Math.max(
  1,
  Math.min(
    5,
    Number.parseInt(process.env.PRISMA_PROOF_ATTEMPTS ?? '3', 10) || 3,
  ),
);
const botUrls = requireCanonicalFlyOwner
  ? [CANONICAL_FLY_OWNER_URL]
  : [
      // The deployed Fly owner is authoritative and must be attempted before
      // any workstation-era URL retained in an old vault.
      CANONICAL_FLY_OWNER_URL,
      process.env.SHOWCASE_OWNER_URL?.trim(),
      process.env.TRADING_AGENT_BOT_URL?.trim(),
      resolveHomeBotPublicUrl(),
      // Local is diagnostic-only. It is never considered by production
      // pre-deploy checks, which set REQUIRE_CANONICAL_FLY_OWNER=YES.
      'http://10.0.0.102:7002',
    ].filter(Boolean);

export function hasFullOwnerOrderState(bot) {
  return (
    bot != null
    && typeof bot === 'object'
    && (
      Array.isArray(bot.orders)
      || Array.isArray(bot.pending_orders)
    )
  );
}

export function hasCurrentOwnerExposureState(bot) {
  return (
    bot != null
    && typeof bot === 'object'
    && Array.isArray(bot.orders)
    && Array.isArray(bot.positions)
  );
}

export function ownerFetchErrorChain(error) {
  const parts = [];
  const seen = new Set();
  let current = error;
  while (current != null && !seen.has(current) && parts.length < 5) {
    seen.add(current);
    const name = String(current?.name ?? 'Error');
    const code = String(current?.code ?? '').trim();
    const message = String(current?.message ?? current ?? 'unknown error');
    parts.push(`${name}${code ? ` [${code}]` : ''}: ${message}`);
    current = current?.cause;
  }
  return parts.join(' <- ');
}

export function describeOwnerFetchError(error, url, timeoutMs, attempts = 1) {
  const detail = ownerFetchErrorChain(error);
  const attemptText = attempts > 1 ? ` after ${attempts} attempts` : '';
  if (
    /TimeoutError|AbortError|timed?\s*out|aborted due to timeout|UND_ERR_CONNECT_TIMEOUT/i.test(detail)
  ) {
    return new Error(
      `canonical owner state timed out after ${timeoutMs}ms per attempt${attemptText} at ${url}; `
      + `root cause: ${detail}; check Fly machine health, whether a critical service check removed public routing, and /health`,
      { cause: error },
    );
  }
  const diagnosis = /ENOTFOUND|EAI_AGAIN/i.test(detail)
    ? 'DNS resolution failed; check the Fly hostname and local resolver'
    : /ECONNREFUSED/i.test(detail)
      ? 'the public route refused the connection; check Fly machine/service binding'
      : /ECONNRESET|UND_ERR_SOCKET|socket/i.test(detail)
        ? 'the route/socket reset; check Fly logs and retry after confirming /health'
        : 'check Fly /health, machine status, and public routing';
  return new Error(
    `canonical owner state request failed${attemptText} at ${url}; `
    + `root cause: ${detail}; ${diagnosis}`,
    { cause: error },
  );
}

export function buildOwnerHttpsRequestOptions(url, token, timeoutMs) {
  const parsed = new URL(url);
  return {
    protocol: parsed.protocol,
    hostname: parsed.hostname,
    port: parsed.port || 443,
    path: `${parsed.pathname}${parsed.search}`,
    method: 'GET',
    family: 4,
    timeout: timeoutMs,
    headers: token ? { 'X-Bot-Admin-Token': token } : undefined,
  };
}

export function fetchOwnerJsonViaHttps(url, token = '', timeoutMs = 15_000) {
  return new Promise((resolve, reject) => {
    const request = https.request(
      buildOwnerHttpsRequestOptions(url, token, timeoutMs),
      (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => {
          const body = Buffer.concat(chunks).toString('utf8');
          if ((response.statusCode ?? 500) < 200 || (response.statusCode ?? 500) >= 300) {
            reject(new Error(`HTTPS HTTP ${response.statusCode ?? 'unknown'}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(new Error('HTTPS owner state returned invalid JSON', { cause: error }));
          }
        });
      },
    );
    request.on('timeout', () => {
      request.destroy(new Error(`HTTPS owner state timed out after ${timeoutMs}ms`));
    });
    request.on('error', reject);
    request.end();
  });
}

async function fetchOwnerJson(url) {
  let lastError = null;
  for (let attempt = 1; attempt <= ownerFetchAttempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: adminToken
          ? { 'X-Bot-Admin-Token': adminToken }
          : undefined,
        signal: AbortSignal.timeout(ownerFetchTimeoutMs),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      lastError = error;
      // Node's global fetch uses undici. On this Windows monitor host it can
      // intermittently exhaust its dual-stack connect attempt while the same
      // canonical Fly route remains healthy. Fall back to a fresh native HTTPS
      // request pinned to IPv4; preserve the same authentication and fail-closed
      // response checks instead of weakening the money-path boundary proof.
      try {
        return await fetchOwnerJsonViaHttps(url, adminToken, ownerFetchTimeoutMs);
      } catch (httpsError) {
        lastError = new Error('fetch and HTTPS fallback both failed', {
          cause: new AggregateError([error, httpsError]),
        });
      }
      if (attempt < ownerFetchAttempts) {
        await new Promise((resolve) => setTimeout(resolve, attempt * 350));
      }
    }
  }
  throw describeOwnerFetchError(
    lastError,
    url,
    ownerFetchTimeoutMs,
    ownerFetchAttempts,
  );
}

async function fetchOwnerState() {
  if (
    process.env.REQUIRE_BOT_ADMIN_TOKEN === 'YES'
    && !dedicatedAdminToken
  ) {
    throw new Error('BOT_ADMIN_TOKEN is required for an authenticated owner-state flat proof');
  }
  let lastError = null;
  for (const baseUrl of [...new Set(botUrls)]) {
    // This is a money-path deployment gate.  It must read the same bounded,
    // authenticated execution authority used by the relay, never the heavy
    // presentation snapshot (or the legacy relay-state cache) which can lag
    // a fill / handoff and falsely look flat.
    const stateUrl = `${baseUrl}/api/relay-execution-state`;
    try {
      const bot = await fetchOwnerJson(stateUrl);
      if (bot?.dashboard_owner === true) {
        if (
          requireCanonicalFlyOwner
          && baseUrl.replace(/\/$/, '') !== CANONICAL_FLY_OWNER_URL
        ) {
          throw new Error(`non-canonical owner refused: ${baseUrl}`);
        }
        if (
          process.env.REQUIRE_BOT_ADMIN_TOKEN === 'YES'
          && !hasFullOwnerOrderState(bot)
        ) {
          throw new Error(
            `${baseUrl} did not return the authenticated owner order state`,
          );
        }
        if (!hasCurrentOwnerExposureState(bot)) {
          throw new Error(
            `${baseUrl} execution snapshot omitted current orders or positions`,
          );
        }
        return { bot: { ...bot, flat_state_source: 'authenticated_execution_snapshot' }, baseUrl };
      }
      lastError = new Error(`${baseUrl} is not the dashboard owner`);
    } catch (error) {
      lastError = error instanceof Error
        && error.message.startsWith('canonical owner state')
        ? error
        : describeOwnerFetchError(error, stateUrl, ownerFetchTimeoutMs);
    }
  }
  throw lastError ?? new Error('showcase owner unavailable');
}

export function isStrictRawFlatReconcileSnapshot(rec, nowMs = Date.now()) {
  if (rec == null || typeof rec !== 'object') return false;
  for (const key of [
    'rawExchangePositionQty',
    'dustPositionQty',
    'signedExchangePositionQty',
    'ledgerOpenQty',
    'signedLedgerOpenQty',
    'deltaBtc',
    'openLots',
    'pendingLots',
  ]) {
    if (!Object.prototype.hasOwnProperty.call(rec, key)) return false;
    if (typeof rec[key] !== 'number' || !Number.isFinite(rec[key])) return false;
  }

  const reconcileAgeMs = nowMs - Date.parse(String(rec.updatedAt ?? ''));
  return (
    rec.rawExchangePositionQty === 0
    && rec.dustPositionQty === 0
    && rec.signedExchangePositionQty === 0
    && rec.ledgerOpenQty === 0
    && rec.signedLedgerOpenQty === 0
    && rec.deltaBtc === 0
    && rec.openLots === 0
    && rec.pendingLots === 0
    && Number.isFinite(reconcileAgeMs)
    && reconcileAgeMs >= 0
    && reconcileAgeMs <= 60_000
  );
}

export function isCompleteStoredRawFlatReconcileSnapshot(rec) {
  if (rec == null || typeof rec !== 'object') return false;
  for (const key of [
    'rawExchangePositionQty', 'dustPositionQty', 'signedExchangePositionQty',
    'ledgerOpenQty', 'signedLedgerOpenQty', 'deltaBtc', 'openLots', 'pendingLots',
  ]) {
    if (!Object.prototype.hasOwnProperty.call(rec, key)) return false;
    if (typeof rec[key] !== 'number' || !Number.isFinite(rec[key]) || rec[key] !== 0) return false;
  }
  return Number.isFinite(Date.parse(String(rec.updatedAt ?? '')));
}

export function isStrictExchangeOrderAuditFlat(audit, nowMs = Date.now()) {
  if (audit == null || typeof audit !== 'object') return false;
  const checkedAgeMs = nowMs - Date.parse(String(audit.checkedAt ?? ''));
  return (
    audit.known === true
    && audit.activeOrderCount === 0
    && audit.managedActiveOrderCount === 0
    && audit.foreignActiveOrderCount === 0
    && Number.isFinite(checkedAgeMs)
    && checkedAgeMs >= 0
    && checkedAgeMs <= 60_000
  );
}

export function isRelayPausedAndDisarmed(row) {
  return (
    row?.status === 'PAUSED'
    && (row.relayExecutionMode == null || row.relayExecutionMode === 'PAUSED')
    && row.relayArmedAt == null
    && row.realTradingConfirmedAt == null
  );
}

export function isCompleteStoredExchangeOrderAuditFlat(audit) {
  return (
    audit != null && typeof audit === 'object' && audit.known === true
    && audit.activeOrderCount === 0 && audit.managedActiveOrderCount === 0
    && audit.foreignActiveOrderCount === 0
    && Number.isFinite(Date.parse(String(audit.checkedAt ?? '')))
  );
}

export async function refreshPausedRelayAudit(
  apiUrl,
  adminSecret,
  userId,
  fetchImpl = fetch,
) {
  const base = String(apiUrl ?? '').trim().replace(/\/$/, '');
  const token = String(adminSecret ?? '').trim();
  const scopedUserId = String(userId ?? '').trim();
  if (!base || !token || !scopedUserId) {
    throw new Error('strict relay proof requires authenticated user-scoped audit refresh configuration');
  }
  const parsed = new URL(base);
  if (parsed.protocol !== 'https:') {
    throw new Error('strict relay proof requires an HTTPS platform API URL');
  }
  const response = await fetchImpl(
    `${base}/trading-agents/conservative-btc/ops/refresh-flat-audit`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-bot-admin-token': token,
    },
    body: JSON.stringify({
      userId: scopedUserId,
      confirmation: 'REFRESH_PAUSED_FLAT_AUDIT',
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    throw new Error(`authenticated platform audit refresh failed HTTP ${response.status}`);
  }
}

export function isRetryablePrismaConnectionError(error) {
  const message = String(error?.message ?? error ?? '');
  return /P1001|P1002|P1017|Can't reach database server|Server has closed the connection/i.test(message);
}

async function loadRelayBoundaryRows() {
  let lastError = null;
  for (let attempt = 1; attempt <= prismaProofAttempts; attempt += 1) {
    try {
      const agent = await prisma.tradingAgent.findUnique({
        where: { slug: 'conservative-btc' },
        select: { id: true },
      });
      if (!agent) throw new Error('conservative-btc agent missing');

      const instances = await prisma.tradingAgentInstance.findMany({
        where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
        include: {
          user: { select: { platformHandle: true, name: true } },
        },
      });

      const rows = [];
      for (const instance of instances) {
        const dashboard = instance.dashboardState ?? {};
        const activeParticipants = await prisma.signalCycleParticipant.count({
          where: {
            userId: instance.userId,
            cycle: { agentId: agent.id },
            status: { in: ['PENDING_ENTRY', 'OPEN'] },
          },
        });
        const reconcile =
          dashboard.copyRelayReconcile
          ?? dashboard.copyRelaySim?.reconcile
          ?? null;
        rows.push({
          instanceId: instance.id,
          refreshUserId: instance.userId,
          user:
            instance.user.platformHandle
            || instance.user.name
            || instance.userId,
          status: instance.status,
          lastError: instance.lastError,
          activeParticipants,
          reconcile,
          relayExecutionMode: dashboard.relayExecutionMode ?? null,
          relayArmedAt: dashboard.relayArmedAt ?? null,
          realTradingConfirmedAt: dashboard.realTradingConfirmedAt ?? null,
          exchangeOrderAudit: dashboard.exchangeOrderAudit ?? null,
          orphanOrderIds: dashboard.orphanOrderIds ?? [],
          orphanPositionIds: dashboard.orphanPositionIds ?? [],
        });
      }
      return rows;
    } catch (error) {
      lastError = error;
      if (!isRetryablePrismaConnectionError(error) || attempt >= prismaProofAttempts) {
        throw error;
      }
      await prisma.$disconnect().catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, attempt * 1_000));
    }
  }
  throw lastError ?? new Error('Neon boundary proof unavailable');
}

async function main() {
  const ownerState = durableOnlyRecovery ? null : await fetchOwnerState();
  const bot = ownerState?.bot ?? null;
  const botUrl = ownerState?.baseUrl ?? null;
  const pendingOrders = (bot?.orders ?? bot?.pending_orders ?? []).filter(
    (order) =>
      order
      && !['FILLED', 'CANCELLED', 'CANCELED', 'EXPIRED', 'REJECTED'].includes(
        String(order.status ?? '').toUpperCase(),
      ),
  );

  let rows = await loadRelayBoundaryRows();
  if (!durableOnlyRecovery) {
    const refreshTargets = rows.filter(isRelayPausedAndDisarmed);
    if (refreshTargets.length === 0) {
      throw new Error('strict relay proof found no paused, disarmed Cheetah audit target');
    }
    for (const target of refreshTargets) {
      await refreshPausedRelayAudit(platformApiUrl, dedicatedAdminToken, target.refreshUserId);
    }
  }
  for (let attempt = 1; attempt <= (durableOnlyRecovery ? 1 : 10); attempt += 1) {
    rows = await loadRelayBoundaryRows();
    if (
      durableOnlyRecovery
      || (
        rows.length > 0
        && rows.every((row) => (
          isRelayPausedAndDisarmed(row)
          && isStrictRawFlatReconcileSnapshot(row.reconcile)
          && isStrictExchangeOrderAuditFlat(row.exchangeOrderAudit)
        ))
      )
    ) break;
    if (attempt < 10) await new Promise((resolve) => setTimeout(resolve, 3_000));
  }

  const output = {
    at: new Date().toISOString(),
    showcase: {
      proofMode: durableOnlyRecovery ? 'DURABLE_RECOVERY_ONLY' : 'AUTHENTICATED_OWNER',
      botVersion: bot?.bot_version ?? null,
      botInstanceId: bot?.bot_instance_id ?? null,
      url: botUrl,
      dashboardOwner: bot == null ? null : bot.dashboard_owner === true,
      positions: Array.isArray(bot?.positions) ? bot.positions.length : null,
      pendingOrders: bot == null ? null : pendingOrders.length,
    },
    instances: rows.map(({ refreshUserId: _refreshUserId, ...row }) => row),
  };
  console.log(JSON.stringify(output, null, 2));

  const showcaseFlat = durableOnlyRecovery || (
    output.showcase.positions === 0 && output.showcase.pendingOrders === 0
  );
  const trackedFlat = rows.every((row) => row.activeParticipants === 0);
  const relayPausedAndDisarmed = rows.length > 0
    && rows.every(isRelayPausedAndDisarmed);
  const reconciledFlat = rows.length > 0
    && rows.every((row) => {
      return (
        (durableOnlyRecovery
          ? isCompleteStoredRawFlatReconcileSnapshot(row.reconcile)
          : isStrictRawFlatReconcileSnapshot(row.reconcile))
        && (durableOnlyRecovery
          ? isCompleteStoredExchangeOrderAuditFlat(row.exchangeOrderAudit)
          : isStrictExchangeOrderAuditFlat(row.exchangeOrderAudit))
        && row.orphanOrderIds.length === 0
        && row.orphanPositionIds.length === 0
      );
    });
  process.exitCode =
    showcaseFlat && trackedFlat && relayPausedAndDisarmed && reconciledFlat ? 0 : 2;
}

const isDirectRun =
  process.argv[1] != null
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectRun) {
  main()
    .catch((error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    })
    .finally(async () => {
      await prisma.$disconnect();
    });
}
