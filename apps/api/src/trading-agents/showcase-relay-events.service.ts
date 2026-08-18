import {
  BadRequestException,
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { SignalCycleStatus, TradingAgentInstanceStatus } from '@prisma/client';
import type { Prisma } from '@prisma/client';
import { createHmac, timingSafeEqual } from 'crypto';
import type { SignalIntentEnvelope } from '@dcf/utils';
import {
  DEFAULT_SUBSCRIBER_LEVERAGE,
  DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
  isExecutableEntryPolicy,
  isMirrorableLaneTradeId,
  SHOWCASE_STRUCTURAL_ENTRY_POLICY_VERSION,
  SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT,
  SUBSCRIBER_TRAIL_LADDER,
} from '@dcf/utils';
import { BotBridgeService } from './bot-bridge.service';
import { SignalCyclesService } from './signal-cycles.service';
import {
  SignalSubscriberExecutionService,
  type RelayExecutorWakeRequest,
} from './signal-subscriber-execution.service';
import { PrismaService } from '../prisma/prisma.service';

export type ShowcaseRelayEventType =
  | 'APPROVE_PENDING'
  | 'ORDER_PLACED'
  | 'POSITION_OPENED'
  | 'POSITION_CLOSED'
  | 'ORDER_EXPIRED'
  | 'LIMIT_UPDATED';

export type ShowcaseRelayEventBody = {
  event: ShowcaseRelayEventType;
  /** Stable source identity for idempotent per-update audit history. */
  event_id?: string | null;
  /** Monotonic per-trade order/chase revision (ORDER_PLACED=activation bucket). */
  event_seq?: number | null;
  trade_id?: string | null;
  ts?: string | null;
  limit_price?: number | null;
  fill_price?: number | null;
  qty?: number | null;
  exit_price?: number | null;
  reason?: string | null;
  exit_reason?: string | null;
  source_created_at?: string | null;
  source_expires_at?: string | null;
  direction?: string | null;
  /** N2 (intent-mirror) — paper/live provenance tag from the showcase bot. */
  intent_source?: 'paper' | 'live' | null;
  /** v1 intent payload fields forwarded by emit_signal_webhook. */
  schema?: string | null;
  signal_price?: number | null;
  margin_usdt?: number | null;
  leverage?: number | null;
  win_prob?: number | null;
  edge_score?: number | null;
  effective_threshold?: number | null;
  research_lane?: string | null;
  entry_limit_policy?: string | null;
  entry_reason?: string | null;
  executable?: boolean | null;
  /** True only for the terminal, exchange-crossing exact-limit revision. */
  marketable_fallback?: boolean | null;
  /** Source-selected UTC instant before which its paper fill is forbidden. */
  relay_settle_not_before_ts?: string | null;
  bot_version?: string | null;
  strategy_mode?: string | null;
  bot_instance_id?: string | null;
  dashboard_owner?: boolean | null;
  dashboard_pid?: number | null;
  dashboard_port?: number | null;
  source_git_rev?: string | null;
  /** Internal receipt timestamp added only after HMAC verification. */
  platform_received_at?: string | null;
};

type RelayLifecycleEnvelope = {
  action?: unknown;
  trade_id?: unknown;
  entry?: {
    exact_limit_price?: unknown;
    exact_qty_btc?: unknown;
  };
  context?: {
    showcase_event?: unknown;
    showcase_event_at?: unknown;
    showcase_event_id?: unknown;
    showcase_event_seq?: unknown;
  };
};

type RelayPersistenceReceipt = {
  persisted: boolean;
  intentApplied: boolean;
};

type CanonicalRelayPersistenceReceipt = RelayPersistenceReceipt & {
  cycleId?: string;
};

/** Prove that the exact incoming revision is the cycle's canonical envelope. */
export function exactLifecycleRevisionMatches(
  current: RelayLifecycleEnvelope | null | undefined,
  incoming: ShowcaseRelayEventBody,
): boolean {
  const currentLimit = Number(current?.entry?.exact_limit_price);
  const incomingLimit = Number(incoming.limit_price);
  const currentQty = Number(current?.entry?.exact_qty_btc);
  const incomingQty = Number(incoming.qty);
  return Boolean(
    current?.action === 'ENTER'
    && String(current?.trade_id ?? '') === String(incoming.trade_id ?? '')
    && current?.context?.showcase_event === incoming.event
    && String(current?.context?.showcase_event_id ?? '') === String(incoming.event_id ?? '')
    && Number(current?.context?.showcase_event_seq) === Number(incoming.event_seq)
    && Number.isFinite(currentLimit)
    && Number.isFinite(incomingLimit)
    && Math.abs(currentLimit - incomingLimit) < 0.005
    && Number.isFinite(currentQty)
    && currentQty > 0
    && Number.isFinite(incomingQty)
    && incomingQty > 0
    && btcQuantityMatches(currentQty, incomingQty),
  );
}

function btcQuantityMatches(a: number, b: number): boolean {
  return Math.abs(Math.floor(a * 1e8) - Math.floor(b * 1e8)) <= 1;
}

/** Current-generation ORDER_PLACED may reclaim an EXPIRED cycle, never a CLOSED one. */
export function canClaimExpiredCycleForCurrentGeneration(input: {
  status?: string | null;
  current?: RelayLifecycleEnvelope | null;
  incoming: ShowcaseRelayEventBody;
}): boolean {
  if (input.status !== SignalCycleStatus.EXPIRED) return false;
  if (input.incoming.event !== 'ORDER_PLACED') return false;
  if (input.current?.context?.showcase_event === 'POSITION_CLOSED') return false;
  if (input.current?.context?.showcase_event === 'POSITION_OPENED') return false;
  const executable =
    input.incoming.executable === true
    && isExecutableEntryPolicy(input.incoming.entry_limit_policy)
    && typeof input.incoming.limit_price === 'number'
    && Number.isFinite(input.incoming.limit_price)
    && input.incoming.limit_price > 0
    && typeof input.incoming.qty === 'number'
    && Number.isFinite(input.incoming.qty)
    && input.incoming.qty > 0;
  if (!executable) return false;
  return shouldApplyExactLifecycleUpdate(input.current, input.incoming);
}

const ORDER_EXPIRED_FLATTEN_REASONS = new Set(['SIGNAL_TTL_EXPIRED', 'TTL_EXPIRED']);

/** Prevent a delayed webhook retry from replacing a newer canonical exact limit. */
export function shouldApplyExactLifecycleUpdate(
  current: RelayLifecycleEnvelope | null | undefined,
  incoming: ShowcaseRelayEventBody,
): boolean {
  if (current?.action !== 'ENTER') return true;
  if (current.context?.showcase_event === 'POSITION_CLOSED') return false;

  const currentSeq = Number(current.context?.showcase_event_seq);
  const incomingSeq = Number(incoming.event_seq);
  const hasCurrentSeq = Number.isInteger(currentSeq) && currentSeq >= 0;
  const hasIncomingSeq = Number.isInteger(incomingSeq) && incomingSeq >= 0;
  if (hasCurrentSeq && hasIncomingSeq) {
    if (incomingSeq !== currentSeq) return incomingSeq > currentSeq;
    return false;
  }
  if (hasCurrentSeq && !hasIncomingSeq) return false;

  const currentAt = Date.parse(String(current.context?.showcase_event_at ?? ''));
  const incomingAt = Date.parse(String(incoming.ts ?? ''));
  if (Number.isFinite(currentAt) && Number.isFinite(incomingAt)) {
    return incomingAt > currentAt;
  }
  const currentId = String(current.context?.showcase_event_id ?? '');
  const incomingId = String(incoming.event_id ?? '');
  if (currentId && incomingId && currentId === incomingId) return false;
  return true;
}

/**
 * Build an audit-only legacy envelope or an executable signed relay envelope.
 *
 * Real cycles get a fully populated envelope built by signal-envelope.mapper's
 * buildIntentEnvelope() from live bot state. The showcase relay webhook path
 * Legacy wake events do not have authenticated entry context, so they remain
 * deliberately non-executable. Signed dcf-showcase-intent-v1 events carry the
 * fields required to construct a conformant ENSE immediately.
 *
 * APPROVE_PENDING is visibility-only while virtual chase is counting. Only
 * a signed ORDER_PLACED/LIMIT_UPDATED carrying the canonical structural
 * policy and an exact positive limit can produce an executable ENTER envelope.
 */
export function relayIntentEnvelope(
  cycleId: string,
  tradeId: string,
  body?: ShowcaseRelayEventBody,
) {
  const direction = body?.direction?.toUpperCase();
  const isSignedIntent =
    body?.schema === 'dcf-showcase-intent-v1'
    && Boolean(body?.platform_received_at)
    && (direction === 'LONG' || direction === 'SHORT');

  // Legacy owner events are wake-only. Keep their audit cycles deliberately
  // non-executable so an unsigned payload can never inject a direction/order.
  if (!isSignedIntent) {
    return {
      cycle_id: cycleId,
      trade_id: tradeId,
      source: 'showcase_relay_legacy_wake',
      schema: 'showcase_relay_audit_v1',
    };
  }

  const dir = direction as 'LONG' | 'SHORT';
  const executablePolicy = isExecutableEntryPolicy(body?.entry_limit_policy)
    ? (body!.entry_limit_policy as string)
    : null;
  const exactLimitPrice =
    (body?.event === 'ORDER_PLACED' || body?.event === 'LIMIT_UPDATED')
    && body?.executable === true
    && executablePolicy !== null
    && typeof body?.limit_price === 'number'
    && Number.isFinite(body.limit_price)
    && body.limit_price > 0
      ? body.limit_price
      : null;
  const exactQtyBtc =
    (body?.event === 'ORDER_PLACED' || body?.event === 'LIMIT_UPDATED')
    && body?.executable === true
    && typeof body?.qty === 'number'
    && Number.isFinite(body.qty)
    && body.qty > 0
      ? body.qty
      : null;
  const settleNotBeforeMs = Date.parse(
    String(body?.relay_settle_not_before_ts ?? ''),
  );
  const marketableFallback =
    body?.event === 'LIMIT_UPDATED'
    && body?.marketable_fallback === true
    && Number.isFinite(settleNotBeforeMs);
  const exactEntryLifecycle =
    body?.event === 'ORDER_PLACED' || body?.event === 'LIMIT_UPDATED';
  const lifecycleContext = {
    signed_showcase_event: Boolean(body?.platform_received_at),
    ...(body?.event ? { showcase_event: body.event } : {}),
    ...(body?.ts ? { showcase_event_at: body.ts } : {}),
    ...(body?.event_id ? { showcase_event_id: body.event_id } : {}),
    ...(Number.isInteger(body?.event_seq)
      ? { showcase_event_seq: body.event_seq }
      : {}),
    ...(body?.platform_received_at
      ? { platform_received_at: body.platform_received_at }
      : {}),
    ...(body?.bot_instance_id ? { bot_instance_id: body.bot_instance_id } : {}),
    ...(executablePolicy !== null ? { entry_limit_policy: executablePolicy } : {}),
    ...(exactEntryLifecycle
      ? {
          // Always overwrite these fields on an exact lifecycle revision so
          // a later ordinary reprice cannot inherit a stale settlement marker.
          marketable_fallback: marketableFallback,
          relay_settle_not_before_ts: marketableFallback
            ? body?.relay_settle_not_before_ts
            : null,
        }
      : {}),
    ...(body?.event === 'POSITION_CLOSED'
      && typeof body.exit_price === 'number'
      && Number.isFinite(body.exit_price)
      && body.exit_price > 0
      ? { showcase_exit_price: body.exit_price }
      : {}),
    ...(body?.event === 'POSITION_CLOSED' && body.exit_reason
      ? { showcase_exit_reason: body.exit_reason }
      : {}),
    ...(body?.event === 'ORDER_EXPIRED' && body.source_created_at
      ? { source_created_at: body.source_created_at }
      : {}),
    ...(body?.event === 'ORDER_EXPIRED' && body.source_expires_at
      ? { source_expires_at: body.source_expires_at }
      : {}),
  };
  if (exactLimitPrice == null) {
    return {
      cycle_id: cycleId,
      trade_id: tradeId,
      source: 'showcase_signed_visibility',
      schema: 'showcase_relay_audit_v2',
      direction: dir,
      version: body?.bot_version ?? 'showcase-relay-v2',
      context: lifecycleContext,
    };
  }
  const envelope: SignalIntentEnvelope & {
    intent_source?: 'paper' | 'live';
    trade_id: string;
    entry: SignalIntentEnvelope['entry'] & {
      exact_limit_price?: number;
      exact_qty_btc?: number;
    };
    context: SignalIntentEnvelope['context'] & {
      signed_showcase_event?: boolean;
      showcase_event?: ShowcaseRelayEventType;
      showcase_event_at?: string;
      platform_received_at?: string;
      bot_instance_id?: string;
      showcase_exit_price?: number;
      showcase_exit_reason?: string;
    };
    margin_usdt?: number;
    leverage?: number;
    effective_threshold?: number;
    research_lane?: string;
  } = {
    schema: 'dcf-signal-intent/v1',
    cycleId,
    signalId: tradeId,
    version: body?.bot_version ?? 'showcase-relay-v1',
    action: 'ENTER',
    trade_id: tradeId,
    direction: dir,
    entry: {
      type: 'LIMIT',
      mode: 'EXACT_LIMIT',
      offset_pct: 0,
      exact_limit_price: exactLimitPrice,
      ...(exactQtyBtc !== null ? { exact_qty_btc: exactQtyBtc } : {}),
      reference: 'SHOWCASE_EXACT_LIMIT',
      ttl_sec: 1800,
    },
    risk: {
      stop_loss_margin_pct: SUBSCRIBER_DEFAULT_HARD_STOP_MARGIN_PCT,
      take_profit_ladder: SUBSCRIBER_TRAIL_LADDER.map(
        ([at_margin_pct, lock_margin_pct]) => ({
          at_margin_pct,
          close_position_pct: lock_margin_pct,
        }),
      ),
      leverage_hint: DEFAULT_SUBSCRIBER_LEVERAGE,
      max_margin_usd: DEFAULT_SUBSCRIBER_MAX_MARGIN_USD,
    },
    context: {
      regime: 'UNKNOWN',
      edge: Number(body?.edge_score ?? 0),
      ai_win_prob: Number(body?.win_prob ?? 0),
      entry_mode_source: body?.entry_reason ?? 'SHOWCASE_EXACT_LIMIT',
      research_venue: 'bitfinex',
      disclaimer:
        'Signed exact showcase limit. Subscriber execution remains subject to platform and exchange safety gates.',
      ...lifecycleContext,
    },
    ...(body?.intent_source ? { intent_source: body.intent_source } : {}),
    ...(body?.margin_usdt != null ? { margin_usdt: body.margin_usdt } : {}),
    ...(body?.leverage != null ? { leverage: body.leverage } : {}),
    ...(body?.effective_threshold != null
      ? { effective_threshold: body.effective_threshold }
      : {}),
    ...(body?.research_lane ? { research_lane: body.research_lane } : {}),
  };
  return envelope;
}

@Injectable()
export class ShowcaseRelayEventsService {
  private readonly logger = new Logger(ShowcaseRelayEventsService.name);
  /**
   * Serialize same-trade lifecycle mutations inside one API process. The
   * Postgres advisory transaction lock in persistRelayEvent supplies the
   * corresponding cross-replica guarantee.
   */
  private readonly relayPersistenceTails = new Map<string, Promise<void>>();
  private relayAgentId: string | null = null;

  constructor(
    private readonly config: ConfigService,
    private readonly botBridge: BotBridgeService,
    private readonly cycles: SignalCyclesService,
    private readonly execution: SignalSubscriberExecutionService,
    private readonly prisma: PrismaService,
  ) {}

  assertAuthorized(secretHeader: string | undefined) {
    const expected = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    if (!expected) {
      throw new UnauthorizedException('Showcase relay webhook not configured');
    }
    const provided = (secretHeader ?? '').trim();
    // Timing-safe compare to avoid leaking the secret via response-time side channels.
    if (provided.length !== expected.length) {
      throw new UnauthorizedException('Invalid bot control secret');
    }
    let diff = 0;
    for (let i = 0; i < expected.length; i++) {
      diff |= provided.charCodeAt(i) ^ expected.charCodeAt(i);
    }
    if (diff !== 0) {
      throw new UnauthorizedException('Invalid bot control secret');
    }
  }

  /**
   * N1 (intent-mirror) — HMAC-SHA256 signature verification over the raw
   * request body. The showcase bot signs the canonical JSON with
   * `SHOWCASE_WEBHOOK_SECRET` (distinct from `BOT_CONTROL_SECRET`) and sends
   * the digest in `X-Showcase-Signature: sha256=<hex>`.
   *
   * Fail-closed:
   *   - If the secret is not configured on the API, the signature check is
   *     SKIPPED (treated as not-yet-rolled-out) so the legacy G13 bearer path
   *     remains the gate. Once the operator sets `SHOWCASE_WEBHOOK_SECRET`
   *     here AND on the bot, signing is enforced.
   *   - If the secret IS configured here but the header is missing or
   *     malformed, the request is rejected (401) so a forged payload without
   *     a valid signature can never inject a fake `cont-` intent.
   *
   * Timing-safe: the HMAC digest compare uses `timingSafeEqual` (no early
   * return on length mismatch — we hash-then-compare fixed-length digests).
   */
  private isLegacyWakeOnly(body: ShowcaseRelayEventBody | undefined): boolean {
    if (!body) return false;
    // Legacy owner webhooks are wake-up notifications only. They may describe
    // the current order/close, but they must not carry any fields consumed by
    // the intent-entry path. Fail closed on unknown future fields as well:
    // anything outside this explicit wake-only envelope requires HMAC.
    const allowedKeys = new Set([
      'event',
      'trade_id',
      'ts',
      'limit_price',
      'exit_price',
      'reason',
      'exit_reason',
      'direction',
      'bot_instance_id',
      'dashboard_owner',
      'dashboard_pid',
      'dashboard_port',
      'source_git_rev',
    ]);
    return Object.keys(body).every((key) => allowedKeys.has(key));
  }

  verifySignature(
    rawBody: Buffer | string | undefined,
    sigHeader: string | undefined,
    relayBody?: ShowcaseRelayEventBody,
  ): boolean {
    const secret = this.config.get<string>('SHOWCASE_WEBHOOK_SECRET')?.trim();
    // Rollout boundary: until the operator sets the shared secret on the API,
    // the signature gate is not enforced — the legacy G13 bearer-secret check
    // (assertAuthorized) remains the sole auth. Once set, signing is required.
    if (!secret) {
      return false;
    }
    // Compatibility bridge for the existing home owner process: an unsigned
    // legacy event may only wake a canonical-state poll. It still passed the
    // independent BOT_CONTROL_SECRET check in the controller and must pass
    // assertActiveDashboardOwner() below. It cannot create an intent from
    // supplied price/size/risk fields. Signed dcf-showcase-intent-v1 payloads
    // remain mandatory for direct intent entry.
    if (!sigHeader && this.isLegacyWakeOnly(relayBody)) {
      this.logger.warn(
        `Accepted legacy owner wake without HMAC event=${relayBody?.event ?? '?'} trade=${relayBody?.trade_id ?? '?'}`,
      );
      return false;
    }
    if (!rawBody) {
      throw new UnauthorizedException('Showcase signature verify requires raw body');
    }
    const body = typeof rawBody === 'string' ? Buffer.from(rawBody) : rawBody;
    const expected = createHmac('sha256', secret).update(body).digest();
    if (!sigHeader) {
      throw new UnauthorizedException('Missing showcase signature');
    }
    const providedHex = sigHeader.trim();
    const m = /^sha256=([0-9a-fA-F]+)$/.exec(providedHex);
    if (!m) {
      throw new UnauthorizedException('Malformed showcase signature header');
    }
    let provided: Buffer;
    try {
      provided = Buffer.from(m[1], 'hex');
    } catch {
      throw new UnauthorizedException('Invalid showcase signature encoding');
    }
    if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
      throw new UnauthorizedException('Invalid showcase signature');
    }
    return true;
  }

  private queueExecutionWake(
    event: ShowcaseRelayEventType,
    tradeId?: string | null,
    receivedAt?: string,
    signedTerminal?: RelayExecutorWakeRequest['signedClose']
      | RelayExecutorWakeRequest['signedOpen']
      | RelayExecutorWakeRequest['signedExpiry'],
  ): void {
    // Start the private-network dispatch in the current event-loop turn. The
    // method remains non-blocking (the promise is intentionally not awaited),
    // but avoiding setImmediate prevents an already-busy API loop from adding
    // an avoidable scheduling turn to the money path. This mattered on a live
    // close that reached Bitfinex in 3069 ms: only 69 ms outside the contract.
    void this.execution.requestExecutorWake(event, tradeId, receivedAt, signedTerminal).catch((err) => {
      this.logger.error(
        `Showcase execution wake ${event} failed: ${err instanceof Error ? err.message : err}`,
      );
    });
  }

  private queueCanonicalReconcile(event: ShowcaseRelayEventType): void {
    if (event === 'APPROVE_PENDING') return;
    setImmediate(() => {
      const work =
        event === 'ORDER_PLACED'
          ? this.cycles.wakeFromShowcase({ intents: true, closures: false })
          : event === 'POSITION_CLOSED'
            ? this.cycles.wakeFromShowcase({ intents: false, closures: true })
            : this.cycles.wakeFromShowcase({ intents: true, closures: true });
      void work.catch((err) => {
        this.logger.warn(
          `Showcase canonical reconcile ${event} failed (fast path remains durable): ${
            err instanceof Error ? err.message : err
          }`,
        );
      });
    });
  }

  private assertActiveDashboardOwner(body: ShowcaseRelayEventBody): void {
    const suppliedId = body.bot_instance_id?.trim();
    if (body.dashboard_owner !== true || !suppliedId) {
      throw new UnauthorizedException('Relay event is not from a dashboard owner');
    }
    const canonical = this.botBridge.getCachedDashboardOwnerIdentity();
    if (!canonical) {
      throw new UnauthorizedException('Active dashboard owner is not currently confirmed');
    }
    if (canonical.instanceId !== suppliedId) {
      this.logger.warn(
        `Rejected stale dashboard instance supplied=${suppliedId} active=${canonical.instanceId}`,
      );
      throw new UnauthorizedException('Relay event dashboard instance is stale');
    }
    if (
      body.dashboard_port != null
      && canonical.port != null
      && body.dashboard_port !== canonical.port
    ) {
      throw new UnauthorizedException('Relay event dashboard port does not match owner');
    }
  }

  async ingest(
    slug: string,
    body: ShowcaseRelayEventBody,
    context?: { rawBody?: Buffer | string; signatureHeader?: string },
  ) {
    const ingestStartedAt = Date.now();
    if (slug !== 'conservative-btc') {
      return { ok: false, reason: 'unsupported_agent' };
    }

    // N1 (intent-mirror) — verify the HMAC signature BEFORE any state mutation
    // so a forged payload can never create an intent row or wake the relay.
    // The legacy G13 bearer-secret check (assertAuthorized) is still performed
    // by the controller before calling ingest; this is the payload-level guard.
    const verifiedSignedPayload = this.verifySignature(
      context?.rawBody,
      context?.signatureHeader,
      body,
    );
    this.assertActiveDashboardOwner(body);

    const tradeId = (body.trade_id ?? '').trim();
    const researchLane = (body.research_lane ?? '').trim().toUpperCase();
    if (
      !isMirrorableLaneTradeId(tradeId)
      || (researchLane && researchLane !== 'CONTINUOUS')
    ) {
      this.logger.warn(
        `Rejected non-mirrorable showcase relay event=${body.event} ` +
        `trade=${tradeId || '?'} lane=${researchLane || 'UNKNOWN'}`,
      );
      return {
        ok: false,
        reason: 'non_mirrorable_lane',
        event: body.event,
        trade_id: tradeId || null,
        intentCreated: false,
        persisted: false,
        platform_received_at: null,
        ingest_ms: Date.now() - ingestStartedAt,
      };
    }

    this.botBridge.invalidateCache();

    const event = body.event;
    const persistBody: ShowcaseRelayEventBody = verifiedSignedPayload
      ? { ...body, platform_received_at: new Date().toISOString() }
      : body;
    let intentCreated = false;

    // A verified v1 payload already contains authenticated direction, price,
    // owner identity and (for ORDER_PLACED/LIMIT_UPDATED) the exact resting
    // limit. Persist it directly; never make the money path wait for a
    // cross-region callback. Canonical state reconciliation still runs
    // immediately, but only as an asynchronous audit/backstop.
    const signedLifecycleEvent =
      verifiedSignedPayload
      && body.schema === 'dcf-showcase-intent-v1'
      && (body.direction?.toUpperCase() === 'LONG'
        || body.direction?.toUpperCase() === 'SHORT');
    const directExecutableIntent =
      signedLifecycleEvent
      && (event === 'ORDER_PLACED' || event === 'LIMIT_UPDATED')
      && body.executable === true
      && isExecutableEntryPolicy(body.entry_limit_policy)
      && typeof body.limit_price === 'number'
      && Number.isFinite(body.limit_price)
      && body.limit_price > 0
      && typeof body.qty === 'number'
      && Number.isFinite(body.qty)
      && body.qty > 0;

    if (
      signedLifecycleEvent
      && (event === 'ORDER_PLACED' || event === 'LIMIT_UPDATED')
      && !directExecutableIntent
    ) {
      throw new BadRequestException(
        'Signed executable relay event requires exact executable limit policy and quantity',
      );
    }

    const signedCloseEvidence = event === 'POSITION_CLOSED' && signedLifecycleEvent && body.ts
      && Number.isFinite(Date.parse(body.ts)) && persistBody.platform_received_at
      && Number.isFinite(Date.parse(persistBody.platform_received_at))
      && typeof body.trade_id === 'string' && body.trade_id.trim()
      && typeof body.event_id === 'string' && body.event_id.trim() && body.event_id.trim().length <= 255
      && typeof body.event_seq === 'number' && Number.isInteger(body.event_seq) && body.event_seq >= 0
      && typeof body.exit_price === 'number' && Number.isFinite(body.exit_price) && body.exit_price > 0
      && typeof body.exit_reason === 'string' && body.exit_reason.trim()
      && Date.parse(persistBody.platform_received_at) >= Date.parse(body.ts) - 5_000
      && Date.parse(body.ts) <= Date.now() + 5_000
      && Date.parse(persistBody.platform_received_at) <= Date.now() + 5_000
      && Date.now() - Date.parse(persistBody.platform_received_at) <= 5 * 60_000
      ? {
          tradeId: body.trade_id.trim(),
          eventId: body.event_id.trim(),
          eventSeq: body.event_seq,
          exitPrice: body.exit_price,
          exitReason: body.exit_reason.trim(),
          sourceEventAtMs: Date.parse(body.ts),
          platformReceivedAtMs: Date.parse(persistBody.platform_received_at),
        }
      : undefined;
    const earlyNoCopyClose =
      event === 'POSITION_CLOSED' && tradeId
        ? await this.isShowcaseOnlyNoCopyClose(tradeId)
        : false;
    if (event === 'POSITION_CLOSED' && signedLifecycleEvent && !signedCloseEvidence) {
      if (earlyNoCopyClose) {
        const noCopyAck = await this.acknowledgeShowcaseOnlyCloseIfRelayPaused(tradeId, persistBody);
        return {
          ok: true,
          accepted: true,
          action: 'NO_COPY_PARTICIPANT',
          reason: 'SHOWCASE_ONLY_NO_COPY_PARTICIPANT',
          exchange_mutation: false,
          event,
          trade_id: tradeId,
          intentCreated: false,
          persisted: Boolean(noCopyAck?.persisted),
          negative_evidence: 'SHOWCASE_ONLY_NO_COPY_PARTICIPANT',
          platform_received_at: persistBody.platform_received_at ?? null,
          ingest_ms: Date.now() - ingestStartedAt,
        };
      }
      await this.persistIncompleteCloseOutbox(tradeId, persistBody);
      return {
        ok: true,
        accepted: false,
        action: 'SCHEMA_QUEUED',
        reason: 'SIGNED_POSITION_CLOSED_INCOMPLETE',
        queued: true,
        exchange_mutation: false,
        event,
        trade_id: tradeId,
        intentCreated: false,
        persisted: true,
        platform_received_at: persistBody.platform_received_at ?? null,
        ingest_ms: Date.now() - ingestStartedAt,
      };
    }
    const signedOpenEvidence = event === 'POSITION_OPENED' && body.ts
      && typeof body.fill_price === 'number' && Number.isFinite(body.fill_price) && body.fill_price > 0
      && persistBody.platform_received_at
      && Number.isFinite(Date.parse(body.ts))
      ? {
          fillPrice: body.fill_price,
          sourceEventAtMs: Date.parse(body.ts),
          platformReceivedAtMs: Date.parse(persistBody.platform_received_at),
        }
      : undefined;
    if (event === 'POSITION_OPENED' && signedLifecycleEvent && !signedOpenEvidence) {
      throw new BadRequestException('Signed position open requires exact source fill evidence');
    }
    const signedExpiryEvidence = event === 'ORDER_EXPIRED'
      && body.ts && body.source_expires_at && persistBody.platform_received_at
      && Number.isFinite(Date.parse(body.ts))
      && Number.isFinite(Date.parse(body.source_expires_at))
      && typeof body.event_seq === 'number'
      && typeof body.limit_price === 'number'
      && typeof body.event_id === 'string'
      && typeof body.reason === 'string'
      ? {
          sourceEventAtMs: Date.parse(body.ts),
          sourceExpiresAtMs: Date.parse(body.source_expires_at),
          platformReceivedAtMs: Date.parse(persistBody.platform_received_at),
          eventSeq: body.event_seq,
          limitPrice: body.limit_price,
          eventId: body.event_id.trim(),
          reason: body.reason as 'SIGNAL_TTL_EXPIRED' | 'TTL_EXPIRED',
        }
      : undefined;
    const expiryFlattenable = Boolean(
      signedExpiryEvidence
      && Number.isInteger(signedExpiryEvidence.eventSeq)
      && signedExpiryEvidence.eventSeq >= 0
      && Number.isFinite(signedExpiryEvidence.limitPrice)
      && signedExpiryEvidence.limitPrice > 0
      && signedExpiryEvidence.eventId
      && signedExpiryEvidence.eventId.length <= 255
      && ORDER_EXPIRED_FLATTEN_REASONS.has(signedExpiryEvidence.reason),
    );
    if (event === 'ORDER_EXPIRED' && signedLifecycleEvent && !expiryFlattenable) {
      await this.persistRelayEvent('conservative-btc', persistBody);
      return {
        ok: true,
        accepted: true,
        action: 'ORDER_EXPIRED_ACKED',
        reason: 'DURABLE_ACK_NO_FLATTEN',
        exchange_mutation: false,
        event,
        trade_id: tradeId || null,
        intentCreated: false,
        persisted: true,
        platform_received_at: persistBody.platform_received_at ?? null,
        ingest_ms: Date.now() - ingestStartedAt,
      };
    }

    const noCopyClose = earlyNoCopyClose;

    // Start the private worker's safety preflight as soon as the signed owner
    // event is authenticated. Entry still waits for this exact cycle to become
    // durable. Close can converge only an already-owned open lot after the
    // worker independently observes the canonical showcase closure. This
    // overlaps transport / safety reads with the canonical transaction below.
    // Never wake or pre-wake for a Showcase-only close while live-copy is paused:
    // that path must not invent a participant or exchange exit.
    if (
      !noCopyClose
      && signedLifecycleEvent
      && (event === 'ORDER_PLACED' || event === 'POSITION_OPENED' || event === 'POSITION_CLOSED' || event === 'ORDER_EXPIRED')
    ) {
      this.execution.requestExecutorPreWake?.(
        event,
        body.trade_id ?? null,
        persistBody.platform_received_at ?? undefined,
        event === 'ORDER_EXPIRED' && expiryFlattenable ? signedExpiryEvidence : event === 'POSITION_OPENED' ? signedOpenEvidence : signedCloseEvidence,
      );
    }

    // Retain unsigned messages for observability, but never use them to wake
    // execution.  Otherwise a polling/reconciliation fallback could turn an
    // unauthenticated canonical snapshot into a real exchange order.

    // [SHOWCASE_RELAY_PERSIST_2026-07-08] Persist the inbound relay event as
    // a signalCycleEvent row so the cycle audit trail reflects every webhook
    // the platform received (APPROVE_PENDING -> ORDER_PLACED -> POSITION_CLOSED).
    // Without this, only events emitted by the copy-trader land in the table;
    // pure showcase-only trades (e.g. cassette-replayed demo cycles) leave no
    // trace and the relay round-trip harness check sees no APPROVE_PENDING /
    // POSITION_CLOSED rows. Best-effort — never let persistence failure kill
    // the relay wake (the wake is the critical side-effect).
    let persisted = false;
    let canonicalRevisionApplied = false;
    let executionWakeQueued = false;
    try {
      const receipt = await this.persistRelayEvent(slug, persistBody, () => {
        // Canonical signed state is committed before this callback. Start the
        // private executor wake while audit-row idempotency is persisted so
        // bookkeeping never sits in front of the exchange path.
        if (!noCopyClose && signedLifecycleEvent && event !== 'APPROVE_PENDING') {
          this.queueExecutionWake(
            event,
            body.trade_id ?? null,
            persistBody.platform_received_at ?? undefined,
            event === 'ORDER_EXPIRED' && expiryFlattenable ? signedExpiryEvidence : event === 'POSITION_OPENED' ? signedOpenEvidence : signedCloseEvidence,
          );
          executionWakeQueued = true;
        }
      });
      persisted = receipt.persisted;
      canonicalRevisionApplied = receipt.intentApplied;
    } catch (err) {
      this.logger.error(
        `Showcase relay persist failed: ${err instanceof Error ? err.message : err}`,
      );
      // Signed fast-path execution is only safe after the durable cycle exists.
      // Let the sender receive a 5xx and retry the idempotent event.
      if (signedLifecycleEvent) throw err;
    }

    // Return the webhook response without waiting for exchange reconciliation.
    // The durable cycle plus the normal 2s runner remain the crash backstop.
    if (!noCopyClose && signedLifecycleEvent && event !== 'APPROVE_PENDING' && !executionWakeQueued) {
      this.queueExecutionWake(
        event,
        body.trade_id ?? null,
        persistBody.platform_received_at ?? undefined,
        event === 'ORDER_EXPIRED' && expiryFlattenable ? signedExpiryEvidence : event === 'POSITION_OPENED' ? signedOpenEvidence : signedCloseEvidence,
      );
    }
    if (!noCopyClose && signedLifecycleEvent) {
      this.queueCanonicalReconcile(event);
      intentCreated =
        persisted && directExecutableIntent && canonicalRevisionApplied;
    }

    this.logger.log(
      `Showcase relay queued ${event} trade=${body.trade_id ?? '?'} signed=${signedLifecycleEvent ? 'yes' : 'no'} executable=${directExecutableIntent ? 'yes' : 'no'} intent=${intentCreated ? 'ready' : 'none'} persisted=${persisted}`,
    );

    if (event === 'POSITION_CLOSED' && tradeId) {
      const noCopyAck = await this.acknowledgeShowcaseOnlyCloseIfRelayPaused(tradeId, persistBody);
      if (noCopyAck) {
        return {
          ok: true,
          accepted: true,
          action: 'NO_COPY_PARTICIPANT',
          reason: 'RELAY_WAS_PAUSED_AT_SOURCE_ENTRY',
          exchange_mutation: false,
          event,
          trade_id: tradeId,
          intentCreated: false,
          persisted: noCopyAck.persisted || persisted,
          negative_evidence: 'SHOWCASE_ONLY_RELAY_PAUSED',
          platform_received_at: persistBody.platform_received_at ?? null,
          ingest_ms: Date.now() - ingestStartedAt,
        };
      }
    }

    return {
      ok: true,
      event,
      trade_id: body.trade_id ?? null,
      intentCreated,
      persisted,
      canonical_revision_applied: canonicalRevisionApplied,
      canonical_event_id: canonicalRevisionApplied ? persistBody.event_id ?? null : null,
      canonical_event_seq: canonicalRevisionApplied ? persistBody.event_seq ?? null : null,
      canonical_limit_price:
        canonicalRevisionApplied ? persistBody.limit_price ?? null : null,
      canonical_trade_id: canonicalRevisionApplied ? persistBody.trade_id ?? null : null,
      platform_received_at: persistBody.platform_received_at ?? null,
      ingest_ms: Date.now() - ingestStartedAt,
    };
  }

  /**
   * True when Showcase closed a trade the live-copy hire never admitted.
   * Preview only — does not persist evidence or mutate exchange state.
   */
  private async isShowcaseOnlyRelayPausedClose(tradeId: string): Promise<boolean> {
    try {
      const agent = await this.prisma.tradingAgent.findUnique({
        where: { slug: 'conservative-btc' },
        select: { id: true },
      });
      if (!agent) return false;
      const liveParticipant = await this.prisma.signalCycleParticipant.findFirst({
        where: {
          venue: 'bitfinex',
          cycle: { agentId: agent.id, tradeId },
          status: {
            in: [
              SignalCycleStatus.PENDING_ENTRY,
              SignalCycleStatus.OPEN,
              SignalCycleStatus.CLOSED,
            ],
          },
        },
        select: { id: true },
      });
      if (liveParticipant) return false;
      const liveHire = await this.prisma.tradingAgentInstance.findFirst({
        where: {
          agentId: agent.id,
          exchangeProvider: 'bitfinex',
          status: {
            in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED],
          },
        },
        select: { id: true },
      });
      return Boolean(liveHire);
    } catch {
      // Fail closed: a lookup error must not skip the owned-lot exit wake.
      return false;
    }
  }

  private async isShowcaseOnlyNoCopyClose(tradeId: string): Promise<boolean> {
    return this.isShowcaseOnlyRelayPausedClose(tradeId);
  }

  private async persistIncompleteCloseOutbox(
    tradeId: string,
    body: ShowcaseRelayEventBody,
  ): Promise<void> {
    try {
      await this.persistRelayEvent('conservative-btc', {
        ...body,
        event: 'POSITION_CLOSED',
        trade_id: tradeId,
      });
    } catch (err) {
      this.logger.warn(
        `POSITION_CLOSED outbox persist failed trade=${tradeId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
    }
  }

  /**
   * Showcase closed a trade that live-copy never admitted (hire PAUSED / EXIT_ONLY).
   * Acknowledge without HTTP 400 and never invent a retrospective participant.
   */
  private async acknowledgeShowcaseOnlyCloseIfRelayPaused(
    tradeId: string,
    body: ShowcaseRelayEventBody,
  ): Promise<{ persisted: boolean } | null> {
    try {
      return await this.tryAcknowledgeShowcaseOnlyCloseIfRelayPaused(tradeId, body);
    } catch (err) {
      this.logger.warn(
        `NO_COPY_PARTICIPANT lookup failed trade=${tradeId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
      return null;
    }
  }

  private async tryAcknowledgeShowcaseOnlyCloseIfRelayPaused(
    tradeId: string,
    body: ShowcaseRelayEventBody,
  ): Promise<{ persisted: boolean } | null> {
    const agent = await this.prisma.tradingAgent.findUnique({
      where: { slug: 'conservative-btc' },
      select: { id: true },
    });
    if (!agent) return null;
    const liveParticipant = await this.prisma.signalCycleParticipant.findFirst({
      where: {
        venue: 'bitfinex',
        cycle: { agentId: agent.id, tradeId },
        status: {
          in: [
            SignalCycleStatus.PENDING_ENTRY,
            SignalCycleStatus.OPEN,
            SignalCycleStatus.CLOSED,
          ],
        },
      },
      select: { id: true, status: true },
    });
    if (liveParticipant) return null;
    const liveHire = await this.prisma.tradingAgentInstance.findFirst({
      where: {
        agentId: agent.id,
        exchangeProvider: 'bitfinex',
        status: {
          in: [TradingAgentInstanceStatus.ACTIVE, TradingAgentInstanceStatus.PAUSED],
        },
      },
      select: { id: true, userId: true },
    });
    if (!liveHire) return null;
    const pausedLive = liveHire;

    let persistedNegative = false;
    try {
      const cycle = await this.prisma.signalCycle.findFirst({
        where: { agentId: agent.id, tradeId },
        select: { id: true },
      });
      if (cycle) {
        await this.prisma.signalCycleEvent.create({
          data: {
            cycleId: cycle.id,
            eventType: 'NEGATIVE_EVIDENCE',
            payload: {
              schema: 'showcase_only_relay_paused_v1',
              type: 'SHOWCASE_ONLY_RELAY_PAUSED',
              trade_id: tradeId,
              exit_price: body.exit_price ?? null,
              exit_reason: body.exit_reason ?? body.reason ?? null,
              source_event_id: body.event_id ?? null,
              platform_received_at: body.platform_received_at ?? null,
              exchange_mutation: false,
              note: 'Showcase closed while live relay was paused; no copy participant existed',
            } as unknown as Prisma.InputJsonValue,
          },
        });
        persistedNegative = true;
      }
    } catch (err) {
      this.logger.warn(
        `SHOWCASE_ONLY_RELAY_PAUSED persist failed trade=${tradeId}: ${
          err instanceof Error ? err.message : err
        }`,
      );
    }
    this.logger.log(
      `POSITION_CLOSED acknowledged NO_COPY_PARTICIPANT trade=${tradeId} (relay paused, no Bitfinex participant)`,
    );
    return { persisted: persistedNegative };
  }

  /**
   * Best-effort persistence of an inbound relay webhook as a signal cycle +
   * signal cycle event. Idempotent per (agent, trade_id, event) — replays of
   * the same cassette won't duplicate rows for the same event type.
   *
   * Visible behavior:
   *   - APPROVE_PENDING -> create INTENT cycle (if missing) + APPROVE_PENDING event
   *   - ORDER_PLACED    -> ensure cycle exists + ORDER_PLACED audit event
   *   - POSITION_CLOSED -> ensure cycle exists, transition to CLOSED, + POSITION_CLOSED event
   *   - LIMIT_UPDATED   -> ensure cycle exists, + LIMIT_UPDATED event (no status change)
   */
  private async persistRelayEvent(
    slug: string,
    body: ShowcaseRelayEventBody,
    onCanonicalPersisted?: (receipt: CanonicalRelayPersistenceReceipt) => void,
  ): Promise<RelayPersistenceReceipt> {
    const tradeId = (body.trade_id ?? '').trim();
    if (!tradeId) return { persisted: false, intentApplied: false };
    const eventId =
      body.event_id?.trim()
      || [
        body.event,
        tradeId,
        Number.isInteger(body.event_seq) ? body.event_seq : 'na',
        body.ts ?? 'unknown',
      ].join(':');
    const eventBody: ShowcaseRelayEventBody = { ...body, event_id: eventId };
    let agentId = this.relayAgentId;
    if (!agentId) {
      const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
      if (!agent) return { persisted: false, intentApplied: false };
      agentId = agent.id;
      this.relayAgentId = agentId;
    }

    const lockKey = `${agentId}:${tradeId}`;
    const canonical = await this.withRelayPersistenceLock(lockKey, () =>
      this.prisma.$transaction(async (tx) => {
        // Transaction-scoped advisory locking makes the envelope
        // compare-and-write atomic across every Railway API replica. Without
        // it, concurrent seq=N and seq=N+1 requests can both read N-1 and the
        // older request can commit last.
        //
        // Use $executeRaw (not $queryRaw): pg_advisory_xact_lock() returns
        // Postgres `void`, and Prisma 6.x throws
        // "Failed to deserialize column of type 'void'" if you try to read
        // the row back via $queryRaw. $executeRaw discards the result so the
        // advisory lock is still acquired inside the transaction without
        // triggering the deserializer. (P2010 raw-query failure.)
        await tx.$executeRaw`
          SELECT pg_advisory_xact_lock(hashtextextended(${lockKey}, 0::bigint))
        `;

        // Idempotency is per source event, not merely per event type: every
        // unique LIMIT_UPDATED revision remains visible while retries of the
        // same revision remain harmless.
        const cycleReceipt = await this.ensureCycle(
          tx,
          agentId,
          tradeId,
          eventBody.direction,
          eventBody,
        );
        if (!cycleReceipt) return { persisted: false, intentApplied: false };
        const { cycleId, intentApplied } = cycleReceipt;

        // The showcase ORDER_PLACED event is audit evidence, not proof that
        // this subscriber has an exchange order. Only the subscriber execution
        // path may transition a participant/cycle to PENDING_ENTRY after
        // Bitfinex has accepted its own order.
        if (eventBody.event === 'POSITION_CLOSED') {
          const cycle = await tx.signalCycle.findUnique({ where: { id: cycleId } });
          if (!cycle || cycle.status === SignalCycleStatus.CLOSED) {
            return { persisted: true, intentApplied, cycleId };
          }
          await tx.signalCycle.update({
            where: { id: cycleId },
            data: { status: SignalCycleStatus.CLOSED, closedAt: new Date() },
          });
        }

        return { persisted: true, intentApplied, cycleId };
      }),
    );
    if (!canonical.persisted || !canonical.cycleId) return canonical;

    onCanonicalPersisted?.(canonical);

    await this.withRelayPersistenceLock(lockKey, () =>
      this.prisma.$transaction(async (tx) => {
        await tx.$executeRaw`
          SELECT pg_advisory_xact_lock(hashtextextended(${lockKey}, 0::bigint))
        `;
        const already = await tx.signalCycleEvent.findFirst({
          where: {
            cycleId: canonical.cycleId,
            eventType: eventBody.event,
            payload: { path: ['event_id'], equals: eventId },
          },
          select: { id: true },
        });
        if (!already) {
          await tx.signalCycleEvent.create({
            data: {
              cycleId: canonical.cycleId,
              eventType: eventBody.event,
              payload: eventBody as unknown as Prisma.InputJsonValue,
            },
          });
        }
      }),
    );
    return { persisted: true, intentApplied: canonical.intentApplied };
  }

  private async withRelayPersistenceLock<T>(
    key: string,
    operation: () => Promise<T>,
  ): Promise<T> {
    const previous = this.relayPersistenceTails.get(key) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.then(() => gate);
    this.relayPersistenceTails.set(key, tail);
    await previous;
    try {
      return await operation();
    } finally {
      release();
      if (this.relayPersistenceTails.get(key) === tail) {
        this.relayPersistenceTails.delete(key);
      }
    }
  }

  /**
   * Find or create a signal cycle row for (agentId, tradeId). Returns the
   * cycle id. Best-effort — concurrent creates are de-duplicated via the
   * unique constraint on (agentId, tradeId).
   */
  private async ensureCycle(
    db: Prisma.TransactionClient,
    agentId: string,
    tradeId: string,
    direction?: string | null,
    body?: ShowcaseRelayEventBody,
  ): Promise<{ cycleId: string; intentApplied: boolean } | null> {
    // A cancel-race fill may relink cycle.tradeId while later signed events
    // continue to carry the original showcase id. Resolve the deterministic
    // primary key before attempting an insert; a caught unique violation would
    // leave the PostgreSQL transaction aborted (25P02).
    const raw = tradeId.toLowerCase().replace(/[^a-z0-9]/g, '').padEnd(8, '0');
    const cycleId = `cyc_rel_${raw.slice(0, 22)}`.slice(0, 30);
    let existing;
    if (typeof db.signalCycle.findFirst === 'function') {
      existing = await db.signalCycle.findFirst({
          where: {
            agentId,
            OR: [{ tradeId }, { id: cycleId }],
          },
          select: { id: true, agentId: true, intentEnvelope: true, status: true },
        });
    } else {
      existing = await db.signalCycle.findUnique({
        where: { agentId_tradeId: { agentId, tradeId } },
        select: { id: true, agentId: true, intentEnvelope: true, status: true },
      });
      if (!existing) {
        const stableIdMatch = await db.signalCycle.findUnique({
          where: { id: cycleId },
          select: { id: true, agentId: true, intentEnvelope: true, status: true },
        });
        if (stableIdMatch?.agentId === agentId) existing = stableIdMatch;
      }
    }
    if (existing) {
      const current = existing.intentEnvelope as
        | (Record<string, unknown> & {
            action?: unknown;
            entry?: Record<string, unknown>;
            context?: Record<string, unknown>;
          })
        | null;
      const signedIntent =
        body?.schema === 'dcf-showcase-intent-v1'
        && Boolean(body.platform_received_at)
        && (body.direction?.toUpperCase() === 'LONG'
          || body.direction?.toUpperCase() === 'SHORT');
      const carriesExactLimit =
        (body?.event === 'ORDER_PLACED' || body?.event === 'LIMIT_UPDATED')
        && body.executable === true
        && isExecutableEntryPolicy(body?.entry_limit_policy)
        && typeof body?.limit_price === 'number'
        && Number.isFinite(body.limit_price)
        && body.limit_price > 0
        && typeof body?.qty === 'number'
        && Number.isFinite(body.qty)
        && body.qty > 0;
      const carriesSignedClose =
        body?.event === 'POSITION_CLOSED'
        && Boolean(body.platform_received_at);
      const carriesSignedExpiry =
        body?.event === 'ORDER_EXPIRED'
        && Boolean(body.platform_received_at)
        && Boolean(body.source_expires_at);
      const applyExactLimit =
        carriesExactLimit
        && existing.status !== SignalCycleStatus.CLOSED
        && shouldApplyExactLifecycleUpdate(current, body);
      let intentApplied = Boolean(
        carriesExactLimit && exactLifecycleRevisionMatches(current, body ?? { event: 'APPROVE_PENDING' }),
      );
      if (
        signedIntent
        && (current?.action !== 'ENTER' || applyExactLimit || carriesSignedClose || carriesSignedExpiry)
      ) {
        const incoming = relayIntentEnvelope(existing.id, tradeId, body) as Record<
          string,
          unknown
        > & {
          entry?: Record<string, unknown>;
          context?: Record<string, unknown>;
        };
        const intentEnvelope =
          current?.action === 'ENTER' && (applyExactLimit || carriesSignedClose || carriesSignedExpiry)
            ? {
                ...current,
                direction: incoming.direction,
                version: incoming.version,
                entry: { ...current.entry, ...incoming.entry },
                context: { ...current.context, ...incoming.context },
              }
            : incoming;
        const claimExpired = canClaimExpiredCycleForCurrentGeneration({
          status: existing.status,
          current,
          incoming: body ?? { event: 'APPROVE_PENDING' },
        });
        await db.signalCycle.update({
          where: { id: existing.id },
          data: {
            intentEnvelope:
              intentEnvelope as unknown as import('@prisma/client').Prisma.InputJsonValue,
            botVersion: body.bot_version ?? undefined,
            // Platform TTL is a fallback anchored at initial activation. A
            // chase revision must not slide it; source expiry is persisted
            // separately in the signed lifecycle context.
            expiresAt: body.event === 'ORDER_PLACED'
              ? new Date(Date.now() + 1_800_000)
              : undefined,
            ...(claimExpired
              ? { status: SignalCycleStatus.INTENT, closedAt: null }
              : {}),
          },
        });
        intentApplied = Boolean(
          carriesExactLimit && exactLifecycleRevisionMatches(intentEnvelope, body ?? { event: 'APPROVE_PENDING' }),
        );
      }
      return { cycleId: existing.id, intentApplied };
    }

    // Stable-ish id for the cycle — derived from the trade_id so replays land
    // on the same row. Strip non-alphanumerics; pad/truncate to fit the cuid-ish shape.
    try {
      const createdEnvelope = relayIntentEnvelope(
        cycleId,
        tradeId,
        body ?? { event: 'APPROVE_PENDING', direction },
      );
      await db.signalCycle.create({
        data: {
          id: cycleId,
          agentId,
          tradeId,
          status: SignalCycleStatus.INTENT,
          intentEnvelope:
            createdEnvelope as unknown as import('@prisma/client').Prisma.InputJsonValue,
          researchVenue: 'bitfinex',
          botVersion: body?.bot_version ?? undefined,
          expiresAt: new Date(Date.now() + 1_800_000),
        },
      });
      return {
        cycleId,
        intentApplied: exactLifecycleRevisionMatches(
          createdEnvelope as RelayLifecycleEnvelope,
          body ?? { event: 'APPROVE_PENDING' },
        ),
      };
    } catch (err) {
      // Concurrent create race — re-fetch.
      const retry = await db.signalCycle.findUnique({
        where: { agentId_tradeId: { agentId, tradeId } },
        select: { id: true, intentEnvelope: true },
      });
      if (retry) {
        return {
          cycleId: retry.id,
          intentApplied: exactLifecycleRevisionMatches(
            retry.intentEnvelope as RelayLifecycleEnvelope,
            body ?? { event: 'APPROVE_PENDING' },
          ),
        };
      }
      throw err;
    }
  }
}
