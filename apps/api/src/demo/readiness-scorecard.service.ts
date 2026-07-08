import { Injectable, Logger } from '@nestjs/common';
import { ObservatoryService } from '../observatory/observatory.service';
import {
  type CheckResult,
  type FakeCounts,
  type ReadinessScorecard,
  type SwitchStates,
  READINESS_DEGRADED_THRESHOLD,
  READINESS_PASS_THRESHOLD,
} from './readiness-scorecard.types';

/**
 * Unified readiness scorecard — aggregates EVERY pillar of the platform
 * (platform / bot / analyzer / genome / relay / ai / founder / stress) into
 * a single object with a readiness score 0-100 and an overall
 * PASS/FAIL/DEGRADED verdict.
 *
 * The scorecard is built by the demo harness from the outputs of:
 *   - DemoSeedService.runSmokeChecks()         (existing platform checks)
 *   - DemoSeedService.runExtendedSmokeChecks() (new bot/relay/ai/genome/founder checks)
 *   - DemoStressService.run()                   (stress probes)
 *
 * It is pushed to Observatory via setLastDemoScorecard() and persisted to
 * logs/demo/demo-report.{json,md}.
 */
@Injectable()
export class ReadinessScorecardService {
  private readonly logger = new Logger(ReadinessScorecardService.name);
  private lastScorecard: ReadinessScorecard | null = null;

  /**
   * Build a unified scorecard from pillar reports produced by callers.
   * Each pillar is responsible for its own checks; this just aggregates.
   */
  build(params: {
    platform: CheckResult[];
    bot: CheckResult[];
    analyzer: CheckResult[];
    genome: CheckResult[];
    relay: CheckResult[];
    ai: CheckResult[];
    founder: CheckResult[];
    stress: CheckResult[];
    switches: SwitchStates;
    numbers: FakeCounts;
    startedAt: number;
  }): ReadinessScorecard {
    const pillars = {
      platform: pillarFromChecks(params.platform),
      bot: pillarFromChecks(params.bot, this.extractBotNumbers(params.bot)),
      analyzer: pillarFromChecks(params.analyzer),
      genome: pillarFromChecks(params.genome),
      relay: pillarFromChecks(params.relay, this.extractRelayNumbers(params.relay)),
      ai: pillarFromChecks(params.ai, this.extractAiNumbers(params.ai)),
      founder: pillarFromChecks(params.founder),
      stress: pillarFromChecks(params.stress, this.extractStressNumbers(params.stress)),
    };
    const weights: Record<keyof typeof pillars, number> = {
      platform: 0.2,
      bot: 0.18,
      analyzer: 0.1,
      genome: 0.07,
      relay: 0.15,
      ai: 0.1,
      founder: 0.1,
      stress: 0.1,
    };
    const readinessScore = Math.round(
      (Object.entries(pillars) as [keyof typeof pillars, PillarView][]).reduce(
        (sum, [key, pillar]) => sum + pillar.score * weights[key],
        0,
      ),
    );
    const overall: ReadinessScorecard['overall'] = readinessScore >= READINESS_PASS_THRESHOLD
      ? 'PASS'
      : readinessScore >= READINESS_DEGRADED_THRESHOLD
        ? 'DEGRADED'
        : 'FAIL';
    const allChecks = Object.values(pillars).flatMap((p) => p.checks);
    const totals = {
      checksRun: allChecks.length,
      checksPassed: allChecks.filter((c) => c.passed).length,
      checksFailed: allChecks.filter((c) => !c.passed).length,
    };
    const scorecard: ReadinessScorecard = {
      overall,
      readinessScore,
      generatedAt: new Date().toISOString(),
      durationMs: Date.now() - params.startedAt,
      pillars,
      switches: params.switches,
      numbers: params.numbers,
      totals,
    };
    this.lastScorecard = scorecard;
    ObservatoryService.setLastDemoScorecard(scorecard);
    return scorecard;
  }

  getLastScorecard(): ReadinessScorecard | null {
    return this.lastScorecard;
  }

  /** Pull numeric metrics out of check details for the scorecard's pillar.numbers. */
  private extractBotNumbers(checks: CheckResult[]): Record<string, number | string | boolean | null> {
    const out: Record<string, number | string | boolean | null> = {};
    for (const c of checks) {
      if (c.name === 'bot_paper_orders_placed' && c.passed) out.paperOrders = c.detail;
      if (c.name === 'lane_size_patch_active' && c.passed) out.laneSizePatchActive = true;
    }
    return out;
  }

  private extractRelayNumbers(checks: CheckResult[]): Record<string, number | string | boolean | null> {
    const out: Record<string, number | string | boolean | null> = {};
    for (const c of checks) {
      if (c.name === 'relay_cycle_completes') out.cyclesCompleted = c.detail;
      if (c.name === 'relay_approve_pending_received') out.approvePending = c.detail;
      if (c.name === 'relay_order_placed_received') out.orderPlaced = c.detail;
      if (c.name === 'relay_position_closed_received') out.positionClosed = c.detail;
    }
    return out;
  }

  private extractAiNumbers(checks: CheckResult[]): Record<string, number | string | boolean | null> {
    const out: Record<string, number | string | boolean | null> = {};
    for (const c of checks) {
      if (c.name === 'ai_verdicts_emitted') out.verdicts = c.detail;
      if (c.name === 'ai_latency_within_budget') out.latencyMsMedian = c.detail;
    }
    return out;
  }

  private extractStressNumbers(checks: CheckResult[]): Record<string, number | string | boolean | null> {
    const out: Record<string, number | string | boolean | null> = {};
    for (const c of checks) {
      if (c.name === 'stress_peak_rps') out.peakRps = c.detail;
      if (c.name === 'stress_p95_latency') out.p95LatencyMs = c.detail;
      if (c.name === 'stress_error_count') out.errors = c.detail;
      if (c.name === 'stress_ddollar_invariant') out.ddollarInvariantHeld = c.passed;
    }
    return out;
  }

  /** Render the scorecard as a markdown report (QME-tile-style backtest tables). */
  toMarkdown(s: ReadinessScorecard): string {
    const lines: string[] = [];
    lines.push(`# End-to-End Demo Readiness Report`);
    lines.push('');
    lines.push(`| Field | Value |`);
    lines.push(`|---|---|`);
    lines.push(`| Generated | ${s.generatedAt} |`);
    lines.push(`| Duration | ${(s.durationMs / 1000).toFixed(1)}s |`);
    lines.push(`| Overall | **${s.overall}** |`);
    lines.push(`| Readiness Score | **${s.readinessScore}/100** |`);
    lines.push(`| Checks | ${s.totals.checksPassed}/${s.totals.checksRun} passed (${s.totals.checksFailed} failed) |`);
    lines.push('');
    lines.push(`## Pillar scores`);
    lines.push('');
    lines.push(`| Pillar | Score | Passed | Failed |`);
    lines.push(`|---|---:|---:|---:|`);
    for (const [name, pillar] of Object.entries(s.pillars)) {
      const passed = pillar.checks.filter((c) => c.passed).length;
      const failed = pillar.checks.length - passed;
      lines.push(`| ${name} | ${pillar.score}/100 | ${passed} | ${failed} |`);
    }
    lines.push('');
    lines.push(`## Switches`);
    lines.push('');
    lines.push(`| Switch | State |`);
    lines.push(`|---|---|`);
    for (const [k, v] of Object.entries(s.switches)) {
      lines.push(`| ${k} | ${typeof v === 'boolean' ? (v ? 'ON' : 'OFF') : v} |`);
    }
    lines.push('');
    lines.push(`## Synthetic data volume`);
    lines.push('');
    lines.push(`| Metric | Count |`);
    lines.push(`|---|---:|`);
    for (const [k, v] of Object.entries(s.numbers)) {
      lines.push(`| ${k} | ${v} |`);
    }
    lines.push('');
    for (const [pillarName, pillar] of Object.entries(s.pillars)) {
      lines.push(`## ${pillarName} checks`);
      lines.push('');
      lines.push(`| Check | Passed | Detail | Duration |`);
      lines.push(`|---|---|---|---:|`);
      for (const c of pillar.checks) {
        const mark = c.passed ? 'PASS' : 'FAIL';
        lines.push(`| ${c.name} | ${mark} | ${escapeMd(c.detail)} | ${c.durationMs ?? 0}ms |`);
      }
      lines.push('');
    }
    lines.push(`## Pillar detail`);
    lines.push('');
    for (const [name, pillar] of Object.entries(s.pillars)) {
      if (!pillar.numbers || Object.keys(pillar.numbers).length === 0) continue;
      lines.push(`### ${name}`);
      lines.push('');
      lines.push(`| Metric | Value |`);
      lines.push(`|---|---|`);
      for (const [k, v] of Object.entries(pillar.numbers)) {
        lines.push(`| ${k} | ${v} |`);
      }
      lines.push('');
    }
    return lines.join('\n');
  }
}

type PillarView = {
  score: number;
  checks: CheckResult[];
  numbers?: Record<string, number | string | boolean | null>;
};

function scoreFromChecks(checks: CheckResult[]): number {
  if (checks.length === 0) return 0;
  const passed = checks.filter((c) => c.passed).length;
  return Math.round((passed / checks.length) * 100);
}

function pillarFromChecks(
  checks: CheckResult[],
  numbers?: Record<string, number | string | boolean | null>,
): PillarView {
  return { score: scoreFromChecks(checks), checks, numbers };
}

function escapeMd(s: string): string {
  return String(s).replace(/\|/g, '\\|').replace(/\n/g, ' ');
}
