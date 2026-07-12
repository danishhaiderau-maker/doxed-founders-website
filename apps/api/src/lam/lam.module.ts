import { Module } from '@nestjs/common';
import { LamController } from './lam.controller';
import { LamOrchestratorService } from './lam-orchestrator.service';
import { BrowserAdapter } from './browser.adapter';
import { ComputerUseAdapter } from './computer-use.adapter';
import { LamScheduler } from './lam.scheduler';
import { PrismaModule } from '../prisma/prisma.module';
import { AiProxyModule } from '../ai-proxy/ai-proxy.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';

/**
 * LAM (Large Action Model) module — Phase 9.
 *
 * The "hands" layer. Composes:
 *   - BrowserAdapter (Playwright, all tiers)
 *   - ComputerUseAdapter (Claude Computer Use, premium / Doxxed tier)
 *   - LamOrchestratorService (plan → execute → synthesize, via AI Gateway)
 *   - LamController (REST surface under /api/lam/*)
 *
 * Depends on the AI Gateway (AiProxyModule) for planning + synthesis
 * model calls, the Flight Recorder for the action trace, and Prisma
 * for the tier gate lookup. No application-code imports — this is a
 * kernel-composed service, same boundary as the Idea Validator.
 *
 * Kill switch: COMPUTER_USE_ENABLED gates the premium adapter only.
 * The browser adapter and orchestrator are always on.
 */
@Module({
  imports: [PrismaModule, AiProxyModule, FlightRecorderModule],
  controllers: [LamController],
  providers: [LamOrchestratorService, LamScheduler, BrowserAdapter, ComputerUseAdapter],
  exports: [LamOrchestratorService, BrowserAdapter, ComputerUseAdapter],
})
export class LamModule {}
