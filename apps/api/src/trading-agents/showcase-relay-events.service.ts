import { Injectable, Logger, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
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

@Injectable()
export class ShowcaseRelayEventsService {
  private readonly logger = new Logger(ShowcaseRelayEventsService.name);

  constructor(
    private readonly config: ConfigService,
    private readonly botBridge: BotBridgeService,
    private readonly cycles: SignalCyclesService,
    private readonly execution: SignalSubscriberExecutionService,
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

    this.logger.log(
      `Showcase relay wake ${event} trade=${body.trade_id ?? '?'} intent=${intentCreated ? 'new' : 'none'}`,
    );

    return { ok: true, event, trade_id: body.trade_id ?? null, intentCreated };
  }
}
