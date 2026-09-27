#!/usr/bin/env node
/**
 * Read-only flat-boundary check for the showcase-to-Bitfinex relay.
 * Uses the executor's fresh raw Bitfinex reconciliation and does not print credentials.
 */
import fs from 'node:fs';
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
const botUrls = requireCanonicalFlyOwner
  ? [CANONICAL_FLY_OWNER_URL]
  : [
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
      if (!response.ok) {
        const body = await response.text();
        const error = new Error(`HTTP ${response.status}`);
        error.status = response.status;
        error.body = body.slice(0, 2_000);
        throw error;
      }
      return await response.json();
    } catch (error) {
      lastError = error;
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

/** Exact env string only. Missing, false, and any other value stay fail-closed. */
export function paperTipExceptionEnabled(env = process.env) {
  return env?.PAPER_TIP_EXCEPTION === 'true';
}

/**
 * Exact env string only. One-shot Pathway Lab boot-loop tip. Default off.
 * Does not arm, and does not replace the post-deploy disarmed-paper proof.
 */
export function pathwayLabTipExceptionEnabled(env = process.env) {
  return env?.PATHWAY_LAB_TIP_EXCEPTION === 'true';
}

/** Short hex prefix of the live tip stuck in the v1_post_ai restart loop. */
export function pathwayLabFailingRev(env = process.env) {
  const rev = String(env?.PATHWAY_LAB_FAILING_REV ?? '').trim();
  return /^[0-9a-f]{7,40}$/.test(rev) ? rev : '';
}

const PATHWAY_BOOT_LOOP_ERRORS = new Set([
  'dashboard loading',
  'dashboard state is restoring',
]);

function pathwayRelayStatus(error) {
  for (const candidate of [error?.status, error?.cause?.status, error?.cause?.cause?.status]) {
    const status = Number(candidate);
    if (status === 502 || status === 503) return status;
  }
  const blob = [error?.message, error?.cause?.message, error?.cause?.cause?.message]
    .map((part) => String(part ?? ''))
    .join('\n');
  const found502 = /\bHTTP 502\b/.test(blob);
  const found503 = /\bHTTP 503\b/.test(blob);
  if (found502 && found503) return 0;
  if (found502) return 502;
  if (found503) return 503;
  return 0;
}

function pathwayRelayBodyText(error) {
  for (const candidate of [error?.body, error?.cause?.body, error?.cause?.cause?.body]) {
    if (typeof candidate === 'string') return candidate;
  }
  return '';
}

function isPathwayBootLoopJson(bodyText) {
  const text = String(bodyText ?? '').trim();
  if (!text) return false;
  let parsed = null;
  try {
    parsed = JSON.parse(text);
  } catch {
    return false;
  }
  if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) return false;
  if (parsed.boot !== 'starting') return false;
  return PATHWAY_BOOT_LOOP_ERRORS.has(String(parsed.error ?? ''));
}

/**
 * Pathway Lab boot-loop relay failure on /api/relay-execution-state.
 * App JSON is HTTP 503 with boot "starting" and error "dashboard loading"
 * or "dashboard state is restoring". The same flap also hits the Fly edge
 * as HTTP 502 or HTTP 503 with an empty body (no boot JSON to parse).
 * A non-empty 502, a non-boot 503 body, HTTP 200, and any other status
 * stay fail-closed.
 */
export function isPathwayLabBootLoop503(error) {
  const message = String(error?.message ?? '');
  if (!/\/api\/relay-execution-state/.test(message)) return false;
  const status = pathwayRelayStatus(error);
  const bodyText = pathwayRelayBodyText(error);
  if (status === 502) return bodyText.trim() === '';
  if (status === 503) {
    if (bodyText.trim() === '') return true;
    return isPathwayBootLoopJson(bodyText);
  }
  return false;
}

/** The SystemExit string from run_startup_pathway_validation. rc=1 is that raise. */
export function pathwayLabStartupFailProven(logText) {
  const text = String(logText ?? '');
  return text.includes('Pathway Lab startup validation FAILED')
    && text.includes('v1_post_ai=FAIL');
}

/**
 * Fly logs API rejected the read. HTTP 401, or a body that says unauthorized.
 * Other failures (missing token, 5xx, timeouts) stay fail-closed.
 */
export function flyLogsReadUnauthorized(error) {
  if (error == null) return false;
  if (typeof error !== 'object') {
    return /unauthorized/i.test(String(error));
  }
  const status = Number(error.status ?? error.cause?.status);
  if (status === 401) return true;
  const message = [
    error.message,
    error.body,
    error.cause?.message,
    error.cause?.body,
  ].map((part) => String(part ?? '')).join('\n');
  return /fly logs HTTP 401\b/.test(message) || /unauthorized/i.test(message);
}

const PATHWAY_FAIL_VERDICT_KEYS = ['v1_post_ai', 'independent_v1_post_ai_spawn'];
const PATHWAY_FAIL_VERDICT_RE = /(?:v1_post_ai|independent_v1_post_ai_spawn)\s*[:=]\s*"?(PASS|FAIL)"?/gi;

/**
 * v1_post_ai FAIL already carried by /health, the relay 503 body, or another
 * artifact the workflow fetched. Absent is not proof. PASS, or any other
 * explicit verdict, contradicts the startup FAIL.
 */
export function pathwayLabEncodedStartupFail(sources) {
  const verdicts = [];
  const seen = new Set();
  const record = (value) => {
    if (value === 'PASS' || value === 'FAIL') verdicts.push(value);
    else verdicts.push('OTHER');
  };
  const visit = (node, depth) => {
    if (node == null || depth > 6) return;
    if (typeof node === 'string') {
      if (pathwayLabStartupFailProven(node)) verdicts.push('FAIL');
      PATHWAY_FAIL_VERDICT_RE.lastIndex = 0;
      let match = PATHWAY_FAIL_VERDICT_RE.exec(node);
      while (match) {
        verdicts.push(String(match[1]).toUpperCase());
        match = PATHWAY_FAIL_VERDICT_RE.exec(node);
      }
      return;
    }
    if (typeof node !== 'object') return;
    if (seen.has(node)) return;
    seen.add(node);
    if (Array.isArray(node)) {
      for (const item of node) visit(item, depth + 1);
      return;
    }
    for (const key of PATHWAY_FAIL_VERDICT_KEYS) {
      if (Object.prototype.hasOwnProperty.call(node, key)) record(node[key]);
    }
    for (const value of Object.values(node)) {
      if (value && (typeof value === 'object' || typeof value === 'string')) {
        visit(value, depth + 1);
      }
    }
  };
  const list = Array.isArray(sources) ? sources : [sources];
  for (const source of list) visit(source, 0);
  if (verdicts.length === 0) return { present: false, fail: false };
  return { present: true, fail: verdicts.every((verdict) => verdict === 'FAIL') };
}

function pathwayLabFailSources({ health, relayError, failArtifacts }) {
  const sources = [];
  if (health != null) sources.push(health);
  const bodyText = relayError?.body ?? relayError?.cause?.body;
  if (typeof bodyText === 'string' && bodyText.trim()) {
    try {
      sources.push(JSON.parse(bodyText));
    } catch {
      sources.push(bodyText);
    }
  }
  if (Array.isArray(failArtifacts)) {
    for (const artifact of failArtifacts) sources.push(artifact);
  }
  return sources;
}

function pathwayRelayBookDisarmed(rows) {
  const trackedFlat = Array.isArray(rows)
    && rows.length > 0
    && rows.every((row) => row.activeParticipants === 0);
  const cheetahRows = (Array.isArray(rows) ? rows : [])
    .filter((row) => String(row?.user ?? '').toLowerCase().includes('cheetah'));
  const relayPausedAndDisarmed = cheetahRows.length > 0
    && cheetahRows.every(isRelayPausedAndDisarmed);
  const orphansClear = cheetahRows.every((row) => (
    Array.isArray(row?.orphanOrderIds)
    && row.orphanOrderIds.length === 0
    && Array.isArray(row?.orphanPositionIds)
    && row.orphanPositionIds.length === 0
  ));
  return trackedFlat && relayPausedAndDisarmed && orphansClear;
}

/**
 * Logged only after _apply_env_live_gating sets live_armed false under
 * FORCE_PAPER_MODE. Early-boot /health does not carry that latch.
 */
export function paperModeDisarmLogProven(logText) {
  return String(logText ?? '').includes('[PAPER MODE] FORCE_PAPER_MODE active');
}

export function healthRevisionMatches(health, failingRev) {
  const prefix = String(failingRev ?? '');
  if (!/^[0-9a-f]{7,40}$/.test(prefix)) return false;
  const rev = String(health?.source_git_rev || health?.git_rev || '');
  return rev.startsWith(prefix);
}

/**
 * Paper-disarmed proof for the flap.
 * Explicit /health flags must be live_armed false, force_paper true, and
 * Bitfinex live disabled. Early-boot /health omits those flags: that server
 * starts only after the Fly entrypoint has already refused a non-paper
 * process (REFUSED_DIRECT_FLY_LIVE / exit 78). Any explicit armed or live
 * flag fails closed.
 */
export function paperDisarmedHealth(health) {
  if (health == null || typeof health !== 'object') {
    return { ok: false, mode: 'missing' };
  }
  if (health.live_armed === true) return { ok: false, mode: 'live_armed' };
  if (health.bitfinex_live_enabled === true) return { ok: false, mode: 'bitfinex_live' };
  if (health.force_paper_mode === false) return { ok: false, mode: 'not_paper' };
  if (
    health.live_armed === false
    && health.force_paper_mode === true
    && health.bitfinex_live_enabled === false
  ) {
    return { ok: true, mode: 'explicit' };
  }
  if (
    health.boot === 'starting'
    && health.ok === true
    && health.dashboard_owner === true
    && health.live_armed == null
    && health.force_paper_mode == null
    && health.bitfinex_live_enabled == null
  ) {
    return { ok: true, mode: 'early_boot' };
  }
  return { ok: false, mode: 'unproven' };
}

export function collectFlyLogText(payload) {
  const parts = [];
  const visit = (node) => {
    if (typeof node === 'string') {
      parts.push(node);
      return;
    }
    if (Array.isArray(node)) {
      for (const item of node) visit(item);
      return;
    }
    if (node == null || typeof node !== 'object') return;
    for (const key of ['message', 'msg', 'log']) {
      if (typeof node[key] === 'string') parts.push(node[key]);
    }
    for (const value of Object.values(node)) {
      if (value && typeof value === 'object') visit(value);
    }
  };
  visit(payload);
  return parts.join('\n');
}

/**
 * One-shot chicken-egg gate. Exit 0 only when the relay failure is the
 * Pathway Lab boot loop (Fly-edge 502/503 with an empty body, or HTTP 503
 * boot JSON), the failing revision and paper-disarmed proofs hold, and
 * the DB relay book is still paused and disarmed.
 * Startup FAIL comes from Fly logs when that read succeeds. A 401 or
 * unauthorized logs read does not skip the gate: the same boot-loop,
 * failing-revision, paper-disarmed, and paused-relay proofs are required,
 * and any already-fetched v1_post_ai verdict must be FAIL. Missing token,
 * 5xx, and other log errors stay fail-closed. Showcase flatness is not
 * read: that snapshot is the 503 this exception exists for. Soft B /
 * recover, and any live arm flag, stay fail-closed.
 */
export function evaluatePathwayLabTipException({
  enabled = false,
  failingRev = '',
  relayError = null,
  health = null,
  logText = '',
  rows = [],
  recoverStalled = false,
  logsUnauthorized = false,
  failArtifacts = [],
}) {
  if (enabled !== true) {
    return { exitCode: 1, pass: false, reason: 'flag off' };
  }
  if (recoverStalled === true) {
    return { exitCode: 1, pass: false, reason: 'recover stalled path' };
  }
  if (!isPathwayLabBootLoop503(relayError)) {
    return { exitCode: 1, pass: false, reason: 'not the pathway boot-loop 503' };
  }
  if (!healthRevisionMatches(health, failingRev)) {
    return { exitCode: 1, pass: false, reason: 'failing revision proof missing' };
  }
  const paper = paperDisarmedHealth(health);
  if (!paper.ok) {
    return { exitCode: 1, pass: false, reason: `paper-disarmed proof missing (${paper.mode})` };
  }
  const encoded = pathwayLabEncodedStartupFail(pathwayLabFailSources({
    health,
    relayError,
    failArtifacts,
  }));
  if (encoded.present && !encoded.fail) {
    return { exitCode: 1, pass: false, reason: 'startup FAIL proof contradicted' };
  }
  const startupFromLogs = pathwayLabStartupFailProven(logText);
  let startupFromAlternate = false;
  if (!startupFromLogs) {
    if (logsUnauthorized !== true) {
      return { exitCode: 1, pass: false, reason: 'startup FAIL proof missing' };
    }
    startupFromAlternate = true;
  }
  if (
    paper.mode === 'early_boot'
    && !startupFromAlternate
    && !paperModeDisarmLogProven(logText)
  ) {
    return { exitCode: 1, pass: false, reason: 'paper disarm log missing' };
  }
  if (!pathwayRelayBookDisarmed(rows)) {
    return { exitCode: 2, pass: false, reason: 'relays are not paused and disarmed' };
  }
  if (startupFromAlternate) {
    return {
      exitCode: 0,
      pass: true,
      reason: encoded.fail
        ? 'pathway lab 401 alternate proof with encoded v1_post_ai=FAIL'
        : 'pathway lab 401 alternate proof',
    };
  }
  return { exitCode: 0, pass: true, reason: 'pathway lab boot-loop tip' };
}

const FLY_LOGS_URL = 'https://api.fly.io/api/v1/apps/doxed-btc-bot/logs';
const FLY_LOG_PAGE_CAP = 12;

async function fetchFlyPathwayLogText(env = process.env) {
  const token = String(env.FLY_API_TOKEN ?? '').trim();
  if (!token) {
    throw new Error('PATHWAY_LAB_TIP_EXCEPTION requires FLY_API_TOKEN to prove the startup FAIL');
  }
  const authorization = token.toLowerCase().startsWith('bearer ')
    ? token
    : `Bearer ${token}`;
  const chunks = [];
  const seenTokens = new Set();
  const readPage = async (nextToken) => {
    const url = new URL(FLY_LOGS_URL);
    if (nextToken) url.searchParams.set('next_token', nextToken);
    const response = await fetch(url, {
      headers: {
        Authorization: authorization,
        Accept: 'application/json',
      },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      const body = await response.text();
      const error = new Error(`fly logs HTTP ${response.status}`);
      error.status = response.status;
      error.body = body.slice(0, 500);
      throw error;
    }
    return response.json();
  };
  const absorb = (payload) => {
    const text = collectFlyLogText(payload);
    if (text) chunks.push(text);
    return payload?.meta?.next_token ?? payload?.meta?.nextToken ?? null;
  };
  const ready = (text) => pathwayLabStartupFailProven(text) && paperModeDisarmLogProven(text);
  let cursor = absorb(await readPage(''));
  if (ready(chunks.join('\n'))) return chunks.join('\n');
  cursor = `${BigInt(Date.now() - (60 * 60 * 1000)) * 1_000_000n}`;
  for (let page = 0; page < FLY_LOG_PAGE_CAP; page += 1) {
    if (!cursor || seenTokens.has(String(cursor))) break;
    seenTokens.add(String(cursor));
    const next = absorb(await readPage(String(cursor)));
    if (ready(chunks.join('\n'))) return chunks.join('\n');
    if (!next || String(next) === String(cursor)) break;
    cursor = String(next);
  }
  return chunks.join('\n');
}

async function fetchCanonicalHealth() {
  const url = `${CANONICAL_FLY_OWNER_URL}/health`;
  const response = await fetch(url, {
    headers: adminToken
      ? { 'X-Bot-Admin-Token': adminToken }
      : undefined,
    signal: AbortSignal.timeout(ownerFetchTimeoutMs),
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`canonical health HTTP ${response.status}`);
  return JSON.parse(body);
}

function orphanIdsClear(row) {
  return Array.isArray(row?.orphanOrderIds)
    && row.orphanOrderIds.length === 0
    && Array.isArray(row?.orphanPositionIds)
    && row.orphanPositionIds.length === 0;
}

function cheetahProofOk(row, paperTipException, nowMs) {
  const nullCheetahProofOk = paperTipException === true;
  const reconcileOk = isStrictRawFlatReconcileSnapshot(row.reconcile, nowMs)
    || (nullCheetahProofOk && row.reconcile == null);
  const auditOk = isStrictExchangeOrderAuditFlat(row.exchangeOrderAudit, nowMs)
    || (nullCheetahProofOk && row.exchangeOrderAudit == null);
  return reconcileOk && auditOk && orphanIdsClear(row);
}

/**
 * Money-path flat gate. paperTipException is the one-shot exit-75 paper tip:
 * a null Cheetah reconcile or exchangeOrderAudit does not fail when showcase
 * is flat and relays are paused/disarmed. Default false keeps the ≤60s proof.
 */
export function evaluateRelayFlatGate({
  showcasePositions,
  showcasePendingOrders,
  rows,
  paperTipException = false,
  nowMs = Date.now(),
}) {
  const showcaseFlat = showcasePositions === 0 && showcasePendingOrders === 0;
  const trackedFlat = Array.isArray(rows)
    && rows.every((row) => row.activeParticipants === 0);
  const cheetahRows = (Array.isArray(rows) ? rows : [])
    .filter((row) => String(row?.user ?? '').toLowerCase().includes('cheetah'));
  const relayPausedAndDisarmed = cheetahRows.length > 0
    && cheetahRows.every(isRelayPausedAndDisarmed);
  const cheetahProofFlat = cheetahRows.length > 0
    && cheetahRows.every((row) => cheetahProofOk(row, paperTipException, nowMs));
  return showcaseFlat && trackedFlat && relayPausedAndDisarmed && cheetahProofFlat
    ? 0
    : 2;
}

async function loadBitfinexRelayRows() {
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
}

async function main() {
  let owner = null;
  let ownerFetchError = null;
  try {
    owner = await fetchOwnerState();
  } catch (error) {
    ownerFetchError = error;
  }

  if (ownerFetchError) {
    if (!pathwayLabTipExceptionEnabled()) throw ownerFetchError;
    let health = null;
    let logText = '';
    let logsUnauthorized = false;
    try {
      health = await fetchCanonicalHealth();
    } catch (error) {
      console.error(
        `PATHWAY_LAB_TIP_EXCEPTION refused: ${error instanceof Error ? error.message : error}`,
      );
      console.error(ownerFetchError instanceof Error ? ownerFetchError.message : ownerFetchError);
      process.exitCode = 1;
      return;
    }
    try {
      logText = await fetchFlyPathwayLogText();
    } catch (error) {
      if (!flyLogsReadUnauthorized(error)) {
        console.error(
          `PATHWAY_LAB_TIP_EXCEPTION refused: ${error instanceof Error ? error.message : error}`,
        );
        console.error(ownerFetchError instanceof Error ? ownerFetchError.message : ownerFetchError);
        process.exitCode = 1;
        return;
      }
      logsUnauthorized = true;
      console.error(
        `PATHWAY_LAB_TIP_EXCEPTION: Fly logs read unauthorized (${error instanceof Error ? error.message : error}). `
        + 'The log line is not proof. Requiring the boot-loop, failing-revision, paper-disarmed, and paused-relay alternate proof.',
      );
    }
    const rows = await loadBitfinexRelayRows();
    const decision = evaluatePathwayLabTipException({
      enabled: true,
      failingRev: pathwayLabFailingRev(),
      relayError: ownerFetchError,
      health,
      logText,
      rows,
      recoverStalled: process.env.RECOVER_STALLED_PAPER_BOUNDARY === 'true',
      logsUnauthorized,
    });
    if (!decision.pass) {
      console.error(`PATHWAY_LAB_TIP_EXCEPTION refused: ${decision.reason}`);
      console.error(ownerFetchError instanceof Error ? ownerFetchError.message : ownerFetchError);
      process.exitCode = decision.exitCode;
      return;
    }
    console.log(JSON.stringify({
      at: new Date().toISOString(),
      pathway_lab_tip_exception: true,
      logs_unauthorized: logsUnauthorized,
      startup_fail_proof: decision.reason,
      showcase: {
        skipped: true,
        reason: 'relay-execution-state pathway boot loop',
      },
      health: {
        source_git_rev: health?.source_git_rev ?? health?.git_rev ?? null,
        live_armed: health?.live_armed ?? null,
        force_paper_mode: health?.force_paper_mode ?? null,
        bitfinex_live_enabled: health?.bitfinex_live_enabled ?? null,
        boot: health?.boot ?? null,
      },
      instances: rows,
    }, null, 2));
    const alternateProof = decision.reason.startsWith('pathway lab 401 alternate proof');
    console.error(
      alternateProof
        ? 'PATHWAY_LAB_TIP_EXCEPTION: Fly logs returned 401/unauthorized, so the startup FAIL log line was not read. '
          + 'Alternate proof passed: relay-execution-state is the Pathway Lab boot loop '
          + '(Fly-edge HTTP 502 or HTTP 503 with an empty body, or HTTP 503 JSON boot: starting '
          + 'and dashboard loading/restoring), /health revision matches the failing rev, '
          + 'paper-disarmed (live_armed is not true), and Bitfinex relays are paused and disarmed'
          + (decision.reason.includes('encoded')
            ? ', with an encoded v1_post_ai=FAIL signal. '
            : '. ')
          + 'Showcase snapshot was not read. Does not arm. Soft B, Force, wipe, and live arm stay no-go.'
        : 'PATHWAY_LAB_TIP_EXCEPTION: one-shot Pathway Lab v1_post_ai=FAIL boot-loop tip. '
          + 'Canonical relay-execution-state boot loop on the failing revision was accepted '
          + 'because startup FAIL and paper-disarmed proofs passed and relays are paused/disarmed. '
          + 'Showcase snapshot was not read. Does not arm. Soft B, Force, wipe, and live arm stay no-go.',
    );
    process.exitCode = 0;
    return;
  }

  const { bot, baseUrl: botUrl } = owner;
  const pendingOrders = (bot.orders ?? bot.pending_orders ?? []).filter(
    (order) =>
      order
      && !['FILLED', 'CANCELLED', 'CANCELED', 'EXPIRED', 'REJECTED'].includes(
        String(order.status ?? '').toUpperCase(),
      ),
  );
  const rows = await loadBitfinexRelayRows();

  const output = {
    at: new Date().toISOString(),
    showcase: {
      botVersion: bot.bot_version ?? null,
      botInstanceId: bot.bot_instance_id ?? null,
      url: botUrl,
      dashboardOwner: bot.dashboard_owner === true,
      positions: Array.isArray(bot.positions) ? bot.positions.length : null,
      pendingOrders: pendingOrders.length,
    },
    instances: rows,
  };
  console.log(JSON.stringify(output, null, 2));

  const paperTipException = paperTipExceptionEnabled();
  const gateInput = {
    showcasePositions: output.showcase.positions,
    showcasePendingOrders: output.showcase.pendingOrders,
    rows,
    nowMs: Date.now(),
  };
  const exitCode = evaluateRelayFlatGate({ ...gateInput, paperTipException });
  if (
    paperTipException
    && exitCode === 0
    && evaluateRelayFlatGate({ ...gateInput, paperTipException: false }) !== 0
  ) {
    console.error(
      'PAPER_TIP_EXCEPTION: one-shot exit-75 paper tip. Showcase flat and relays paused/disarmed; null Cheetah reconcile/exchangeOrderAudit accepted. Does not arm.',
    );
  }
  process.exitCode = exitCode;
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
