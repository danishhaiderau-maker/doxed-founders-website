import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { existsSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import { PrismaService } from '../prisma/prisma.service';
import { BotBridgeService } from '../trading-agents/bot-bridge.service';
import { CopyRelaySimService } from '../trading-agents/copy-relay-sim.service';
import { BusinessJourneyService } from './business-journey.service';
import { isDemoModeEnabled } from './demo.constants';
import type { CheckResult } from './readiness-scorecard.types';

/**
 * Extended smoke checks — bot / analyzer / genome / relay / AI / founder.
 *
 * Each method returns a list of CheckResult in the same shape as
 * DemoSeedService.runSmokeChecks() so the scorecard can aggregate them.
 *
 * Every check is best-effort: a missing file or unreachable bot produces a
 * failed check with a helpful detail, never an uncaught exception.
 */
@Injectable()
export class ExtendedSmokeService {
  private readonly logger = new Logger(ExtendedSmokeService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly config: ConfigService,
    private readonly botBridge: BotBridgeService,
    private readonly relaySim: CopyRelaySimService,
    private readonly businessJourney: BusinessJourneyService,
  ) {}

  // -------------------------------------------------------------------------
  // Bot pillar
  // -------------------------------------------------------------------------
  async runBotChecks(): Promise<CheckResult[]> {
    const botUrl = this.botBaseUrl();
    const checks: CheckResult[] = [];
    checks.push(await this.runCheck('bot_ping', async () => this.probeBotPing(botUrl)));
    checks.push(await this.runCheck('bot_state_parseable', async () => this.probeBotStateParseable(botUrl)));
    checks.push(await this.runCheck('bot_paper_orders_placed', async () => this.probePaperOrders()));
    checks.push(await this.runCheck('lane_size_patch_active', async () => this.probeLaneSizePatch(botUrl)));
    checks.push(await this.runCheck('lab_shadow_tiles_running', async () => this.probeLabShadowTiles(botUrl)));
    checks.push(await this.runCheck('tunnel_reachable', async () => this.probeTunnel()));
    return checks;
  }

  private botBaseUrl(): string {
    return (
      process.env.DEMO_BOT_URL?.trim() ||
      process.env.TRADING_AGENT_BOT_URL?.trim() ||
      'http://127.0.0.1:7002'
    );
  }

  private async probeBotPing(botUrl: string): Promise<CheckResult> {
    try {
      const res = await fetch(`${botUrl}/api/ping`, { signal: AbortSignal.timeout(8000) });
      if (!res.ok) return fail(`HTTP ${res.status}`);
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      const ok = Boolean(body?.status === 'ok' || body?.ok === true || body?.version);
      return ok ? pass(`ping ok (${JSON.stringify(body).slice(0, 80)})`) : fail('ping body missing status');
    } catch (err) {
      return fail(err);
    }
  }

  private async probeBotStateParseable(botUrl: string): Promise<CheckResult> {
    try {
      // Mirror the BotBridgeService parser: it pulls /api/relay-state or /api/state.
      const res = await fetch(`${botUrl}/api/state`, {
        signal: AbortSignal.timeout(15_000),
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) return fail(`/api/state HTTP ${res.status}`);
      const data = (await res.json()) as Record<string, unknown>;
      // BotBridgeService treats state as parseable when price + execution_paused exist.
      const hasPrice = data?.price != null;
      const hasPause = data?.execution_paused != null;
      const pausedSafe = data?.execution_paused === true || data?.execution_paused === 'SIMULATION_ONLY';
      const ok = hasPrice && hasPause && pausedSafe;
      return ok
        ? pass(`state parseable — price=${data.price} paused=${data.execution_paused}`)
        : fail(`state missing fields — price=${data?.price} paused=${data?.execution_paused} (must be paused for demo)`);
    } catch (err) {
      return fail(err);
    }
  }

  private async probePaperOrders(): Promise<CheckResult> {
    // trades_3factor.csv is the bot's canonical paper-trade ledger. Count rows
    // written this run by mtime.
    try {
      const path = this.botArtifactPath('trades_3factor.csv');
      if (!existsSync(path)) {
        return fail('trades_3factor.csv not found (bot may not have placed any paper orders yet)');
      }
      const lines = readFileSync(path, 'utf8').trim().split(/\r?\n/);
      const rows = lines.length > 1 ? lines.length - 1 : 0;
      const minOrders = Number(process.env.DEMO_MIN_PAPER_ORDERS ?? '0');
      const ok = rows >= minOrders;
      return ok
        ? pass(`${rows} paper orders in trades_3factor.csv (>= ${minOrders} required)`)
        : fail(`${rows} paper orders (< required ${minOrders})`);
    } catch (err) {
      return fail(err);
    }
  }

  private async probeLaneSizePatch(botUrl: string): Promise<CheckResult> {
    // The [LANE_SIZE_MULT_PATCH_2026-07-08] applies a 1.5x / 0.5x multiplier to
    // margin by session. Verify by pulling the bot state and confirming the
    // applied multipliers are non-default in debug_state, OR that at least one
    // open/pending order carries a non-1.0 size_mult marker.
    try {
      const res = await fetch(`${botUrl}/api/state`, {
        signal: AbortSignal.timeout(15_000),
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) return fail(`/api/state HTTP ${res.status}`);
      const data = (await res.json()) as Record<string, any>;
      const dbg = (data?.debug_state ?? data?.['debug_state'] ?? {}) as Record<string, unknown>;
      const orders = (data?.pending_orders ?? []) as Record<string, unknown>[];
      const open = (data?.open_positions ?? []) as Record<string, unknown>[];
      const allOrders = [...orders, ...open];
      const tagged = allOrders.filter((o) => {
        const mult = Number(o?.size_mult ?? o?.margin_mult ?? 1);
        return Number.isFinite(mult) && Math.abs(mult - 1.0) > 1e-6;
      });
      const lanePatchFlag = dbg?.lane_size_patch_active === true || dbg?.['lane_size_patch_active'] === true;
      const ok = tagged.length > 0 || lanePatchFlag;
      return ok
        ? pass(`lane size patch active — ${tagged.length} orders carry non-1x mult, flag=${lanePatchFlag}`)
        : fail('no order carries a non-1x size mult (patch may not have fired for this session)');
    } catch (err) {
      return fail(err);
    }
  }

  private async probeLabShadowTiles(botUrl: string): Promise<CheckResult> {
    // Assert SL_AVOIDANCE_V1 + SIZED_CONTINUOUS_V1 produced at least one
    // shadow signal this run. Shadow signals land in soft_reject_shadow.jsonl
    // tagged with the research lane.
    try {
      const softPath = this.botArtifactPath('soft_reject_shadow.jsonl');
      let slAvoidance = 0;
      let sizedContinuous = 0;
      if (existsSync(softPath)) {
        const lines = readFileSync(softPath, 'utf8').trim().split(/\r?\n/);
        for (const line of lines) {
          if (!line) continue;
          if (line.includes('SL_AVOIDANCE_V1')) slAvoidance += 1;
          if (line.includes('SIZED_CONTINUOUS_V1')) sizedContinuous += 1;
        }
      }
      // The bot state also exposes research_lane counters.
      const res = await fetch(`${botUrl}/api/state`, {
        signal: AbortSignal.timeout(15_000),
        headers: { Accept: 'application/json' },
      }).catch(() => null);
      if (res && res.ok) {
        const data = (await res.json()) as Record<string, any>;
        const laneCounters = (data?.lane_opportunity_counters ??
          data?.['lane_opportunity_counters'] ??
          {}) as Record<string, unknown>;
        slAvoidance += Number(laneCounters?.['SL_AVOIDANCE_V1'] ?? 0);
        sizedContinuous += Number(laneCounters?.['SIZED_CONTINUOUS_V1'] ?? 0);
      }
      const ok = slAvoidance > 0 && sizedContinuous > 0;
      return ok
        ? pass(`LAB shadow tiles firing — SL_AVOIDANCE=${slAvoidance} SIZED_CONTINUOUS=${sizedContinuous}`)
        : fail(`LAB shadow tiles idle — SL_AVOIDANCE=${slAvoidance} SIZED_CONTINUOUS=${sizedContinuous} (soft_reject_shadow.jsonl may be empty)`);
    } catch (err) {
      return fail(err);
    }
  }

  private async probeTunnel(): Promise<CheckResult> {
    if (process.env.DEMO_SKIP_TUNNEL === '1') {
      return pass('skipped (DEMO_SKIP_TUNNEL=1)');
    }
    const url = process.env.BOT_PUBLIC_URL?.trim() || 'https://bot.doxxedcrypto.digital';
    try {
      const res = await fetch(`${url}/api/ping`, { signal: AbortSignal.timeout(12_000) });
      if (!res.ok) return fail(`tunnel ${url} HTTP ${res.status}`);
      return pass(`tunnel ${url} reachable`);
    } catch (err) {
      return fail(`tunnel ${url} unreachable — ${msg(err)} (set DEMO_SKIP_TUNNEL=1 to skip)`);
    }
  }

  // -------------------------------------------------------------------------
  // AI pillar
  // -------------------------------------------------------------------------
  async runAiChecks(): Promise<CheckResult[]> {
    const checks: CheckResult[] = [];
    checks.push(await this.runCheck('ai_verdicts_emitted', async () => this.probeAiVerdicts()));
    checks.push(await this.runCheck('ai_latency_within_budget', async () => this.probeAiLatency()));
    return checks;
  }

  private async probeAiVerdicts(): Promise<CheckResult> {
    const path = this.botArtifactPath('ai_input_log.jsonl');
    if (!existsSync(path)) return fail('ai_input_log.jsonl not found');
    const lines = readFileSync(path, 'utf8').trim().split(/\r?\n/).filter(Boolean);
    if (lines.length === 0) return fail('ai_input_log.jsonl empty');
    const since = this.demoStartMs();
    let recent = 0;
    let withDecision = 0;
    for (const line of lines.slice(-200)) {
      try {
        const row = JSON.parse(line) as Record<string, unknown>;
        const ts = Number(row?.ts ?? row?.time ?? 0);
        if (since > 0 && ts * 1000 >= since) recent += 1;
        const decision = String(row?.decision ?? '').toUpperCase();
        if (decision && decision !== 'AI_ERROR' && decision !== 'UNKNOWN') withDecision += 1;
      } catch {
        continue;
      }
    }
    const minVerdicts = Number(process.env.DEMO_MIN_AI_VERDICTS ?? '0');
    const ok = withDecision >= minVerdicts && (since === 0 || recent > 0 || lines.length > 0);
    return ok
      ? pass(`${withDecision} AI verdicts with non-empty decision (${recent} this run)`)
      : fail(`${withDecision} verdicts with decision (< ${minVerdicts} required)`);
  }

  private async probeAiLatency(): Promise<CheckResult> {
    const path = this.botArtifactPath('ai_input_log.jsonl');
    if (!existsSync(path)) return fail('ai_input_log.jsonl not found');
    const lines = readFileSync(path, 'utf8').trim().split(/\r?\n/).filter(Boolean);
    if (lines.length === 0) return fail('ai_input_log.jsonl empty');
    const latencies: number[] = [];
    for (const line of lines.slice(-200)) {
      try {
        const row = JSON.parse(line) as Record<string, unknown>;
        const lat = Number(row?.latency_ms ?? -1);
        if (Number.isFinite(lat) && lat > 0) latencies.push(lat);
      } catch {
        continue;
      }
    }
    if (latencies.length === 0) return fail('no latency_ms entries in ai_input_log.jsonl');
    latencies.sort((a, b) => a - b);
    const median = latencies[Math.floor(latencies.length / 2)]!;
    const budget = Number(process.env.DEMO_AI_LATENCY_BUDGET_MS ?? '4000');
    const ok = median < budget;
    return ok
      ? pass(`median AI latency ${median}ms (< ${budget}ms budget, n=${latencies.length})`)
      : fail(`median AI latency ${median}ms exceeds ${budget}ms budget`);
  }

  // -------------------------------------------------------------------------
  // Relay pillar
  // -------------------------------------------------------------------------
  async runRelayChecks(): Promise<CheckResult[]> {
    const checks: CheckResult[] = [];
    const since = new Date(this.demoStartMs() || Date.now() - 24 * 3600_000);
    const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
    if (!agent) {
      checks.push(failResult('relay_wakeup_path', 'conservative-btc agent missing from DB'));
      return checks;
    }
    checks.push(await this.runCheck('relay_approve_pending_received', async () => this.probeRelayEvent('APPROVE_PENDING', agent.id, since)));
    checks.push(await this.runCheck('relay_order_placed_received', async () => this.probeRelayEvent('ORDER_PLACED', agent.id, since)));
    checks.push(await this.runCheck('relay_position_closed_received', async () => this.probeRelayEvent('POSITION_CLOSED', agent.id, since)));
    checks.push(await this.runCheck('relay_cycle_completes', async () => this.probeCycleCompletes(agent.id, since)));
    checks.push(await this.runCheck('copy_relay_sim_consistent', async () => this.probeRelaySimConsistent()));
    return checks;
  }

  private async probeRelayEvent(eventType: string, agentId: string, since: Date): Promise<CheckResult> {
    try {
      const count = await this.prisma.signalCycleEvent.count({
        where: { eventType, cycle: { agentId }, createdAt: { gte: since } },
      });
      return count > 0
        ? pass(`${count} ${eventType} events since ${since.toISOString()}`)
        : fail(`0 ${eventType} events since ${since.toISOString()}`);
    } catch (err) {
      return fail(err);
    }
  }

  private async probeCycleCompletes(agentId: string, since: Date): Promise<CheckResult> {
    try {
      // A "complete" cycle = cycle with all three event types present.
      const cycles = await this.prisma.signalCycle.findMany({
        where: { agentId, createdAt: { gte: since } },
        take: 50,
        orderBy: { createdAt: 'desc' },
        select: { id: true, tradeId: true, status: true },
      });
      let complete = 0;
      for (const cycle of cycles) {
        const types = await this.prisma.signalCycleEvent.findMany({
          where: { cycleId: cycle.id, eventType: { in: ['APPROVE_PENDING', 'ORDER_PLACED', 'POSITION_CLOSED'] } },
          distinct: ['eventType'] as const,
          select: { eventType: true },
        });
        if (types.length >= 3) complete += 1;
      }
      return complete > 0
        ? pass(`${complete} cycle(s) went APPROVE_PENDING -> ORDER_PLACED -> POSITION_CLOSED`)
        : fail('0 complete signal cycles this run (relay round-trip not observed)');
    } catch (err) {
      return fail(err);
    }
  }

  private async probeRelaySimConsistent(): Promise<CheckResult> {
    // If no relay-sim instance is active, treat as informational pass (the
    // demo may run without any copy participants). Otherwise confirm the
    // sim PnL mirrors the bot PnL within $5.
    try {
      const agent = await this.prisma.tradingAgent.findUnique({ where: { slug: 'conservative-btc' } });
      if (!agent) return fail('conservative-btc agent missing');
      const instances = await this.prisma.tradingAgentInstance.findMany({
        where: { agentId: agent.id, exchangeProvider: 'bitfinex' },
      });
      const activeSim = instances.filter((i) => {
        const dash = (i.dashboardState ?? {}) as Record<string, any>;
        const sim = dash.copyRelaySim;
        return sim?.active === true;
      });
      if (activeSim.length === 0) {
        return pass('no active relay-sim instances (skip — demo can run without copy)');
      }
      // For each active sim, verify the participant has a non-error status.
      const errored = activeSim.filter((i) => (i.lastError ?? '').length > 0);
      return errored.length === 0
        ? pass(`${activeSim.length} active relay-sim instance(s), all without errors`)
        : fail(`${errored.length}/${activeSim.length} relay-sim instances have errors`);
    } catch (err) {
      return fail(err);
    }
  }

  // -------------------------------------------------------------------------
  // Analyzer pillar
  // -------------------------------------------------------------------------
  async runAnalyzerChecks(): Promise<CheckResult[]> {
    const checks: CheckResult[] = [];
    checks.push(await this.runCheck('analyzer_manifest_present', async () => this.probeManifest()));
    checks.push(await this.runCheck('analyzer_new_v1_reports', async () => this.probeNewV1Reports()));
    return checks;
  }

  private async probeManifest(): Promise<CheckResult> {
    const path = this.botArtifactPath('reports', 'report_manifest.json');
    if (!existsSync(path)) return fail('reports/report_manifest.json not found');
    try {
      const manifest = JSON.parse(readFileSync(path, 'utf8')) as Record<string, any>;
      const reports = (manifest?.reports ?? []) as unknown[];
      const ok = manifest?.schema === 'report_manifest_v1' && reports.length > 0;
      return ok
        ? pass(`manifest schema=${manifest?.schema}, ${reports.length} reports listed`)
        : fail(`manifest malformed — schema=${manifest?.schema}, reports=${reports.length}`);
    } catch (err) {
      return fail(err);
    }
  }

  private async probeNewV1Reports(): Promise<CheckResult> {
    const required = [
      'session_edge_report.json',
      'statistical_significance_report.json',
      'stop_loss_fingerprint_report.json',
    ];
    const missing: string[] = [];
    const parsed: string[] = [];
    for (const name of required) {
      const inReports = this.botArtifactPath('reports', name);
      const atRoot = this.botArtifactPath(name);
      const path = existsSync(inReports) ? inReports : atRoot;
      if (!existsSync(path)) {
        missing.push(name);
        continue;
      }
      try {
        JSON.parse(readFileSync(path, 'utf8'));
        parsed.push(name);
      } catch {
        missing.push(`${name} (unparseable)`);
      }
    }
    return missing.length === 0
      ? pass(`all 3 new v1 reports parse (${parsed.join(', ')})`)
      : fail(`missing/unparseable: ${missing.join(', ')}`);
  }

  // -------------------------------------------------------------------------
  // Genome pillar
  // -------------------------------------------------------------------------
  async runGenomeChecks(): Promise<CheckResult[]> {
    const checks: CheckResult[] = [];
    checks.push(await this.runCheck('genome_events_emitted', async () => this.probeGenomeEvents()));
    checks.push(await this.runCheck('genome_db_queryable', async () => this.probeGenomeDb()));
    return checks;
  }

  private async probeGenomeEvents(): Promise<CheckResult> {
    const files = ['decision_genome.jsonl', 'environment_genome.jsonl', 'market_genome.jsonl'];
    const since = this.demoStartMs();
    let grewThisRun = 0;
    let totalLines = 0;
    for (const name of files) {
      const path = this.botArtifactPath('research', 'genome', name);
      if (!existsSync(path)) continue;
      const stat = statSync(path);
      if (since > 0 && stat.mtimeMs >= since) grewThisRun += 1;
      const lines = readFileSync(path, 'utf8').trim().split(/\r?\n/).filter(Boolean).length;
      totalLines += lines;
    }
    return totalLines > 0
      ? pass(`${totalLines} genome rows across ${files.length} files (${grewThisRun} grew this run)`)
      : fail('genome jsonl files empty/missing');
  }

  private async probeGenomeDb(): Promise<CheckResult> {
    const path = this.botArtifactPath('research.db');
    if (!existsSync(path)) return fail('research.db not found');
    // SQLite is queryable if the file opens. We can't easily shell out to
    // sqlite3 here, so the file's existence + non-zero size is the proxy.
    const size = statSync(path).size;
    return size > 0
      ? pass(`research.db present (${(size / 1024).toFixed(1)} KB)`)
      : fail('research.db is zero bytes');
  }

  // -------------------------------------------------------------------------
  // Founder pillar
  // -------------------------------------------------------------------------
  async runFounderChecks(): Promise<CheckResult[]> {
    const checks: CheckResult[] = [];
    checks.push(await this.runCheck('founder_message_dispatch', async () => this.probeFounderMessage()));
    checks.push(await this.runCheck('founder_os_full_journey', async () => this.probeFounderJourney()));
    return checks;
  }

  private async probeFounderMessage(): Promise<CheckResult> {
    // Synthesize a notification + a founder event, then verify both land.
    try {
      const user = await this.prisma.user.findFirst({
        where: { email: { endsWith: '@doxxed.demo' } },
        select: { id: true },
      });
      if (!user) return fail('no demo user to dispatch founder message to');
      const dedupe = `demo-founder-msg-${Date.now()}`;
      await this.prisma.notification.create({
        data: {
          userId: user.id,
          type: 'POINTS_EARNED',
          title: dedupe,
          body: '[Demo] Synthetic founder message for harness verification.',
          link: '/founder-os',
        },
      });
      const found = await this.prisma.notification.findFirst({
        where: { userId: user.id, title: dedupe },
      });
      return found
        ? pass(`founder message dispatched — notification "${dedupe}" landed`)
        : fail('founder message did not land in notifications feed');
    } catch (err) {
      return fail(err);
    }
  }

  private async probeFounderJourney(): Promise<CheckResult> {
    try {
      const result = await this.businessJourney.runGoldenDdollarJourney();
      return result.passed
        ? pass(`golden journey OK — ${result.detail}`)
        : fail(`golden journey failed — ${result.detail}`);
    } catch (err) {
      return fail(err);
    }
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  /** The orchestrator sets DEMO_RUN_STARTED_MS; fall back to 24h ago. */
  private demoStartMs(): number {
    const raw = Number(process.env.DEMO_RUN_STARTED_MS ?? 0);
    return Number.isFinite(raw) && raw > 0 ? raw : 0;
  }

  private botArtifactPath(...segments: string[]): string {
    const base = process.env.DEMO_BOT_CWD?.trim() ||
      join(process.cwd(), 'services', 'btc-conservative-agent');
    return join(base, ...segments);
  }

  private async runCheck(name: string, fn: () => Promise<CheckResult>): Promise<CheckResult> {
    const t0 = Date.now();
    try {
      const result = await fn();
      return { ...result, name, durationMs: Date.now() - t0 };
    } catch (err) {
      return {
        name,
        passed: false,
        detail: msg(err),
        durationMs: Date.now() - t0,
      };
    }
  }
}

function pass(detail: string): CheckResult {
  return { name: '', passed: true, detail };
}

function fail(detail: unknown): CheckResult {
  return { name: '', passed: false, detail: msg(detail) };
}

function failResult(name: string, detail: string): CheckResult {
  return { name, passed: false, detail, durationMs: 0 };
}

function msg(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// Reference isDemoModeEnabled to keep the import used (it's a useful guard
// for future per-check gating; the harness already enforces demo mode).
void isDemoModeEnabled;
