import { Controller, Get, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';

/**
 * Read-only, exchange-side observability endpoint consumed by the operator's
 * local self-aware layer. Exposes in-process WS transport telemetry, per-order
 * fill latency, nonce-lane depths, and a best-effort active order/position
 * snapshot for every live Bitfinex relay instance. JWT + admin-gated; never
 * mutates order flow or arm state.
 */
@SkipThrottle()
@Controller('exchanges')
export class ExchangeTelemetryController {
  constructor(private readonly execution: SignalSubscriberExecutionService) {}

  @UseGuards(AdminGuard)
  @Get('bitfinex/telemetry')
  telemetry() {
    return this.execution.getExchangeTelemetry();
  }
}
