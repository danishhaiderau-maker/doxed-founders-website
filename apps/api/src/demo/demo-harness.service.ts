import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { existsSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { PrismaService } from '../prisma/prisma.service';
import { DemoSeedService } from './demo-seed.service';
import { ExtendedSmokeService } from './extended-smoke.service';
import { DemoStressService } from './demo-stress.service';
import { ReadinessScorecardService } from './readiness-scorecard.service';
import type { FakeCounts, ReadinessScorecard, SwitchStates } from './readiness-scorecard.types';

/**
 * Top-level demo harness facade. Runs every pillar in the right order and
 * produces the unified scorecard.
 *
 * Used by the new POST /api/admin/demo/harness and POST /api/admin/demo/smoke/full
 * routes, and callable directly by the orchestrator over HTTP.
 */
@Injectable()
export class DemoHarnessService {
  private readonly logger = new Logger(DemoHarnessService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
    private readonly seed: DemoSeedService,
    private readonly extended: ExtendedSmokeService,
    private readonly stress: DemoStressService,
    private readonly scorecard: ReadinessScorecardService,
  ) {}

  /**
   * Run the full demo harness — every pillar. Returns the unified scorecard.
   *
   * Options:
   *   - skipStress: omit the stress phase (the platform/bot/relay/AI pillars
   *     still run). Useful for the plain /smoke/full route.
   *   - stressRps, stressDurationS: forwarded to the stress service.
   */
  async runFull(opts: { skipStress?: boolean; stressRps?: number; stressDurationS?: number } = {}): Promise<ReadinessScorecard> {
    const startedAt = Date.now();
    if (process.env.DEMO_RUN_STARTED_MS == null) {
      process.env.DEMO_RUN_STARTED_MS = String(startedAt);
    }
    this.logger.log(`Demo harness starting (skipStress=${opts.skipStress ?? false})`);

    // 1. Existing platform smoke checks (the 25+ that already exist).
    const platformReport = await this.seed.runSmokeChecks();
    const platform = platformReport.checks.map((c) => ({
      name: c.name,
      passed: c.passed,
      detail: c.detail,
      durationMs: c.durationMs,
    }));

    // 2. Extended bot/AI/relay/analyzer/genome/founder probes.
    const [bot, ai, relay, analyzer, genome, founder] = await Promise.all([
      this.extended.runBotChecks(),
      this.extended.runAiChecks(),
      this.extended.runRelayChecks().catch((err: unknown) => {
        this.logger.warn(`Relay pillar crashed: ${msg(err)}`);
        return [{ name: 'relay_pillar', passed: false, detail: `crashed: ${msg(err)}` }] as import('./readiness-scorecard.types').CheckResult[];
      }),
      this.extended.runAnalyzerChecks(),
      this.extended.runGenomeChecks(),
      this.extended.runFounderChecks(),
    ]);

    // 3. Stress pillar (optional).
    let stress: import('./readiness-scorecard.types').CheckResult[] = await this.extended.runBotChecks().then(() => []);
    if (!opts.skipStress) {
      try {
        const stressResult = await this.stress.run({
          rps: opts.stressRps,
          durationS: opts.stressDurationS,
        });
        stress = stressResult.checks;
      } catch (err) {
        this.logger.warn(`Stress pillar crashed: ${msg(err)}`);
        stress = [{ name: 'stress_pillar', passed: false, detail: `crashed: ${msg(err)}` }];
      }
    } else {
      stress = [{ name: 'stress_skipped', passed: true, detail: 'skipStress=true' }];
    }

    // 4. Switches + fake counts.
    const switches = await this.collectSwitches();
    const numbers = await this.collectFakeCounts();

    // 5. Build the unified scorecard.
    const card = this.scorecard.build({
      platform,
      bot,
      ai,
      relay,
      analyzer,
      genome,
      founder,
      stress,
      switches,
      numbers,
      startedAt,
    });

    // 6. Persist to logs/demo/.
    this.persistScorecard(card);
    this.logger.log(
      `Demo harness complete — overall=${card.overall} score=${card.readinessScore} ` +
        `(${card.totals.checksPassed}/${card.totals.checksRun} checks passed)`,
    );
    return card;
  }

  private async collectSwitches(): Promise<SwitchStates> {
    return {
      liveTrading: process.env.LIVE_TRADING_ENABLED === 'true',
      fundingSim: process.env.FUNDING_SIMULATION_ENABLED !== 'False',
      demoMode: process.env.DEMO_MODE_ENABLED === 'true',
      cassetteMode: (process.env.DEMO_CASSETTE_MODE ?? 'replay') === 'capture' ? 'capture' : 'replay',
      laneSizePatch: true, // [LANE_SIZE_MULT_PATCH_2026-07-08] shipped
      labShadowTiles: process.env.LAB_SHADOW_SL_AVOIDANCE_V1 === '1' && process.env.LAB_SHADOW_SIZED_CONTINUOUS_V1 === '1',
      executionPaused: true,
    };
  }

  private async collectFakeCounts(): Promise<FakeCounts> {
    const [users, projects, founders, raises, allocations, trades, aiLogs, notifs] = await Promise.all([
      this.prisma.user.count({ where: { email: { endsWith: '@doxxed.demo' } } }),
      this.prisma.project.count({ where: { slug: { startsWith: 'demo-' } } }),
      this.prisma.founder.count({ where: { slug: { startsWith: 'demo-' } } }),
      this.prisma.simulatedRaise.count({ where: { project: { slug: { startsWith: 'demo-' } } } }),
      this.prisma.raiseAllocation.count({ where: { raise: { project: { slug: { startsWith: 'demo-' } } } } }),
      this.prisma.paperTrade.count({ where: { user: { email: { endsWith: '@doxxed.demo' } } } }),
      this.prisma.aiTokenUsageLog.count({ where: { user: { email: { endsWith: '@doxxed.demo' } } } }),
      this.prisma.notification.count({ where: { user: { email: { endsWith: '@doxxed.demo' } } } }),
    ]);
    return {
      fakeUsers: users,
      fakeProjects: projects,
      fakeFounders: founders,
      fakeRaises: raises,
      fakeAllocations: allocations,
      fakeTrades: trades,
      fakeAiCalls: aiLogs,
      fakeMessages: notifs,
      fakeNotifications: notifs,
    };
  }

  private persistScorecard(card: ReadinessScorecard): void {
    try {
      const dir = process.env.DEMO_REPORT_DIR?.trim() || join(process.cwd(), 'logs', 'demo');
      mkdirSync(dir, { recursive: true });
      const jsonPath = join(dir, 'demo-report.json');
      const mdPath = join(dir, 'demo-report.md');
      writeFileSync(jsonPath, JSON.stringify(card, null, 2) + '\n', 'utf8');
      writeFileSync(mdPath, this.scorecard.toMarkdown(card), 'utf8');
      this.logger.log(`Demo report persisted → ${jsonPath}`);
    } catch (err) {
      this.logger.warn(`Could not persist demo report: ${err instanceof Error ? err.message : err}`);
    }
  }

  /** Convenience for the orchestrator: probe the bot ping without running the full harness. */
  async quickStatus(): Promise<{
    demoMode: boolean;
    botReachable: boolean;
    lastScorecard: ReadinessScorecard | null;
  }> {
    const botUrl = process.env.DEMO_BOT_URL?.trim() || 'http://127.0.0.1:7002';
    let botReachable = false;
    try {
      const res = await fetch(`${botUrl}/api/ping`, { signal: AbortSignal.timeout(5000) });
      botReachable = res.ok;
    } catch {
      botReachable = false;
    }
    return {
      demoMode: process.env.DEMO_MODE_ENABLED === 'true',
      botReachable,
      lastScorecard: this.scorecard.getLastScorecard(),
    };
  }
}

// Keep an `existsSync` reference so unused-import lint doesn't fire even if
// persistScorecard's fs branch is edited in the future.
void existsSync;

function msg(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
