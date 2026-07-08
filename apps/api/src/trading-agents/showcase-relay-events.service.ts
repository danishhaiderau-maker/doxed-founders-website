import { Injectable, Logger, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { SignalCycleStatus } from '@prisma/client';
import { BotBridgeService } from './bot-bridge.service';
import { SignalCyclesService } from './signal-cycles.service';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';

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
 */
function minimalIntentEnvelope(tradeId: string, direction?: string | null) {
  const dir = direction === 'SHORT' ? 'SHORT' : 'LONG';
  return {
    cycle_id: tradeId,
    trade_id: tradeId,
    direction: dir,
    entry: {
      order_type: 'LIMIT',
      limit_offset_pct: 0.05,
      ttl_sec: 90,
      margin_usd: 100,
    },
    risk: {
      stop_loss_margin_pct: -18,
      leverage_hint: 1,
    },
    source: 'showcase_relay_webhook',
    schema: 'signal_intent_envelope_v1',
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
    private readonly prisma: import('../prisma/prisma.service').PrismaService,
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

  async ingest(slug: string, body: ShowcaseRelayEventBody) {
    if (slug !== 'conservative-btc') {
      return { ok: false, reason: 'unsupported_agent' };
    }

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
    const cycleId = await this.ensureCycle(agent.id, tradeId, body.direction);
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
          intentEnvelope: minimalIntentEnvelope(tradeId, direction) as unknown as import('@prisma/client').Prisma.InputJsonValue,
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
