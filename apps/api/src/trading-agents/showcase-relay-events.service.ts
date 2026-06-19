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
    if (secretHeader?.trim() !== expected) {
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

    await this.execution.wakeNow();

    this.logger.log(
      `Showcase relay wake ${event} trade=${body.trade_id ?? '?'} intent=${intentCreated ? 'new' : 'none'}`,
    );

    return { ok: true, event, trade_id: body.trade_id ?? null, intentCreated };
  }
}
