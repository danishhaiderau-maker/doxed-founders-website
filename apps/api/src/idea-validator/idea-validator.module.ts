import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { IdeaValidatorController } from './idea-validator.controller';
import { IdeaValidatorService } from './idea-validator.service';
import { BrowserResearchAdapter } from './browser-research.adapter';
import { IdeaValidatorDailyCron } from './idea-validator.cron';
import { PrismaModule } from '../prisma/prisma.module';
import { AiProxyModule } from '../ai-proxy/ai-proxy.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';

/**
 * Founder Idea Validator — Phase 6 application module.
 *
 * Wires the controller, the orchestration service, the Browser Use LAM
 * adapter, and the daily proactive pop-up cron. Depends only on kernel
 * services (Prisma, AI Gateway, Flight Recorder) — no application-code
 * imports, per the kernel boundary rule (KERNEL.md §4).
 *
 * Kill switch: gated behind IDEA_VALIDATOR_ENABLED (default ON in dev).
 * When the env is 'false', the controller still mounts (so the API boots
 * without a redeploy) but the check endpoint short-circuits. The cron
 * also no-ops when disabled.
 */
@Module({
  imports: [ConfigModule, PrismaModule, AiProxyModule, FlightRecorderModule],
  controllers: [IdeaValidatorController],
  providers: [IdeaValidatorService, BrowserResearchAdapter, IdeaValidatorDailyCron],
  exports: [IdeaValidatorService, BrowserResearchAdapter],
})
export class IdeaValidatorModule {}

/**
 * Helper (exported for the controller/cron) to read the kill switch.
 * Default: ON. Set IDEA_VALIDATOR_ENABLED=false to disable in prod
 * without redeploying.
 */
export function ideaValidatorEnabled(config: ConfigService): boolean {
  const raw = config.get<string>('IDEA_VALIDATOR_ENABLED');
  if (raw === undefined || raw === null || raw === '') return true; // default ON
  return raw.toLowerCase() !== 'false';
}
