import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { DemoHarnessService } from '../demo/demo-harness.service';
import {
  AiProxyRuntimeService,
  type ProxyAuth,
} from '../ai-proxy/ai-proxy-runtime.service';
import type { ChatCompletionRequestDto } from '../ai-proxy/dto/ai-proxy.dto';
import type {
  CheckResult,
  PillarReport,
  ReadinessScorecard,
} from '../demo/readiness-scorecard.types';

/**
 * One row of an AI-suggested fix — stored as JSON in DebugSquasherRun.
 */
export type SuggestedFix = {
  title: string;
  fix: string;
  severity: 'low' | 'medium' | 'high';
  files: string[];
};

/**
 * The shape returned by every debug-squasher run. Mirrors what we persist to
 * DebugSquasherRun (one row per pillar) plus the overall summary row.
 */
export type DebugSquasherRunSummary = {
  runId: string;
  triggeredBy: 'cron' | 'manual' | 'startup';
  startedAt: string;
  durationMs: number;
  overall: 'PASS' | 'FAIL' | 'DEGRADED';
  readinessScore: number;
  totals: { checksRun: number; checksPassed: number; checksFailed: number };
  pillars: Array<{
    pillar: string;
    status: string;
    summary: string;
    diagnosis: string | null;
    suggestedFixes: SuggestedFix[];
    runDurationMs: number;
  }>;
};

/**
 * Output shape of the diagnosis call to the AI Gateway.
 */
type DiagnosisResult = {
  diagnosis: string | null;
  fixes: SuggestedFix[];
};

const PILLAR_KEYS = [
  'platform',
  'bot',
  'ai',
  'relay',
  'analyzer',
  'genome',
  'founder',
  'stress',
  'aiProxy',
  'routingEngine',
  'memoryEngine',
  'learningEngine',
  'doxxing',
  'ideaValidator',
] as const;

/**
 * Debug Squasher — the work-completing health-check + bug diagnostician.
 *
 * Pipeline:
 *   1. Run DemoHarnessService.runFull() to exercise every platform pillar.
 *   2. Collect failures from the readiness scorecard.
 *   3. For each failed pillar, ask the AI Gateway (GLM reasoning tier) to
 *      diagnose the root cause and propose a one-line fix. Uses DeepSeek
 *      (fast tier) for the quick triage pre-pass.
 *   4. Persist one DebugSquasherRun row per pillar (plus an 'overall' row)
 *      so the daily report and admin panel can render history.
 *
 * The cron job in debug-squasher.cron.ts drives this daily; the controller
 * exposes manual run + consent endpoints.
 */
@Injectable()
export class DebugSquasherService {
  private readonly logger = new Logger(DebugSquasherService.name);
  /** Synthetic system auth context for AI Gateway calls (no real user). */
  private readonly systemAuth: ProxyAuth = {
    userId: 'debug-squasher',
    nodeId: 'system',
  };

  constructor(
    private readonly prisma: PrismaService,
    private readonly harness: DemoHarnessService,
    private readonly ai: AiProxyRuntimeService,
    private readonly config: ConfigService,
  ) {}

  /**
   * Run a full debug-squash cycle. Always safe to call — failures inside the
   * harness are caught and turned into diagnoses, never re-thrown.
   *
   * `triggeredBy` is recorded on every persisted row so the daily report can
   * distinguish cron/manual/startup runs.
   */
  async run(
    triggeredBy: 'cron' | 'manual' | 'startup' = 'manual',
  ): Promise<DebugSquasherRunSummary> {
    const startedAt = Date.now();
    this.logger.log(`Debug Squasher run starting (trigger=${triggeredBy})`);

    let card: ReadinessScorecard;
    try {
      // skipStress so the daily cron doesn't hammer the platform; the manual
      // admin trigger can opt into stress via the harness directly.
      card = await this.harness.runFull({ skipStress: true });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger.error(`Harness itself crashed: ${message}`);
      return this.persistHarnessCrash(triggeredBy, startedAt, message);
    }

    const durationMs = Date.now() - startedAt;
    const pillarRows = await Promise.all(
      PILLAR_KEYS.map((key) =>
        this.diagnosePillar(key, card.pillars[key], triggeredBy),
      ),
    );

    // Persist the overall summary row so /latest can always find a header.
    const overallRow = await this.prisma.debugSquasherRun.create({
      data: {
        pillar: 'overall',
        status: card.overall.toLowerCase(),
        summary: `${card.overall} — score ${card.readinessScore} (${card.totals.checksPassed}/${card.totals.checksRun} checks passed)`,
        diagnosis: null,
        suggestedFixJson: [],
        runDurationMs: durationMs,
        triggeredBy,
      },
    });

    const summary: DebugSquasherRunSummary = {
      runId: overallRow.id,
      triggeredBy,
      startedAt: new Date(startedAt).toISOString(),
      durationMs,
      overall: card.overall,
      readinessScore: card.readinessScore,
      totals: card.totals,
      pillars: pillarRows.map((p) => ({
        pillar: p.pillar,
        status: p.status,
        summary: p.summary,
        diagnosis: p.diagnosis,
        suggestedFixes: p.fixes,
        runDurationMs: p.runDurationMs,
      })),
    };

    this.logger.log(
      `Debug Squasher run complete — overall=${card.overall} ` +
        `${card.totals.checksPassed}/${card.totals.checksRun} passed in ${durationMs}ms`,
    );
    return summary;
  }

  /**
   * Diagnose a single pillar: build a status + summary from its PillarReport,
   * and (only if it has failures) call the AI Gateway for a diagnosis + fixes.
   * Persists exactly one DebugSquasherRun row.
   */
  private async diagnosePillar(
    pillar: string,
    report: PillarReport,
    triggeredBy: 'cron' | 'manual' | 'startup',
  ): Promise<{
    pillar: string;
    status: string;
    summary: string;
    diagnosis: string | null;
    fixes: SuggestedFix[];
    runDurationMs: number;
  }> {
    const startedAt = Date.now();
    const failed = report.checks.filter((c) => !c.passed);
    const passed = report.checks.length - failed.length;
    const status = failed.length === 0 ? 'pass' : report.score >= 60 ? 'degraded' : 'fail';
    const summary = `${passed}/${report.checks.length} checks passed (score ${report.score})`;

    let diagnosis: string | null = null;
    let fixes: SuggestedFix[] = [];

    if (failed.length > 0) {
      try {
        const result = await this.callAiForDiagnosis(pillar, failed, report);
        diagnosis = result.diagnosis;
        fixes = result.fixes;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        this.logger.warn(
          `AI diagnosis failed for pillar=${pillar}: ${message}. Storing raw failure only.`,
        );
        diagnosis = `AI diagnosis unavailable: ${message}`;
      }
    }

    const runDurationMs = Date.now() - startedAt;
    await this.prisma.debugSquasherRun.create({
      data: {
        pillar,
        status,
        summary,
        diagnosis,
        suggestedFixJson: toJson(fixes),
        runDurationMs,
        triggeredBy,
      },
    });

    return { pillar, status, summary, diagnosis, fixes, runDurationMs };
  }

  /**
   * Build the diagnosis prompt and call the AI Gateway. Uses the
   * `founder-os-reasoning` alias so the gateway routes to GLM 5.2 for the
   * heavy reasoning pass; a `founder-os-fast` (DeepSeek) pre-pass produces a
   * short triage label that is prepended to the reasoning prompt for context.
   *
   * Both calls are non-streaming (we need the full body). If the gateway is
   * unavailable, the caller falls back to a null diagnosis.
   */
  private async callAiForDiagnosis(
    pillar: string,
    failures: CheckResult[],
    report: PillarReport,
  ): Promise<DiagnosisResult> {
    const failureBlob = failures
      .map((f, i) => `FAILURE ${i + 1}: ${f.name}\nDETAIL: ${f.detail}`)
      .join('\n\n');

    // 1) Fast triage pass via DeepSeek (founder-os-fast alias).
    const triagePrompt =
      `You are a triage engine for the "${pillar}" pillar of a NestJS + Prisma platform. ` +
      `Given these failures, respond with ONE line: severity (low|medium|high) and a 6-word label.\n\n` +
      `${failureBlob}\n\n` +
      `Format: "<severity> | <label>"`;
    let triageLabel = 'medium | needs investigation';
    try {
      triageLabel = await this.quickChat(triagePrompt, 'founder-os-fast');
    } catch (err) {
      this.logger.debug(
        `Triage pass skipped for pillar=${pillar}: ${err instanceof Error ? err.message : err}`,
      );
    }

    // 2) Reasoning diagnosis via GLM 5.2 (founder-os-reasoning alias).
    const reasoningPrompt =
      `You are a senior debugging agent for the Founder OS platform (NestJS + Prisma + Next.js monorepo).\n` +
      `The "${pillar}" pillar just failed its health check during a daily debug-squash run.\n\n` +
      `Pillar score: ${report.score}/100\n` +
      `Triage label: ${triageLabel}\n\n` +
      `Failures:\n${failureBlob}\n\n` +
      `Diagnose the most likely root cause and propose a concrete fix. Respond as STRICT JSON:\n` +
      `{\n` +
      `  "diagnosis": "<2-4 sentence root-cause explanation>",\n` +
      `  "fixes": [\n` +
      `    { "title": "<short title>", "fix": "<one-line actionable fix>", "severity": "low|medium|high", "files": ["<path or empty>"] }\n` +
      `  ]\n` +
      `}`;

    const raw = await this.quickChat(reasoningPrompt, 'founder-os-reasoning', 900);
    return this.parseDiagnosis(raw, triageLabel);
  }

  /**
   * Non-streaming chat completion through the AI Gateway. Returns the
   * assistant message content. Uses decideRoute → invoke so the call is
   * logged and DDollar-attributed like any other proxy request.
   */
  private async quickChat(
    prompt: string,
    modelAlias: string,
    maxTokens = 400,
  ): Promise<string> {
    const body: ChatCompletionRequestDto = {
      model: modelAlias,
      messages: [{ role: 'user', content: prompt }],
      stream: false,
      max_tokens: maxTokens,
      temperature: 0.2,
      response_format: { type: 'json_object' },
    };
    const route = await this.ai.decideRoute(this.systemAuth, body);
    const result = await this.ai.invoke(this.systemAuth, body, route);
    if (!result.ok || typeof result.body !== 'string') {
      throw new Error(
        `AI Gateway returned status=${result.status} ok=${result.ok}`,
      );
    }
    const parsed = JSON.parse(result.body) as {
      choices?: Array<{ message?: { content?: string } }>;
    };
    const content = parsed.choices?.[0]?.message?.content;
    if (!content) throw new Error('AI Gateway returned empty content');
    return content;
  }

  /**
   * Parse the JSON diagnosis payload from the reasoning model. Tolerates
   * code-fences and partial JSON by extracting the first balanced object.
   */
  private parseDiagnosis(raw: string, triageLabel: string): DiagnosisResult {
    const jsonText = this.extractJsonObject(raw);
    if (!jsonText) {
      return {
        diagnosis: `Triage: ${triageLabel}. (Model did not return JSON.)`,
        fixes: [],
      };
    }
    try {
      const parsed = JSON.parse(jsonText) as {
        diagnosis?: string;
        fixes?: Array<{
          title?: string;
          fix?: string;
          severity?: string;
          files?: string[];
        }>;
      };
      const fixes: SuggestedFix[] = (parsed.fixes ?? []).map((f) => ({
        title: f.title ?? 'untitled fix',
        fix: f.fix ?? '',
        severity: this.normalizeSeverity(f.severity),
        files: Array.isArray(f.files) ? f.files : [],
      }));
      return {
        diagnosis: parsed.diagnosis ?? `Triage: ${triageLabel}.`,
        fixes,
      };
    } catch {
      return {
        diagnosis: `Triage: ${triageLabel}. (Model JSON malformed.)`,
        fixes: [],
      };
    }
  }

  private extractJsonObject(text: string): string | null {
    const start = text.indexOf('{');
    if (start === -1) return null;
    let depth = 0;
    for (let i = start; i < text.length; i += 1) {
      const ch = text[i];
      if (ch === '{') depth += 1;
      else if (ch === '}') {
        depth -= 1;
        if (depth === 0) return text.slice(start, i + 1);
      }
    }
    return null;
  }

  private normalizeSeverity(s: string | undefined): 'low' | 'medium' | 'high' {
    if (s === 'high' || s === 'medium' || s === 'low') return s;
    return 'medium';
  }

  /**
   * Persist a single 'overall' row when the harness itself crashed (so rare
   * that the harness threw instead of returning a FAIL scorecard). Keeps the
   * daily report honest about platform-wide outages.
   */
  private async persistHarnessCrash(
    triggeredBy: 'cron' | 'manual' | 'startup',
    startedAt: number,
    message: string,
  ): Promise<DebugSquasherRunSummary> {
    const durationMs = Date.now() - startedAt;
    const row = await this.prisma.debugSquasherRun.create({
      data: {
        pillar: 'overall',
        status: 'error',
        summary: `Harness crashed: ${message.slice(0, 240)}`,
        diagnosis: `The platform harness threw an unrecoverable error: ${message}`,
        suggestedFixJson: toJson([]),
        runDurationMs: durationMs,
        triggeredBy,
      },
    });
    return {
      runId: row.id,
      triggeredBy,
      startedAt: new Date(startedAt).toISOString(),
      durationMs,
      overall: 'FAIL',
      readinessScore: 0,
      totals: { checksRun: 0, checksPassed: 0, checksFailed: 0 },
      pillars: [
        {
          pillar: 'overall',
          status: 'error',
          summary: row.summary,
          diagnosis: row.diagnosis,
          suggestedFixes: [],
          runDurationMs: durationMs,
        },
      ],
    };
  }

  // ─── Read-side helpers used by the controller ────────────────────────────

  /** Latest run summary (overall row + every pillar row from that cycle). */
  async getLatest(): Promise<DebugSquasherRunSummary | null> {
    const latestOverall = await this.prisma.debugSquasherRun.findFirst({
      where: { pillar: 'overall' },
      orderBy: { createdAt: 'desc' },
    });
    if (!latestOverall) return null;
    // Pull all pillar rows created within ±5s of the overall row — same run.
    const windowStart = new Date(latestOverall.createdAt.getTime() - 5000);
    const windowEnd = new Date(latestOverall.createdAt.getTime() + 5000);
    const pillarRows = await this.prisma.debugSquasherRun.findMany({
      where: {
        pillar: { not: 'overall' },
        createdAt: { gte: windowStart, lte: windowEnd },
      },
      orderBy: { createdAt: 'asc' },
    });

    return {
      runId: latestOverall.id,
      triggeredBy: latestOverall.triggeredBy as DebugSquasherRunSummary['triggeredBy'],
      startedAt: latestOverall.createdAt.toISOString(),
      durationMs: latestOverall.runDurationMs,
      overall:
        latestOverall.status === 'pass'
          ? 'PASS'
          : latestOverall.status === 'degraded'
            ? 'DEGRADED'
            : 'FAIL',
      readinessScore: this.extractScore(latestOverall.summary),
      totals: this.extractTotals(latestOverall.summary),
      pillars: pillarRows.map((r) => ({
        pillar: r.pillar,
        status: r.status,
        summary: r.summary,
        diagnosis: r.diagnosis,
        suggestedFixes: (r.suggestedFixJson as unknown as SuggestedFix[]) ?? [],
        runDurationMs: r.runDurationMs,
      })),
    };
  }

  /** History for the admin panel — N most recent overall rows. */
  async getHistory(limit = 20): Promise<Array<{ id: string; createdAt: string; status: string; summary: string; triggeredBy: string; durationMs: number }>> {
    const rows = await this.prisma.debugSquasherRun.findMany({
      where: { pillar: 'overall' },
      orderBy: { createdAt: 'desc' },
      take: Math.max(1, Math.min(100, limit)),
    });
    return rows.map((r) => ({
      id: r.id,
      createdAt: r.createdAt.toISOString(),
      status: r.status,
      summary: r.summary,
      triggeredBy: r.triggeredBy,
      durationMs: r.runDurationMs,
    }));
  }

  private extractScore(summary: string): number {
    const m = summary.match(/score (\d+)/i);
    return m ? Number(m[1]) : 0;
  }

  private extractTotals(summary: string): {
    checksRun: number;
    checksPassed: number;
    checksFailed: number;
  } {
    const m = summary.match(/(\d+)\/(\d+) checks passed/);
    if (!m) return { checksRun: 0, checksPassed: 0, checksFailed: 0 };
    const passed = Number(m[1]);
    const run = Number(m[2]);
    return { checksRun: run, checksPassed: passed, checksFailed: run - passed };
  }

  // ─── Consent flow ─────────────────────────────────────────────────────────

  async getConsent(userId: string): Promise<{
    consent: string;
    consentAt: string | null;
  }> {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { debugSquasherConsent: true, debugSquasherConsentAt: true },
    });
    return {
      consent: user?.debugSquasherConsent ?? 'unset',
      consentAt: user?.debugSquasherConsentAt?.toISOString() ?? null,
    };
  }

  async setConsent(
    userId: string,
    consent: 'accepted' | 'declined' | 'later',
  ): Promise<{ ok: true; consent: string }> {
    await this.prisma.user.update({
      where: { id: userId },
      data: {
        debugSquasherConsent: consent,
        debugSquasherConsentAt: new Date(),
      },
    });
    return { ok: true, consent };
  }

  /**
   * Has any admin opted in? The cron uses this to decide whether to run.
   * In dev (DEBUG_SQUASHER_ENABLED != 'false'), the cron runs regardless.
   */
  async hasAdminOptedIn(): Promise<boolean> {
    const accepted = await this.prisma.user.findFirst({
      where: { role: 'ADMIN', debugSquasherConsent: 'accepted' },
      select: { id: true },
    });
    return accepted != null;
  }

  /** Exposed for the cron to read the env flag centrally. */
  isFeatureEnabled(): boolean {
    const flag = this.config.get<string>('DEBUG_SQUASHER_ENABLED');
    // default ON in dev, opt-in in prod.
    if (flag == null) return process.env.NODE_ENV !== 'production';
    return flag !== 'false';
  }
}

/** Helper mirroring the pattern in deployment-modes.service.ts. */
const toJson = <T>(value: T): Prisma.InputJsonValue =>
  value as unknown as Prisma.InputJsonValue;
