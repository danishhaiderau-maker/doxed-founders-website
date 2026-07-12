import { Injectable, Logger } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { IdeaCheck, IdeaCheckStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { AiProxyRuntimeService, type ProxyAuth } from '../ai-proxy/ai-proxy-runtime.service';
import { FOUNDER_OS_AUTO_MODEL } from '../ai-proxy/ai-proxy.constants';
import type { ChatCompletionRequestDto } from '../ai-proxy/dto/ai-proxy.dto';
import { BrowserResearchAdapter } from './browser-research.adapter';
import {
  DEFAULT_RESEARCH_BUDGET,
  type ResearchQuery,
  type ResearchTarget,
} from './browser-research.types';
import type {
  CompetitorEntry,
  IdeaValidationReport,
  IdeaVerdict,
  OpenSourceReuseEntry,
} from './idea-validator.types';

/**
 * FounderIdeaValidatorService — orchestrates the idea-check flow.
 *
 * Flow (docs/FOUNDER-IDEA-VALIDATOR.md §2.1):
 *   1. Generate search queries from the idea text (one cheap model call
 *      through the AI Gateway — keyword extraction).
 *   2. Run the Browser Use research hand (BrowserResearchAdapter) — for
 *      each query, browse GitHub / Product Hunt / web, extracting
 *      project names, descriptions, stars, URLs.
 *   3. Synthesize the competitive landscape (one reasoning-tier model
 *      call through the AI Gateway): categorize, score differentiation,
 *      identify reusable OSS, write the summary.
 *   4. Persist everything to IdeaCheck.resultJson; status → COMPLETED.
 *   5. On any failure, status → FAILED with the error message.
 *
 * Discipline: EVERY model call goes through AiProxyRuntimeService so it
 * is logged in the Flight Recorder and meters DDollar. No direct
 * DeepSeek/GLM API calls anywhere in this service.
 */
@Injectable()
export class IdeaValidatorService {
  private readonly logger = new Logger(IdeaValidatorService.name);

  /** Idempotency window: reuse a check with the same idea hash < this old. */
  private static readonly IDEMPOTENCY_WINDOW_MS = 24 * 60 * 60 * 1000;
  private static readonly MAX_ATTEMPTS = 3;
  private static readonly STALE_WORK_MS = 15 * 60 * 1000;

  constructor(
    private readonly prisma: PrismaService,
    private readonly aiProxy: AiProxyRuntimeService,
    private readonly browserResearch: BrowserResearchAdapter,
  ) {}

  /**
   * Kick off (or reuse) a check for a user's idea. Creates the IdeaCheck
   * row with status PENDING, fires the async research without blocking,
   * and returns the row. Idempotent within 24h on identical idea text
   * unless force=true.
   */
  async checkIdea(
    auth: ProxyAuth,
    params: {
      ideaText: string;
      projectId?: string;
      applicationId?: string;
      force?: boolean;
    },
  ): Promise<IdeaCheck> {
    const ideaHash = this.hashIdea(params.ideaText);

    if (!params.force) {
      const recent = await this.prisma.ideaCheck.findFirst({
        where: {
          userId: auth.userId,
          ideaText: params.ideaText,
          createdAt: { gte: new Date(Date.now() - IdeaValidatorService.IDEMPOTENCY_WINDOW_MS) },
          status: { in: ['COMPLETED', 'RUNNING', 'PENDING'] },
        },
        orderBy: { createdAt: 'desc' },
      });
      if (recent) {
        this.logger.log(
          `reusing recent idea check ${recent.id} for user ${auth.userId} (ideaHash=${ideaHash.slice(0, 8)})`,
        );
        return recent;
      }
    }

    const row = await this.prisma.ideaCheck.create({
      data: {
        userId: auth.userId,
        projectId: params.projectId ?? null,
        applicationId: params.applicationId ?? null,
        ideaText: params.ideaText,
        status: IdeaCheckStatus.PENDING,
      },
    });

    // Prompt the durable worker now for a responsive UI. The scheduled worker
    // owns recovery after a restart, so this is an optimisation rather than a
    // correctness requirement.
    void this.processPending(1).catch((err) => {
      this.logger.warn(`idea queue kick failed: ${err instanceof Error ? err.message : String(err)}`);
    });

    return row;
  }

  /** List a user's recent checks, newest first. */
  async listChecks(userId: string, limit = 20): Promise<IdeaCheck[]> {
    return this.prisma.ideaCheck.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
      take: Math.min(Math.max(limit, 1), 50),
    });
  }

  /** Get one check; caller is responsible for ownership checks. */
  async getCheck(id: string, userId: string): Promise<IdeaCheck | null> {
    return this.prisma.ideaCheck.findFirst({
      where: { id, userId },
    });
  }

  /**
   * Most recent COMPLETED check, for the daily pop-up. Only surfaces
   * unviewed checks so the pop-up shows once per result.
   */
  async latestCompletedForUser(userId: string): Promise<IdeaCheck | null> {
    return this.prisma.ideaCheck.findFirst({
      where: { userId, status: 'COMPLETED', dismissed: false },
      orderBy: { createdAt: 'desc' },
    });
  }

  /** Patch dismissed/viewed flags from the UI. */
  async patchCheck(
    id: string,
    userId: string,
    patch: { dismissed?: boolean; viewed?: boolean },
  ): Promise<IdeaCheck | null> {
    const data: Record<string, unknown> = {};
    if (patch.dismissed !== undefined) data.dismissed = patch.dismissed;
    if (patch.viewed !== undefined) data.viewed = patch.viewed;
    if (Object.keys(data).length === 0) {
      return this.getCheck(id, userId);
    }
    try {
      return await this.prisma.ideaCheck.updateMany({
        where: { id, userId },
        data,
      }).then(async () => this.getCheck(id, userId));
    } catch {
      return null;
    }
  }

  /**
   * Find users with unviewed completed checks — used by the daily cron
   * (Part C). Returns distinct userIds that have at least one COMPLETED,
   * unviewed, undismissed check.
   */
  async usersWithUnviewedCompletedChecks(): Promise<string[]> {
    const rows = await this.prisma.ideaCheck.findMany({
      where: { status: 'COMPLETED', viewed: false, dismissed: false },
      select: { userId: true },
      distinct: ['userId'],
    });
    return rows.map((r) => r.userId);
  }

  /**
   * Claim and process queued checks. The conditional update is the lock: two
   * API replicas may discover the same row, but only one transitions it to
   * RUNNING. Stale RUNNING rows are returned to PENDING before claiming.
   */
  async processPending(limit = 5): Promise<{ processed: number }> {
    const now = new Date();
    const staleBefore = new Date(now.getTime() - IdeaValidatorService.STALE_WORK_MS);
    await this.prisma.ideaCheck.updateMany({
      where: { status: IdeaCheckStatus.RUNNING, processingStartedAt: { lt: staleBefore } },
      data: { status: IdeaCheckStatus.PENDING, processingStartedAt: null },
    });

    const queued = await this.prisma.ideaCheck.findMany({
      where: {
        status: IdeaCheckStatus.PENDING,
        OR: [{ nextAttemptAt: null }, { nextAttemptAt: { lte: now } }],
        attemptCount: { lt: IdeaValidatorService.MAX_ATTEMPTS },
      },
      orderBy: { createdAt: 'asc' },
      take: Math.max(1, Math.min(limit, 20)),
    });

    let processed = 0;
    for (const row of queued) {
      const claim = await this.prisma.ideaCheck.updateMany({
        where: { id: row.id, status: IdeaCheckStatus.PENDING },
        data: {
          status: IdeaCheckStatus.RUNNING,
          processingStartedAt: new Date(),
          nextAttemptAt: null,
          attemptCount: { increment: 1 },
        },
      });
      if (claim.count !== 1) continue;
      processed += 1;
      await this.runResearch({ userId: row.userId, nodeId: 'idea-validator-worker' }, row.id, row.ideaText);
    }
    return { processed };
  }

  // -- The research flow ------------------------------------------------

  /**
   * The orchestration: query generation → browser research → synthesis →
   * persist. Updates the row status RUNNING at the start, COMPLETED with
   * the result on success, FAILED with the error on any exception.
   */
  private async runResearch(auth: ProxyAuth, rowId: string, ideaText: string): Promise<void> {
    await this.setStatus(rowId, IdeaCheckStatus.RUNNING);
    try {
      // Step 1 — generate search queries (cheap model call).
      const queries = await this.generateSearchQueries(auth, ideaText);
      await this.prisma.ideaCheck.update({
        where: { id: rowId },
        data: { searchQueries: queries as unknown as never },
      });

      // Step 2 — browser research (the LAM capability).
      const researchQueries: ResearchQuery[] = queries.map((q) => ({
        query: q,
        targets: ['github', 'web'] as ResearchTarget[],
      }));
      const results = await this.browserResearch.runResearch(
        auth,
        researchQueries,
        DEFAULT_RESEARCH_BUDGET,
      );
      const hits = results.flatMap((r) => r.hits);
      this.logger.log(
        `idea check ${rowId}: ${results.length} queries, ${hits.length} total hits`,
      );

      // Step 3 — synthesize the competitive landscape (reasoning-tier call).
      const report = await this.synthesize(auth, ideaText, hits);

      // Step 4 — persist.
      await this.prisma.ideaCheck.update({
        where: { id: rowId },
        data: {
          status: IdeaCheckStatus.COMPLETED,
          resultJson: report as unknown as never,
          differentiationScore: report.differentiationScore,
          similarProjectsJson: report.competitors as unknown as never,
          suggestedOssJson: report.openSourceReuse as unknown as never,
          completedAt: new Date(),
          processingStartedAt: null,
          nextAttemptAt: null,
        },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.logger.error(`idea check ${rowId} failed during research: ${message}`);
      const current = await this.prisma.ideaCheck.findUnique({
        where: { id: rowId },
        select: { attemptCount: true },
      });
      const attempts = current?.attemptCount ?? IdeaValidatorService.MAX_ATTEMPTS;
      const retry = attempts < IdeaValidatorService.MAX_ATTEMPTS;
      const retryDelayMs = Math.pow(2, Math.max(0, attempts - 1)) * 60_000;
      await this.prisma.ideaCheck.update({
        where: { id: rowId },
        data: {
          status: retry ? IdeaCheckStatus.PENDING : IdeaCheckStatus.FAILED,
          errorMessage: message.slice(0, 4000),
          processingStartedAt: null,
          nextAttemptAt: retry ? new Date(Date.now() + retryDelayMs) : null,
        },
      });
    }
  }

  /**
   * Step 1 — one cheap model call through the AI Gateway to turn the idea
   * description into 5-8 targeted search queries. This is the "Stage A"
   * keyword extraction from the design doc §7.1 — it's non-optional
   * because naive keyword extraction misses domain terms.
   */
  private async generateSearchQueries(auth: ProxyAuth, ideaText: string): Promise<string[]> {
    const systemPrompt =
      'You generate search queries for finding existing projects similar to a founder\'s idea. ' +
      'Return a JSON object {"queries": ["...", "..."]} with 5-8 targeted queries. ' +
      'Include domain/technical terms a developer would use (e.g. "MEV", "frontrunning", "DEX") ' +
      'not just the founder\'s words. Mix broad and specific queries. JSON only.';

    const body: ChatCompletionRequestDto = {
      model: FOUNDER_OS_AUTO_MODEL,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: `Founder's idea:\n${ideaText}` },
      ],
      max_tokens: 400,
      temperature: 0.2,
      response_format: { type: 'json_object' },
    };

    try {
      const route = await this.aiProxy.decideRoute(auth, body);
      const result = await this.aiProxy.invoke(auth, body, route);
      if (!result.ok) {
        // Fallback: simple keyword split.
        return this.fallbackQueries(ideaText);
      }
      const parsed = JSON.parse(typeof result.body === 'string' ? result.body : '') as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      const content = parsed.choices?.[0]?.message?.content ?? '';
      const obj = JSON.parse(content) as { queries?: string[] };
      const queries = Array.isArray(obj.queries) ? obj.queries.filter((q) => typeof q === 'string') : [];
      return queries.length > 0 ? queries.slice(0, 8) : this.fallbackQueries(ideaText);
    } catch (err) {
      this.logger.warn(
        `query generation model call failed: ${err instanceof Error ? err.message : String(err)} — using fallback`,
      );
      return this.fallbackQueries(ideaText);
    }
  }

  /**
   * Naive fallback for query generation: split on whitespace, drop
   * stopwords, take the top tokens. Used if the model call fails.
   */
  private fallbackQueries(ideaText: string): string[] {
    const stopwords = new Set([
      'the', 'a', 'an', 'and', 'or', 'but', 'for', 'to', 'of', 'in', 'on', 'at',
      'is', 'are', 'was', 'were', 'be', 'been', 'being', 'i', 'want', 'build',
      'make', 'create', 'that', 'this', 'with', 'from', 'by', 'as', 'it',
    ]);
    const tokens = ideaText
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter((t) => t.length > 2 && !stopwords.has(t));
    const unique = Array.from(new Set(tokens)).slice(0, 6);
    return unique.length > 0 ? [unique.join(' ')] : [ideaText.slice(0, 80)];
  }

  /**
   * Step 3 — the value-driving synthesis call. Feeds the idea + the
   * research hits to the reasoning-tier model (GLM 5.2 / DeepSeek via the
   * AI Gateway) and parses the structured report. The model categorizes
   * competitors, scores differentiation, identifies reusable OSS, and
   * writes the summary.
   */
  private async synthesize(
    auth: ProxyAuth,
    ideaText: string,
    hits: Array<{ name: string; description: string; url: string; stars?: number; source: string }>,
  ): Promise<IdeaValidationReport> {
    const evidenceBlock = JSON.stringify(
      hits.map((h) => ({
        name: h.name,
        url: h.url,
        description: (h.description ?? '').slice(0, 300),
        stars: h.stars,
        source: h.source,
      })),
      null,
      2,
    ).slice(0, 8000);

    const systemPrompt =
      'You are the Founder OS Idea Validator. Given a founder\'s idea and a bundle of ' +
      'evidence (GitHub repos + web results), produce a rigorous competitive landscape analysis.\n\n' +
      'Return STRICT JSON with this shape:\n' +
      '{\n' +
      '  "verdict": "novel" | "empty" | "moderate" | "crowded",\n' +
      '  "summary": "1-2 paragraph landscape + differentiation summary",\n' +
      '  "differentiation": "2-4 sentences naming the founder\'s specific wedge",\n' +
      '  "differentiationScore": 0-100 (higher = more differentiated),\n' +
      '  "competitors": [{ "name", "type": "oss|product|startup", "url", "description", ' +
      '    "stars?", "traction?", "funding?", "differentiation" }],\n' +
      '  "openSourceReuse": [{ "repo", "license", "whatToReuse", "modulePath?", ' +
      '    "savedTimeEstimate?", "lastPushedAt?", "stars?" }]\n' +
      '}\n\n' +
      'Rules:\n' +
      '- Be honest. If crowded, say so. If novel, say why.\n' +
      '- Do not fabricate companies, funding rounds, star counts, or licenses.\n' +
      '- verdict: novel = no competitors; empty = <3 weak; moderate = 3-8; crowded = >8 or funded incumbent.\n' +
      '- differentiationScore: 80+ for novel, 60-80 moderate, 30-60 crowded-with-wedge, <30 saturated.\n' +
      'JSON only.';

    const userPrompt =
      `FOUNDER'S IDEA:\n${ideaText}\n\n` +
      `EVIDENCE (pre-fetched from GitHub + web):\n${evidenceBlock}\n\n` +
      `Produce the report.`;

    const body: ChatCompletionRequestDto = {
      model: FOUNDER_OS_AUTO_MODEL,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 2048,
      temperature: 0.3,
      response_format: { type: 'json_object' },
    };

    const route = await this.aiProxy.decideRoute(auth, body);
    const result = await this.aiProxy.invoke(auth, body, route);
    if (!result.ok) {
      throw new Error(`synthesis model call failed (status ${result.status})`);
    }
    const parsed = JSON.parse(typeof result.body === 'string' ? result.body : '') as {
      choices?: Array<{ message?: { content?: string } }>;
    };
    const content = parsed.choices?.[0]?.message?.content ?? '';
    return this.parseReport(content, hits);
  }

  /**
   * Defensive parse of the synthesis model's JSON report. Tolerates prose
   * around the JSON, missing fields, and invalid enum values. Never throws
   * — a malformed report degrades to a minimal valid report so the
   * IdeaCheck still completes (status COMPLETED) rather than failing.
   */
  private parseReport(
    content: string,
    hits: Array<{ name: string; url: string; description?: string; stars?: number }>,
  ): IdeaValidationReport {
    let raw: Record<string, unknown> = {};
    try {
      const start = content.indexOf('{');
      const end = content.lastIndexOf('}');
      if (start !== -1 && end !== -1) {
        raw = JSON.parse(content.slice(start, end + 1)) as Record<string, unknown>;
      }
    } catch {
      // leave raw empty — we'll build a fallback report below
    }

    const verdict = this.parseVerdict(raw.verdict);
    const competitors = this.parseCompetitors(raw.competitors, hits);
    const openSourceReuse = this.parseReuse(raw.openSourceReuse, competitors);
    const differentiationScore = this.parseScore(
      raw.differentiationScore,
      verdict,
      competitors.length,
    );
    const summary =
      typeof raw.summary === 'string' && raw.summary.trim().length > 0
        ? raw.summary.trim().slice(0, 4000)
        : this.fallbackSummary(verdict, competitors.length);
    const differentiation =
      typeof raw.differentiation === 'string' && raw.differentiation.trim().length > 0
        ? raw.differentiation.trim().slice(0, 2000)
        : summary.slice(0, 500);

    return {
      verdict,
      summary,
      differentiation,
      differentiationScore,
      competitors,
      openSourceReuse,
    };
  }

  private parseVerdict(value: unknown): IdeaVerdict {
    if (typeof value === 'string') {
      const v = value.toLowerCase();
      if (v === 'novel' || v === 'empty' || v === 'moderate' || v === 'crowded') return v;
    }
    return 'moderate';
  }

  private parseCompetitors(
    value: unknown,
    hits: Array<{ name: string; url: string; description?: string; stars?: number }>,
  ): CompetitorEntry[] {
    const out: CompetitorEntry[] = [];
    if (Array.isArray(value)) {
      for (const item of value) {
        if (!item || typeof item !== 'object') continue;
        const c = item as Record<string, unknown>;
        const name = typeof c.name === 'string' ? c.name : '';
        const url = typeof c.url === 'string' ? c.url : '';
        if (!name || !url) continue;
        out.push({
          name: name.slice(0, 200),
          type: this.parseCompetitorType(c.type, url),
          url: url.slice(0, 500),
          description: typeof c.description === 'string' ? c.description.slice(0, 600) : '',
          stars: typeof c.stars === 'number' ? c.stars : undefined,
          traction: typeof c.traction === 'string' ? c.traction.slice(0, 300) : undefined,
          funding: typeof c.funding === 'string' ? c.funding.slice(0, 200) : undefined,
          differentiation:
            typeof c.differentiation === 'string' ? c.differentiation.slice(0, 600) : '',
        });
        if (out.length >= 12) break;
      }
    }
    // If the model returned nothing usable, seed from the raw hits.
    if (out.length === 0) {
      for (const h of hits.slice(0, 8)) {
        out.push({
          name: h.name.slice(0, 200),
          type: this.parseCompetitorType(undefined, h.url),
          url: h.url.slice(0, 500),
          description: (h.description ?? '').slice(0, 600),
          stars: h.stars,
          differentiation: '',
        });
      }
    }
    return out;
  }

  private parseCompetitorType(value: unknown, url: string): 'oss' | 'product' | 'startup' {
    if (value === 'oss' || value === 'product' || value === 'startup') return value;
    return url.includes('github.com') ? 'oss' : 'product';
  }

  private parseReuse(value: unknown, competitors: CompetitorEntry[]): OpenSourceReuseEntry[] {
    const out: OpenSourceReuseEntry[] = [];
    if (Array.isArray(value)) {
      for (const item of value) {
        if (!item || typeof item !== 'object') continue;
        const r = item as Record<string, unknown>;
        const repo = typeof r.repo === 'string' ? r.repo : '';
        if (!repo) continue;
        out.push({
          repo: repo.slice(0, 200),
          license: typeof r.license === 'string' ? r.license.slice(0, 80) : 'unknown',
          whatToReuse: typeof r.whatToReuse === 'string' ? r.whatToReuse.slice(0, 400) : '',
          modulePath: typeof r.modulePath === 'string' ? r.modulePath.slice(0, 200) : undefined,
          savedTimeEstimate: typeof r.savedTimeEstimate === 'string' ? r.savedTimeEstimate.slice(0, 80) : undefined,
          lastPushedAt: typeof r.lastPushedAt === 'string' ? r.lastPushedAt.slice(0, 40) : undefined,
          stars: typeof r.stars === 'number' ? r.stars : undefined,
        });
        if (out.length >= 8) break;
      }
    }
    // Seed from OSS competitors if the model produced nothing.
    if (out.length === 0) {
      for (const c of competitors) {
        if (c.type !== 'oss') continue;
        out.push({
          repo: c.name,
          license: 'unknown',
          whatToReuse: 'Review the repo for reusable modules.',
        });
        if (out.length >= 5) break;
      }
    }
    return out;
  }

  private parseScore(value: unknown, verdict: IdeaVerdict, competitorCount: number): number {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return Math.max(0, Math.min(100, Math.round(value)));
    }
    // Heuristic from verdict + count.
    switch (verdict) {
      case 'novel':
        return 90;
      case 'empty':
        return 70;
      case 'moderate':
        return Math.max(40, 65 - competitorCount * 2);
      case 'crowded':
      default:
        return Math.max(10, 35 - competitorCount * 2);
    }
  }

  private fallbackSummary(verdict: IdeaVerdict, competitorCount: number): string {
    return (
      `I ran a check across GitHub and the web — found ${competitorCount} similar project(s). ` +
      `Verdict: ${verdict}. See the competitor list for what exists vs. what's novel about your idea.`
    );
  }

  // -- utils ------------------------------------------------------------

  private async setStatus(id: string, status: IdeaCheckStatus): Promise<void> {
    try {
      await this.prisma.ideaCheck.update({ where: { id }, data: { status } });
    } catch (err) {
      this.logger.warn(
        `setStatus(${id}, ${status}) failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private hashIdea(ideaText: string): string {
    return createHash('sha256').update(ideaText.trim().toLowerCase(), 'utf8').digest('hex');
  }
}
