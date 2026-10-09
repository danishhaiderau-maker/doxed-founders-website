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
    if (!Number.isFinite(stop) || stop <= 5 || stop > 95) {
      return { ok: false, reason: 'APPROVAL_EXCHANGE_STOP_INVALID' };
    }
  }
  return { ok: true, approval: a };
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
