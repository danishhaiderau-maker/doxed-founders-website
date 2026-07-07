import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

export type AiProxyProviderBreakdown = {
  provider: string;
  requests: number;
  promptTokens: number;
  completionTokens: number;
  ddollarSpent: number;
};

export type AiProxyDayBreakdown = {
  day: string; // YYYY-MM-DD
  requests: number;
  promptTokens: number;
  completionTokens: number;
  ddollarSpent: number;
};

export type AiProxyUsageSummary = {
  totals: {
    requests: number;
    promptTokens: number;
    completionTokens: number;
    ddollarSpent: number;
    estimatedCursorProCost: number;
  };
  providers: AiProxyProviderBreakdown[];
  daily: AiProxyDayBreakdown[];
};

const CURSOR_PRO_MONTHLY_USD = 20; // retail price reference
const DAYS_PER_MONTH = 30;
const USD_PER_1K_TOKENS_BLENDED = 0.003; // rough GLM blended rate

/**
 * Aggregates AI proxy usage for the dashboard at /settings/ai-usage.
 * Reads from `AiTokenUsageLog` (token counts) + `PointLedger` (DDollar spend).
 */
@Injectable()
export class AiProxyUsageService {
  private readonly logger = new Logger(AiProxyUsageService.name);

  constructor(private readonly prisma: PrismaService) {}

  async summarize(
    userId: string,
    range: { from: Date; to: Date },
  ): Promise<AiProxyUsageSummary> {
    const [tokenRows, ddollarAgg] = await Promise.all([
      this.prisma.aiTokenUsageLog.findMany({
        where: { userId, createdAt: { gte: range.from, lt: range.to } },
        select: {
          provider: true,
          source: true,
          promptTokens: true,
          completionTokens: true,
          createdAt: true,
        },
      }),
      this.prisma.pointLedger.aggregate({
        where: {
          userId,
          actionKey: 'AI_SPEND',
          createdAt: { gte: range.from, lt: range.to },
        },
        _sum: { amount: true },
      }),
    ]);

    const totalPrompt = tokenRows.reduce((s, r) => s + r.promptTokens, 0);
    const totalCompletion = tokenRows.reduce((s, r) => s + r.completionTokens, 0);
    const totalRequests = tokenRows.length;
    const ddollarSpent = Math.abs(ddollarAgg._sum.amount ?? 0);

    // Provider breakdown
    const byProvider = new Map<string, AiProxyProviderBreakdown>();
    for (const row of tokenRows) {
      const key = row.provider || 'unknown';
      const entry =
        byProvider.get(key) ??
        {
          provider: key,
          requests: 0,
          promptTokens: 0,
          completionTokens: 0,
          ddollarSpent: 0,
        };
      entry.requests += 1;
      entry.promptTokens += row.promptTokens;
      entry.completionTokens += row.completionTokens;
      byProvider.set(key, entry);
    }

    // Distribute DDollar spend pro-rata across providers by request count
    for (const entry of byProvider.values()) {
      entry.ddollarSpent =
        totalRequests > 0
          ? Math.round((entry.requests / totalRequests) * ddollarSpent)
          : 0;
    }

    // Daily breakdown
    const byDay = new Map<string, AiProxyDayBreakdown>();
    for (const row of tokenRows) {
      const day = row.createdAt.toISOString().slice(0, 10);
      const entry =
        byDay.get(day) ??
        {
          day,
          requests: 0,
          promptTokens: 0,
          completionTokens: 0,
          ddollarSpent: 0,
        };
      entry.requests += 1;
      entry.promptTokens += row.promptTokens;
      entry.completionTokens += row.completionTokens;
      byDay.set(day, entry);
    }
    const daily = Array.from(byDay.values())
      .sort((a, b) => a.day.localeCompare(b.day))
      .slice(-30);

    // Estimated retail cost — what this would have cost on Cursor Pro
    // (very rough: blended GLM rate per 1K tokens, compared to $20/mo flat)
    const blendedTokens = totalPrompt + totalCompletion;
    const estimatedRetailUsd =
      (blendedTokens / 1000) * USD_PER_1K_TOKENS_BLENDED;
    const cursorProEquivalent = (estimatedRetailUsd / CURSOR_PRO_MONTHLY_USD) * DAYS_PER_MONTH;

    return {
      totals: {
        requests: totalRequests,
        promptTokens: totalPrompt,
        completionTokens: totalCompletion,
        ddollarSpent,
        estimatedCursorProCost: Number.isFinite(cursorProEquivalent)
          ? Number(cursorProEquivalent.toFixed(2))
          : 0,
      },
      providers: Array.from(byProvider.values()).sort(
        (a, b) => b.requests - a.requests,
      ),
      daily,
    };
  }
}
