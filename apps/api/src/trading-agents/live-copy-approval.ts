import { createHmac, timingSafeEqual } from 'node:crypto';

/**
 * Fly "live copy" approval verification (Option 1, 2026-10-09).
 *
 * Fly (paper signal source) stamps every copy intent it is allowed to emit
 * with a signed approval. The HMAC key is domain-separated from the shared
 * relay secret (SHOWCASE_WEBHOOK_SECRET) and covers `signed_body`, the exact
 * canonical JSON text of the approval. Fields are read ONLY from that signed
 * text, so encoder float/key formatting can never break or bypass the check.
 * Anything missing, unsigned, stale, or for another trade/event is refused.
 */
export const FLY_LIVE_COPY_APPROVAL_SCHEMA = 'fly_live_copy_approval_v1';
export const FLY_LIVE_COPY_APPROVAL_DOMAIN = 'fly-live-copy-approval-v1';
export const LIVE_EXECUTION_REPORT_DOMAIN = 'railway-live-execution-report-v1';
export const FLY_WEBSITE_STATE_DOMAIN = 'fly-website-state-v1';
/** An entry approval older than this is stale (intent must be fresh). */
export const FLY_APPROVAL_MAX_AGE_SEC = 120;
export const LIVE_COPY_ENTRY_EVENTS = new Set(['ORDER_PLACED', 'LIMIT_UPDATED']);
/** Executor-side bound: an entry is placed only from a recent Fly approval. */
export const FLY_APPROVAL_PLACEMENT_MAX_AGE_SEC = 180;

/**
 * Active Fly tiles (lane -> trade-id prefix). Mirrors Fly's
 * combo_pathway_config.ACTIVE_TILE_REGISTRY; a Fly test fails on drift.
 * Being listed here never makes a tile copyable: a copy also needs Fly's
 * signed approval (operator/registry eligible, output ON, tile switch ON).
 */
export const LIVE_COPY_TILE_PREFIXES: Readonly<Record<string, string>> = Object.freeze({
  FAMILY_COMMITTED_FADE_TAKER_90: 'cft',
  FAMILY_PREMIUM_REVERSION_60M: 'pmr',
  FAMILY_RANDOM_CONTROL_TAKER_90: 'rnd',
  FAMILY_GS01_XV_PREMIUM_ATR_TP: 'gs1',
  FAMILY_GSB1_CVD_DIV_REGIME: 'gb1',
  FAMILY_GSB2_REGIME_SWITCHER: 'gb2',
  FAMILY_GSB3_COMMITTED_FADE_REGIME: 'gb3',
  FAMILY_GS06_COMMITTED_FADE_ATR_TP: 'gs6',
  FAMILY_GS07_FAST_PREMIUM_FADE: 'gs7',
  FAMILY_DANISH_REGIME_ROUTER: 'dnr',
  FAMILY_FADE_POOL: 'fdp',
  FAMILY_GS07_V07_PREMIUM_FADE_60M: 'g7v',
});

/**
 * Bitfinex derivatives (BTCF0:USTF0) are isolated-margin: initial margin
 * 1/leverage, minimum maintenance margin 0.5%. A $0.25 / 100x ($25 notional)
 * position liquidates ~50 bp adverse (mark price; fees/funding ignored).
 * Boss 2026-10-09: the catastrophe backup stop sits >= 15 bp inside that.
 */
export const BITFINEX_DERIV_MAINTENANCE_MARGIN = 0.005;
export const LIQUIDATION_SAFETY_BP = 15;
export function liquidationDistanceBp(leverage: number, mm = BITFINEX_DERIV_MAINTENANCE_MARGIN): number {
  if (!(leverage > 0)) return 0;
  return Math.max(0, Math.round((1 / leverage - mm) * 1e4 * 1e4) / 1e4);
}
export function exchangeStopCapBp(leverage: number): number {
  return liquidationDistanceBp(leverage) - LIQUIDATION_SAFETY_BP;
}

export type FlyLiveCopyApproval = {
  schema: string;
  correlation_id: string;
  trade_id: string;
  event: string;
  research_lane: string;
  relay_eligible: boolean;
  eligibility_source?: string;
  entry_allowed: boolean;
  continuation?: boolean;
  output_on?: boolean;
  tile_live_on?: boolean;
  created_at_ts: number;
  signal_at_ts?: number | null;
  max_margin_usd: number;
  leverage: number;
  order_type: string;
  hard_stop_bp?: number | null;
  exchange_stop_bp?: number | null;
  [key: string]: unknown;
};

export type ApprovalVerdict =
  | { ok: true; approval: FlyLiveCopyApproval }
  | { ok: false; reason: string };

export function deriveLiveCopyKey(secret: string | undefined | null, domain: string): Buffer | null {
  const s = String(secret ?? '').trim();
  if (!s) return null;
  return createHmac('sha256', Buffer.from(s, 'utf8')).update(Buffer.from(domain, 'utf8')).digest();
}

function safeHexEqual(expectedHex: string, providedHex: string): boolean {
  if (!/^[0-9a-f]+$/i.test(providedHex) || providedHex.length !== expectedHex.length) return false;
  return timingSafeEqual(Buffer.from(expectedHex, 'hex'), Buffer.from(providedHex.toLowerCase(), 'hex'));
}

export function verifyFlyLiveCopyApproval(
  raw: unknown,
  secret: string | undefined | null,
  expect: { tradeId: string; event: string; nowMs?: number; maxAgeSec?: number },
): ApprovalVerdict {
  const key = deriveLiveCopyKey(secret, FLY_LIVE_COPY_APPROVAL_DOMAIN);
  if (!key) return { ok: false, reason: 'APPROVAL_SECRET_MISSING' };
  if (!raw || typeof raw !== 'object') return { ok: false, reason: 'APPROVAL_MISSING' };
  const obj = raw as Record<string, unknown>;
  const text = obj.signed_body;
  const sig = obj.signature;
  if (typeof text !== 'string' || !text || typeof sig !== 'string' || !sig) {
    return { ok: false, reason: 'APPROVAL_UNSIGNED' };
  }
  const expected = createHmac('sha256', key).update(Buffer.from(text, 'utf8')).digest('hex');
  if (!safeHexEqual(expected, sig)) return { ok: false, reason: 'APPROVAL_SIGNATURE_INVALID' };
  let a: FlyLiveCopyApproval;
  try {
    a = JSON.parse(text) as FlyLiveCopyApproval;
  } catch {
    return { ok: false, reason: 'APPROVAL_BODY_INVALID' };
  }
  if (!a || typeof a !== 'object' || a.schema !== FLY_LIVE_COPY_APPROVAL_SCHEMA) {
    return { ok: false, reason: 'APPROVAL_SCHEMA_INVALID' };
  }
  if (String(a.trade_id) !== String(expect.tradeId) || String(a.event) !== String(expect.event)) {
    return { ok: false, reason: 'APPROVAL_IDENTITY_MISMATCH' };
  }
  if (LIVE_COPY_ENTRY_EVENTS.has(String(expect.event))) {
    if (a.entry_allowed !== true) return { ok: false, reason: 'APPROVAL_ENTRY_NOT_ALLOWED' };
    if (a.relay_eligible !== true) return { ok: false, reason: 'APPROVAL_TILE_NOT_ELIGIBLE' };
    if (a.output_on !== true) return { ok: false, reason: 'APPROVAL_OUTPUT_OFF' };
    if (a.tile_live_on !== true) return { ok: false, reason: 'APPROVAL_TILE_LIVE_SWITCH_OFF' };
    if (String(a.order_type).toUpperCase() !== 'LIMIT') return { ok: false, reason: 'APPROVAL_NOT_LIMIT' };
    if (!(Number(a.max_margin_usd) > 0 && Number(a.max_margin_usd) <= 0.25) || Number(a.leverage) !== 100) {
      return { ok: false, reason: 'APPROVAL_SIZE_OUT_OF_POLICY' };
    }
    const nowSec = (expect.nowMs ?? Date.now()) / 1000;
    const created = Number(a.created_at_ts);
    const maxAge = expect.maxAgeSec ?? FLY_APPROVAL_MAX_AGE_SEC;
    if (!Number.isFinite(created) || created <= 0) return { ok: false, reason: 'APPROVAL_TS_INVALID' };
    if (nowSec - created > maxAge) return { ok: false, reason: 'APPROVAL_STALE' };
    if (created - nowSec > 30) return { ok: false, reason: 'APPROVAL_FUTURE_DATED' };
    const stop = Number(a.exchange_stop_bp);
    if (!Number.isFinite(stop) || stop <= 5 || stop > exchangeStopCapBp(Number(a.leverage)) + 1e-9) {
      return { ok: false, reason: 'APPROVAL_EXCHANGE_STOP_INVALID' };
    }
  }
  return { ok: true, approval: a };
}

/** True when the approval's lane is an active tile whose prefix owns the trade id. */
export function approvalTileMatchesTrade(a: FlyLiveCopyApproval, tradeId: string | null | undefined): boolean {
  const lane = String(a?.research_lane ?? '').trim().toUpperCase();
  const prefix = LIVE_COPY_TILE_PREFIXES[lane];
  const tid = String(tradeId ?? '').trim().toLowerCase();
  if (!prefix || !tid) return false;
  const [head, ...rest] = tid.split('-');
  return head === prefix && rest.length > 0 && String(a.trade_id).toLowerCase() === tid;
}

/** The entry approval Fly attached to a persisted intent envelope, if any. */
export function readEnvelopeFlyApproval(envelope: unknown): unknown {
  const ctx = (envelope as { context?: Record<string, unknown> } | null)?.context;
  return ctx && typeof ctx === 'object' ? ctx.fly_live_approval ?? null : null;
}

/**
 * Relay allowlist extension for operator-eligible tiles: a valid signed Fly
 * ENTRY approval for this exact trade, relay_eligible, on an active tile.
 * No freshness bound here (lifecycle routing); placement re-checks age.
 */
export function approvalPermitsRelayCopy(
  approvalRaw: unknown,
  tradeId: string | null | undefined,
  secret: string | undefined | null,
): { ok: true; approval: FlyLiveCopyApproval } | { ok: false; reason: string } {
  const a = approvalRaw as Record<string, unknown> | null;
  const event = typeof a?.signed_body === 'string'
    ? (() => { try { return String((JSON.parse(a.signed_body as string) as { event?: unknown }).event ?? ''); } catch { return ''; } })()
    : '';
  if (!LIVE_COPY_ENTRY_EVENTS.has(event)) return { ok: false, reason: 'APPROVAL_NOT_ENTRY' };
  const v = verifyFlyLiveCopyApproval(approvalRaw, secret, {
    tradeId: String(tradeId ?? ''),
    event,
    maxAgeSec: Number.POSITIVE_INFINITY,
  });
  if (!v.ok) return v;
  if (!approvalTileMatchesTrade(v.approval, tradeId)) return { ok: false, reason: 'APPROVAL_TILE_TRADE_MISMATCH' };
  return v;
}

/** Ingest: any event of a Fly-approved trade on an active tile (continuations must flow). */
export function approvalPermitsIngest(
  approvalRaw: unknown,
  tradeId: string,
  event: string,
  secret: string | undefined | null,
  nowMs: number = Date.now(),
): boolean {
  const v = verifyFlyLiveCopyApproval(approvalRaw, secret, { tradeId, event, nowMs });
  return v.ok && approvalTileMatchesTrade(v.approval, tradeId);
}

/**
 * Option 1 "account armed": hired instance ACTIVE, a real exchange (never
 * paper) and an explicit Start (`relayArmedAt`). The legacy
 * realTradingConfirmedAt fallback does NOT count as armed for live copy.
 */
export function liveCopyAccountArmed(instance: {
  status?: unknown;
  exchangeProvider?: unknown;
  dashboardState?: unknown;
} | null | undefined): boolean {
  if (!instance || String(instance.status) !== 'ACTIVE') return false;
  if (String(instance.exchangeProvider ?? '').toLowerCase() === 'paper') return false;
  const dash = instance.dashboardState && typeof instance.dashboardState === 'object'
    ? (instance.dashboardState as Record<string, unknown>)
    : {};
  return typeof dash.relayArmedAt === 'string' && Number.isFinite(Date.parse(dash.relayArmedAt));
}

export type LiveCopyPlacementVerdict =
  | { ok: true; approval: FlyLiveCopyApproval; exchangeStopBp: number; correlationId: string }
  | { ok: false; reason: string };

/**
 * Final executor gate before ANY exchange entry write (Option 1). All four
 * must hold: Fly output ON, tile live switch ON, tile operator/registry
 * eligible (all three carried in Fly's signed fresh approval) AND the
 * copier account armed. Anything else refuses (fail closed).
 */
export function evaluateLiveCopyPlacement(input: {
  envelope: unknown;
  tradeId: string;
  secret: string | undefined | null;
  accountArmed: boolean;
  nowMs?: number;
  maxAgeSec?: number;
}): LiveCopyPlacementVerdict {
  if (input.accountArmed !== true) return { ok: false, reason: 'ACCOUNT_NOT_ARMED' };
  const raw = readEnvelopeFlyApproval(input.envelope);
  if (!raw) return { ok: false, reason: 'APPROVAL_MISSING' };
  const routed = approvalPermitsRelayCopy(raw, input.tradeId, input.secret);
  if (!routed.ok) return routed;
  const fresh = verifyFlyLiveCopyApproval(raw, input.secret, {
    tradeId: input.tradeId,
    event: String(routed.approval.event),
    nowMs: input.nowMs,
    maxAgeSec: input.maxAgeSec ?? FLY_APPROVAL_PLACEMENT_MAX_AGE_SEC,
  });
  if (!fresh.ok) return fresh;
  const a = fresh.approval;
  if (a.eligibility_source !== 'REGISTRY' && a.eligibility_source !== 'OPERATOR') {
    return { ok: false, reason: 'APPROVAL_ELIGIBILITY_SOURCE_INVALID' };
  }
  return { ok: true, approval: a, exchangeStopBp: Number(a.exchange_stop_bp), correlationId: String(a.correlation_id) };
}

/**
 * Boss 2026-10-09: an entry that passed the Fly-approval gate uses the
 * approval's catastrophe backup stop (min(tile hard stop + 25, liq - 15) bp),
 * expressed as a stop-loss margin % at the order leverage. Returns null for
 * every legacy / non-approval path (those keep the frozen -13% clamp).
 */
export function approvalBackedStopLossMarginPct(
  intent: unknown,
  secret: string | undefined | null,
  leverage: number,
): number | null {
  const tradeId = (intent as { signalId?: unknown } | null)?.signalId;
  if (typeof tradeId !== 'string' || !tradeId) return null;
  const v = approvalPermitsRelayCopy(readEnvelopeFlyApproval(intent), tradeId, secret);
  if (!v.ok) return null;
  const lev = Number(v.approval.leverage);
  const bp = Number(v.approval.exchange_stop_bp);
  if (!(lev > 0) || lev !== leverage) return null;
  if (!(bp > 5) || bp > exchangeStopCapBp(lev) + 1e-9) return null;
  // distance fraction = |pct| / (100 * lev)  =>  pct = -bp * lev / 100
  return -Math.round(bp * lev) / 100;
}

/** Signature for an executor report POSTed back to Fly (raw body bytes). */
export function signLiveExecutionReport(rawBody: string | Buffer, secret: string | undefined | null): string | null {
  const key = deriveLiveCopyKey(secret, LIVE_EXECUTION_REPORT_DOMAIN);
  if (!key) return null;
  return `sha256=${createHmac('sha256', key).update(rawBody).digest('hex')}`;
}

/** Fly -> website signed GET (`GET\n{path}\n{ts}`), ±60 s. */
export function verifyFlyViewSignature(
  path: string,
  tsHeader: string | undefined,
  sigHeader: string | undefined,
  secret: string | undefined | null,
  nowMs: number = Date.now(),
): boolean {
  const key = deriveLiveCopyKey(secret, FLY_WEBSITE_STATE_DOMAIN);
  const ts = Number(tsHeader);
  if (!key || !Number.isFinite(ts) || !sigHeader) return false;
  if (Math.abs(nowMs / 1000 - ts) > 60) return false;
  const expected = createHmac('sha256', key).update(`GET\n${path}\n${tsHeader}`).digest('hex');
  const provided = sigHeader.startsWith('sha256=') ? sigHeader.slice(7) : sigHeader;
  return safeHexEqual(expected, provided);
}

/** Bounded in-memory ring of ingest rejections (for monitoring routes). */
export class LiveCopyRejectRing {
  private rows: Array<{ at: number; reason: string; trade_id: string | null; event: string | null }> = [];
  constructor(private readonly max = 500) {}
  push(reason: string, tradeId: string | null, event: string | null, at = Date.now()): void {
    this.rows.push({ at, reason, trade_id: tradeId, event });
    if (this.rows.length > this.max) this.rows.splice(0, this.rows.length - this.max);
  }
  since(ms: number) {
    return this.rows.filter((r) => r.at >= ms);
  }
}
