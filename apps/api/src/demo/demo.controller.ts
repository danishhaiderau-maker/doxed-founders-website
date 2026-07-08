import { Body, Controller, Get, Post, Query, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { Public } from '../auth/public.decorator';
import { DemoModeGuard } from './demo-mode.guard';
import { DemoSeedService } from './demo-seed.service';
import { DemoHarnessService } from './demo-harness.service';
import { DemoStressService } from './demo-stress.service';
import { isDemoModeEnabled } from './demo.constants';
import type { ReadinessScorecard } from './readiness-scorecard.types';

@SkipThrottle()
@Controller('admin/demo')
@UseGuards(AdminGuard)
export class DemoController {
  constructor(
    private readonly demo: DemoSeedService,
    private readonly harness: DemoHarnessService,
    private readonly stress: DemoStressService,
  ) {}

  @Post('seed')
  @UseGuards(DemoModeGuard)
  seed() {
    return this.demo.seedEcosystem();
  }

  @Post('reset')
  @UseGuards(DemoModeGuard)
  reset() {
    return this.demo.resetDemoData();
  }

  @Get('status')
  status() {
    return this.demo.getStatus();
  }

  @Post('smoke')
  @UseGuards(DemoModeGuard)
  runSmoke() {
    return this.demo.runSmokeChecks();
  }

  @Get('smoke')
  @UseGuards(DemoModeGuard)
  runSmokeGet() {
    return this.demo.runSmokeChecks();
  }

  /**
   * Full extended smoke run — existing 25 platform checks PLUS the new
   * bot/analyzer/genome/relay/AI/founder checks. Returns the unified
   * readiness scorecard. Stress phase is skipped (use POST /stress or the
   * orchestrator --stress flag).
   */
  @Post('smoke/full')
  @UseGuards(DemoModeGuard)
  runFullSmoke(): Promise<ReadinessScorecard> {
    return this.harness.runFull({ skipStress: true });
  }

  @Get('smoke/full')
  @UseGuards(DemoModeGuard)
  runFullSmokeGet(): Promise<ReadinessScorecard> {
    return this.harness.runFull({ skipStress: true });
  }

  /**
   * Full harness including stress phase. This is what `scripts/run-demo.cmd`
   * invokes as its final step.
   *
   * [DEMO_HARNESS_FIX_2026-07-08] Forwards body.skipStress ?? false so callers
   * that POST a JSON body can request a non-stress run via the same route.
   */
  @Post('harness')
  @UseGuards(DemoModeGuard)
  runHarness(
    @Query('stressRps') stressRps?: string,
    @Query('stressDurationS') stressDurationS?: string,
    @Body() body?: { skipStress?: boolean; stressRps?: number; stressDurationS?: number },
  ): Promise<ReadinessScorecard> {
    return this.harness.runFull({
      stressRps: stressRps ? Number(stressRps) : body?.stressRps,
      stressDurationS: stressDurationS ? Number(stressDurationS) : body?.stressDurationS,
      skipStress: body?.skipStress ?? false,
    });
  }

  @Get('harness/quick')
  quickStatus() {
    return this.harness.quickStatus();
  }

  @Post('stress')
  @UseGuards(DemoModeGuard)
  runStress(
    @Query('rps') rps?: string,
    @Query('durationS') durationS?: string,
    @Body() body?: { rps?: number; durationS?: number },
  ) {
    return this.stress.run({
      rps: rps ? Number(rps) : body?.rps,
      durationS: durationS ? Number(durationS) : body?.durationS,
    });
  }

  /**
   * Internal harness token route — used by the orchestrator (Node) when it
   * can't easily obtain an admin JWT. Guarded by DemoModeGuard (DEMO_MODE_ENABLED=true)
   * PLUS a shared secret (DEMO_HARNESS_TOKEN). Intentionally Public() so no
   * JWT is required; the secret gate replaces it.
   *
   * Inert when DEMO_MODE_ENABLED is not true (refuses even with the token).
   */
  @Public()
  @Post('harness/internal')
  runHarnessInternal(
    @Query('token') token: string | undefined,
    @Body() body?: { token?: string; stressRps?: number; stressDurationS?: number; skipStress?: boolean },
  ): Promise<ReadinessScorecard> {
    assertHarnessToken(token ?? body?.token);
    return this.harness.runFull({
      stressRps: body?.stressRps,
      stressDurationS: body?.stressDurationS,
      skipStress: body?.skipStress,
    });
  }
}

function assertHarnessToken(provided: string | undefined): void {
  if (!isDemoModeEnabled()) {
    throw new Error('DEMO_MODE_ENABLED must be true to use the internal harness route.');
  }
  const expected = (process.env.DEMO_HARNESS_TOKEN ?? '').trim();
  if (!expected) {
    throw new Error('DEMO_HARNESS_TOKEN not configured on the API service.');
  }
  const given = (provided ?? '').trim();
  if (given.length !== expected.length) {
    throw new Error('Invalid DEMO_HARNESS_TOKEN');
  }
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) {
    diff |= expected.charCodeAt(i) ^ given.charCodeAt(i);
  }
  if (diff !== 0) {
    throw new Error('Invalid DEMO_HARNESS_TOKEN');
  }
}
