import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { DdollarRuntimeService } from '../ddollar/ddollar-runtime.service';
import type { CheckResult } from './readiness-scorecard.types';

/**
 * Stress mode — floods the platform API, bot dashboard, and relay webhook
 * ingestion to validate throughput, DB connection pooling, and the
 * two-ledger DDollar invariant under load.
 *
 * Triggered by POST /api/admin/demo/stress or the orchestrator --stress flag.
 *
 * Reports: peak sustained RPS, p95 latency, error count, and asserts the
 * DDollar two-ledger invariant (lifetime >= spendable) holds after the burst.
 */
@Injectable()
export class DemoStressService {
  private readonly logger = new Logger(DemoStressService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly ddollarRuntime: DdollarRuntimeService,
  ) {}

  async run(opts: {
    rps?: number;
    durationS?: number;
    apiBaseUrl?: string;
    botBaseUrl?: string;
    relaySecret?: string;
    founderUserIds?: string[];
  } = {}): Promise<{
    checks: CheckResult[];
    metrics: {
      peakRps: number;
      p95LatencyMs: number;
      errorCount: number;
      requestsIssued: number;
      relayBurstAccepted: number;
      memoryHighWaterMb: number;
    };
  }> {
    const rps = clamp(Number(opts.rps ?? process.env.DEMO_STRESS_RPS ?? 20), 1, 200);
    const durationS = clamp(Number(opts.durationS ?? process.env.DEMO_STRESS_DURATION_S ?? 10), 1, 120);
    const apiBaseUrl = (opts.apiBaseUrl ?? process.env.DEMO_API_URL ?? 'http://127.0.0.1:4000').replace(/\/$/, '');
    const botBaseUrl = (opts.botBaseUrl ?? process.env.DEMO_BOT_URL ?? 'http://127.0.0.1:7002').replace(/\/$/, '');
    const relaySecret = opts.relaySecret ?? process.env.BOT_CONTROL_SECRET ?? '';
    const startedAt = Date.now();
    const latencies: number[] = [];
    let errorCount = 0;
    let requestsIssued = 0;

    // ---- Phase 1: platform API flood ----
    const endpoints = [
      '/api/projects',
      '/api/projects/featured/list',
      '/api/feed?filter=recent',
      '/api/founder-den/discover/universe?timeframe=24h',
      '/api/reputation/leaderboard?limit=5',
    ];
    const intervalMs = 1000 / rps;
    const deadline = Date.now() + durationS * 1000;
    const inflight: Promise<void>[] = [];
    while (Date.now() < deadline) {
      const url = `${apiBaseUrl}${endpoints[requestsIssued % endpoints.length]}`;
      inflight.push(
        this.timedFetch(url).then(({ latencyMs, ok }) => {
          latencies.push(latencyMs);
          if (!ok) errorCount += 1;
        }),
      );
      requestsIssued += 1;
      // eslint-disable-next-line no-await-in-loop
      await sleep(intervalMs);
    }
    await Promise.all(inflight);

    // ---- Phase 2: relay webhook burst (validates backpressure handling) ----
    let relayBurstAccepted = 0;
    if (relaySecret) {
      const burstPromises: Promise<boolean>[] = [];
      for (let i = 0; i < 25; i += 1) {
        const tradeId = `demo-stress-${startedAt}-${i}`;
        burstPromises.push(
          fetch(`${apiBaseUrl}/api/trading-agents/conservative-btc/showcase-relay-event`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'x-bot-control-secret': relaySecret,
            },
            body: JSON.stringify({
              event: 'LIMIT_UPDATED',
              trade_id: tradeId,
              ts: new Date().toISOString(),
              limit_price: 109850 + i,
              direction: 'LONG',
            }),
            signal: AbortSignal.timeout(10_000),
          })
            .then((r) => r.ok)
            .catch(() => false),
        );
      }
      const results = await Promise.all(burstPromises);
      relayBurstAccepted = results.filter(Boolean).length;
      // Relay errors don't count against platform errorCount (different service).
    }

    // ---- Phase 3: bot dashboard burst ----
    let botPingsOk = 0;
    for (let i = 0; i < 10; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      const ok = await fetch(`${botBaseUrl}/api/ping`, { signal: AbortSignal.timeout(5000) })
        .then((r) => r.ok)
        .catch(() => false);
      if (ok) botPingsOk += 1;
    }

    // ---- Phase 4: DDollar two-ledger invariant check ----
    const invariant = await this.checkDdollarInvariant(opts.founderUserIds ?? []);

    // ---- Aggregate ----
    latencies.sort((a, b) => a - b);
    const p95 = latencies.length > 0 ? latencies[Math.floor(latencies.length * 0.95)] : 0;
    const actualRps = requestsIssued / Math.max(0.001, (Date.now() - startedAt) / 1000);
    const memoryHighWaterMb = Math.round(process.memoryUsage().rss / 1024 / 1024);

    const checks: CheckResult[] = [
      result('stress_peak_rps', actualRps >= rps * 0.5, `${actualRps.toFixed(1)} rps (target ${rps})`),
      result('stress_p95_latency', p95 < 5000, `${p95}ms p95 (n=${latencies.length})`),
      result('stress_error_count', errorCount < requestsIssued * 0.05, `${errorCount} errors / ${requestsIssued} requests`),
      result('stress_relay_burst', !relaySecret || relayBurstAccepted >= 20, `${relayBurstAccepted}/25 relay webhooks accepted`),
      result('stress_bot_dash_under_load', botPingsOk >= 7, `${botPingsOk}/10 bot pings succeeded under load`),
      result('stress_ddollar_invariant', invariant.passed, `two-ledger invariant ${invariant.passed ? 'held' : 'violated'} — ${invariant.detail}`),
      result('stress_memory', memoryHighWaterMb < 2048, `RSS ${memoryHighWaterMb} MB`),
    ];
    return {
      checks,
      metrics: {
        peakRps: Number(actualRps.toFixed(1)),
        p95LatencyMs: p95,
        errorCount,
        requestsIssued,
        relayBurstAccepted,
        memoryHighWaterMb,
      },
    };
  }

  private async timedFetch(url: string): Promise<{ latencyMs: number; ok: boolean }> {
    const t0 = Date.now();
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
      return { latencyMs: Date.now() - t0, ok: res.ok };
    } catch {
      return { latencyMs: Date.now() - t0, ok: false };
    }
  }

  private async checkDdollarInvariant(
    userIds: string[],
  ): Promise<{ passed: boolean; detail: string }> {
    try {
      const users = userIds.length > 0
        ? await this.prisma.user.findMany({
            where: { id: { in: userIds } },
            select: { id: true, email: true, reputationPoints: true, lifetimeContributionEarned: true },
          })
        : await this.prisma.user.findMany({
            where: { email: { endsWith: '@doxxed.demo' } },
            take: 50,
            orderBy: { createdAt: 'asc' },
            select: { id: true, email: true, reputationPoints: true, lifetimeContributionEarned: true },
          });
      if (users.length === 0) return { passed: true, detail: 'no demo users to check' };
      const violated = users.filter((u) => u.lifetimeContributionEarned < u.reputationPoints);
      return violated.length === 0
        ? { passed: true, detail: `${users.length} demo users, all satisfy lifetime >= spendable` }
        : {
            passed: false,
            detail: `${violated.length}/${users.length} users have lifetime < spendable (e.g. ${violated[0].email})`,
          };
    } catch (err) {
      return { passed: false, detail: msg(err) };
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function clamp(n: number, lo: number, hi: number): number {
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function result(name: string, passed: boolean, detail: string): CheckResult {
  return { name, passed, detail };
}

function msg(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
