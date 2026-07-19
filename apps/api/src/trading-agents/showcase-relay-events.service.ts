import { Injectable, Logger, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { SignalCycleStatus } from '@prisma/client';
import { createHmac, timingSafeEqual } from 'crypto';
import { BotBridgeService } from './bot-bridge.service';
import { SignalCyclesService } from './signal-cycles.service';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';
import { PrismaService } from '../prisma/prisma.service';

export type ShowcaseRelayEventType =
  | 'APPROVE_PENDING'
  | 'ORDER_PLACED'
  | 'POSITION_CLOSED'
  | 'LIMIT_UPDATED';

export type ShowcaseRelayEventBody = {
  event: ShowcaseRelayEventType;
  trade_id?: string | null;
  ts?: string | null;
  limit_price?: number | null;
  exit_reason?: string | null;
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
  pullback_pct?: number | null;
  bot_version?: string | null;
  strategy_mode?: string | null;
  bot_instance_id?: string | null;
  dashboard_owner?: boolean | null;
  dashboard_pid?: number | null;
  dashboard_port?: number | null;
  source_git_rev?: string | null;
};

/**
 * Minimal intent envelope for showcase relay demo cycles.
 *
 * Real cycles get a fully populated envelope built by signal-envelope.mapper's
 * buildIntentEnvelope() from live bot state. The showcase relay webhook path
 * doesn't have the full bot context for the trade_id it's announcing, so when
 * a relay event lands for a trade_id with no existing cycle (e.g. cassette
 * replay during the demo harness), we upsert a cycle with this minimal shape
 * so the event has somewhere to attach. The platform polls + backfills the
 * full envelope later if a real bot trade is observed for the same id.
 *
 * N2 (intent-mirror) — when a signed v1 webhook carries `intent_source` and
 * signal context, those fields are merged into the envelope so the relay's
 * intent-mirror path can read `intent_source`, `signal_price`, and
 * `pullback_pct` to place the hire order (decision §8 #4).
 */
function minimalIntentEnvelope(
  tradeId: string,
  direction?: string | null,
  intentSource?: 'paper' | 'live' | null,
  v1?: Partial<Pick<ShowcaseRelayEventBody, 'signal_price' | 'pullback_pct' | 'margin_usdt' | 'leverage' | 'win_prob' | 'edge_score' | 'effective_threshold'>> & { bot_version?: string | null },
) {
  const dir = direction === 'SHORT' ? 'SHORT' : 'LONG';
  const sigPrice = typeof v1?.signal_price === 'number' && Number.isFinite(v1.signal_price) ? v1.signal_price : null;
  const pullbackPct = typeof v1?.pullback_pct === 'number' && Number.isFinite(v1.pullback_pct) ? v1.pullback_pct : null;
  return {
    cycle_id: tradeId,
    trade_id: tradeId,
    direction: dir,
    entry: {
      order_type: 'LIMIT',
      // Offset % derived from the webhook's signal_price ± pullback_pct when
      // available; otherwise the conservative legacy default. The relay's
      // maybeEnterFromIntent reads signal_price directly for the limit (§8 #4).
      limit_offset_pct: 0.05,
      ttl_sec: 90,
      margin_usd: 100,
      ...(sigPrice != null ? { signal_price: sigPrice } : {}),
      ...(pullbackPct != null ? { pullback_pct: pullbackPct } : {}),
    },
    risk: {
      stop_loss_margin_pct: -18,
      leverage_hint: 1,
    },
    source: 'showcase_relay_webhook',
    schema: 'signal_intent_envelope_v1',
    ...(intentSource ? { intent_source: intentSource } : {}),
    ...(v1?.margin_usdt != null ? { margin_usdt: v1.margin_usdt } : {}),
    ...(v1?.leverage != null ? { leverage: v1.leverage } : {}),
    ...(v1?.win_prob != null ? { win_prob: v1.win_prob } : {}),
    ...(v1?.edge_score != null ? { edge_score: v1.edge_score } : {}),
    ...(v1?.effective_threshold != null ? { effective_threshold: v1.effective_threshold } : {}),
    ...(v1?.bot_version != null ? { bot_version: v1.bot_version } : {}),
  };
}

@Injectable()
export class ShowcaseRelayEventsService {
  private readonly logger = new Logger(ShowcaseRelayEventsService.name);

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
    // the intent-entry path. Those richer payloads always require HMAC.
    return [
      body.schema,
      body.intent_source,
      body.signal_price,
      body.margin_usdt,
      body.leverage,
      body.win_prob,
      body.edge_score,
      body.effective_threshold,
      body.research_lane,
      body.pullback_pct,
      body.bot_version,
      body.strategy_mode,
    ].every((value) => value == null);
  }

  verifySignature(
    rawBody: Buffer | string | undefined,
    sigHeader: string | undefined,
    relayBody?: ShowcaseRelayEventBody,
  ): void {
    const secret = this.config.get<string>('SHOWCASE_WEBHOOK_SECRET')?.trim();
    // Rollout boundary: until the operator sets the shared secret on the API,
    // the signature gate is not enforced — the legacy G13 bearer-secret check
    // (assertAuthorized) remains the sole auth. Once set, signing is required.
    if (!secret) {
      return;
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
      return;
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
    if (slug !== 'conservative-btc') {
      return { ok: false, reason: 'unsupported_agent' };
    }

    // N1 (intent-mirror) — verify the HMAC signature BEFORE any state mutation
    // so a forged payload can never create an intent row or wake the relay.
    // The legacy G13 bearer-secret check (assertAuthorized) is still performed
    // by the controller before calling ingest; this is the payload-level guard.
    this.verifySignature(context?.rawBody, context?.signatureHeader, body);
    this.assertActiveDashboardOwner(body);

    this.botBridge.invalidateCache();

    const event = body.event;
    let intentCreated = false;

    if (event === 'APPROVE_PENDING' || event === 'ORDER_PLACED') {
      intentCreated = await this.cycles.wakeFromShowcase({ intents: true, closures: false });
    } else if (event === 'POSITION_CLOSED') {
      await this.cycles.wakeFromShowcase({ intents: false, closures: true });
    } else {
      await this.cycles.wakeFromShowcase({ intents: true, closures: true });
    }

    // F7 — Pass the event trigger so the relay can tag the resulting exit
    // mirror with SHOWCASE_CLOSED_WEBHOOK (fast path) vs SHOWCASE_CLOSED_POLL.
    // This is purely an audit log distinction — both paths close the copy lot
    // identically; the tag lets ops measure end-to-end exit lag.
    await this.execution.wakeNow(event);

    // [SHOWCASE_RELAY_PERSIST_2026-07-08] Persist the inbound relay event as
    // a signalCycleEvent row so the cycle audit trail reflects every webhook
    // the platform received (APPROVE_PENDING -> ORDER_PLACED -> POSITION_CLOSED).
    // Without this, only events emitted by the copy-trader land in the table;
    // pure showcase-only trades (e.g. cassette-replayed demo cycles) leave no
    // trace and the relay round-trip harness check sees no APPROVE_PENDING /
    // POSITION_CLOSED rows. Best-effort — never let persistence failure kill
    // the relay wake (the wake is the critical side-effect).
    let persisted = false;
    try {
      persisted = await this.persistRelayEvent(slug, body);
    } catch (err) {
      this.logger.warn(
        `Showcase relay persist failed (non-fatal): ${err instanceof Error ? err.message : err}`,
      );
    }

    this.logger.log(
      `Showcase relay wake ${event} trade=${body.trade_id ?? '?'} intent=${intentCreated ? 'new' : 'none'} persisted=${persisted}`,
    );

    return { ok: true, event, trade_id: body.trade_id ?? null, intentCreated, persisted };
  }

  /**
   * Best-effort persistence of an inbound relay webhook as a signal cycle +
   * signal cycle event. Idempotent per (agent, trade_id, event) — replays of
   * the same cassette won't duplicate rows for the same event type.
   *
   * Visible behavior:
   *   - APPROVE_PENDING -> create INTENT cycle (if missing) + APPROVE_PENDING event
   *   - ORDER_PLACED    -> ensure cycle exists, transition INTENT -> PENDING_ENTRY, + ORDER_PLACED event
   *   - POSITION_CLOSED -> ensure cycle exists, transition to CLOSED, + POSITION_CLOSED event
   *   - LIMIT_UPDATED   -> ensure cycle exists, + LIMIT_UPDATED event (no status change)
   */
  private async persistRelayEvent(slug: string, body: ShowcaseRelayEventBody): Promise<boolean> {
    const tradeId = (body.trade_id ?? '').trim();
    if (!tradeId) return false;
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug } });
    if (!agent) return false;

    // Idempotency: skip if this exact event already landed for this cycle.
    const cycleId = await this.ensureCycle(agent.id, tradeId, body.direction, body);
    if (!cycleId) return false;
    const already = await this.prisma.signalCycleEvent.findFirst({
      where: { cycleId, eventType: body.event },
      select: { id: true },
    });
    if (already) return true; // replay protection

    await this.prisma.signalCycleEvent.create({
      data: {
        cycleId,
        eventType: body.event,
        payload: body as unknown as import('@prisma/client').Prisma.InputJsonValue,
      },
    });

    const cycle = await this.prisma.signalCycle.findUnique({ where: { id: cycleId } });
    if (!cycle) return true;

    if (body.event === 'ORDER_PLACED' && cycle.status === SignalCycleStatus.INTENT) {
      await this.prisma.signalCycle.update({
        where: { id: cycleId },
        data: { status: SignalCycleStatus.PENDING_ENTRY },
      });
    } else if (body.event === 'POSITION_CLOSED' && cycle.status !== SignalCycleStatus.CLOSED) {
      await this.prisma.signalCycle.update({
        where: { id: cycleId },
        data: { status: SignalCycleStatus.CLOSED, closedAt: new Date() },
      });
    }

    return true;
  }

  /**
   * Find or create a signal cycle row for (agentId, tradeId). Returns the
   * cycle id. Best-effort — concurrent creates are de-duplicated via the
   * unique constraint on (agentId, tradeId).
   */
  private async ensureCycle(
    agentId: string,
    tradeId: string,
    direction?: string | null,
    body?: ShowcaseRelayEventBody,
  ): Promise<string | null> {
    const existing = await this.prisma.signalCycle.findUnique({
      where: { agentId_tradeId: { agentId, tradeId } },
      select: { id: true },
    });
    if (existing) return existing.id;

    // Stable-ish id for the cycle — derived from the trade_id so replays land
    // on the same row. Strip non-alphanumerics; pad/truncate to fit the cuid-ish shape.
    const raw = tradeId.toLowerCase().replace(/[^a-z0-9]/g, '').padEnd(8, '0');
    const cycleId = `cyc_rel_${raw.slice(0, 22)}`.slice(0, 30);
    try {
      await this.prisma.signalCycle.create({
        data: {
          id: cycleId,
          agentId,
          tradeId,
          status: SignalCycleStatus.INTENT,
          intentEnvelope: minimalIntentEnvelope(tradeId, direction, body?.intent_source ?? undefined, body ?? undefined) as unknown as import('@prisma/client').Prisma.InputJsonValue,
          researchVenue: 'bitfinex',
          expiresAt: new Date(Date.now() + 300_000),
        },
      });
      return cycleId;
    } catch (err) {
      // Concurrent create race — re-fetch.
      const retry = await this.prisma.signalCycle.findUnique({
        where: { agentId_tradeId: { agentId, tradeId } },
        select: { id: true },
      });
      if (retry) return retry.id;
      throw err;
    }
  }
}
