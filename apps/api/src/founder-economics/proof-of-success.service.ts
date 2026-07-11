/**
 * Proof of Success Service — verifier implementations + record persistence.
 *
 * Verifiers prefer live external APIs when credentials exist:
 *   - GitHub stars / commits via GitHubApiService (gitHubConnection) or
 *     IntegrationCredential provider `github`
 *   - Vercel deployments via IntegrationCredential provider `vercel`
 *   - Stripe ARR / paying users via IntegrationCredential provider `stripe`
 *
 * When credentials are missing, callers may still supply a verifiedMetric
 * manually (MANUAL_AUDIT / demo). Auto-fetch never invents metrics.
 */

import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { GitHubApiService } from '../github/github-api.service';
import { DdollarEngineService } from './ddollar-engine.service';
import {
  MILESTONE_TIERS,
  suggestMilestoneGrant,
  type ProofOfSuccessResult,
  type ProofType,
} from '@dcf/utils';

const VALID_PROOF_TYPES: ProofType[] = [
  'STRIPE_ARR',
  'STRIPE_PAYING_USERS',
  'GITHUB_REPO_STARS',
  'GITHUB_COMMITS',
  'VERCEL_DEPLOYMENTS',
  'APP_STORE_DOWNLOADS',
  'GOOGLE_ANALYTICS_USERS',
  'MANUAL_AUDIT',
];

function assertProofType(value: string): asserts value is ProofType {
  if (!VALID_PROOF_TYPES.includes(value as ProofType)) {
    throw new Error(`Invalid proof type: ${value}`);
  }
}

@Injectable()
export class ProofOfSuccessService {
  private readonly logger = new Logger(ProofOfSuccessService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly ddollarEngine: DdollarEngineService,
    private readonly github: GitHubApiService,
  ) {}

  /**
   * Verify a milestone and persist the record.
   * When `verifiedMetric` is omitted/0, attempts a live fetch from the
   * matching integration. Manual override still works for demos / audits.
   */
  async verify(
    userId: string,
    proofType: ProofType,
    externalId: string,
    verifiedMetric?: number,
    rawPayload?: unknown,
  ): Promise<ProofOfSuccessResult> {
    assertProofType(proofType);

    let metric = typeof verifiedMetric === 'number' && verifiedMetric > 0 ? verifiedMetric : 0;
    let payload: unknown = rawPayload;
    let fetchDetail: string | undefined;

    if (metric <= 0 && proofType !== 'MANUAL_AUDIT') {
      const fetched = await this.fetchMetricFromExternal(userId, proofType, externalId);
      if (fetched) {
        metric = fetched.metric;
        payload = fetched.rawPayload;
        fetchDetail = fetched.detail;
      }
    }

    if (metric <= 0) {
      return {
        verified: false,
        proofType,
        externalId,
        verifiedMetric: 0,
        metricLabel: '',
        suggestedDdollarGrant: 0,
        failureReason:
          fetchDetail ??
          'No verified metric — connect GitHub/Vercel/Stripe credentials or supply verifiedMetric.',
      };
    }

    const suggestion = suggestMilestoneGrant(proofType, metric);
    if (!suggestion) {
      return {
        verified: false,
        proofType,
        externalId,
        verifiedMetric: metric,
        metricLabel: '',
        suggestedDdollarGrant: 0,
        failureReason: 'Metric below minimum milestone tier.',
        rawPayload: payload,
      };
    }

    const record = await this.prisma.proofOfSuccess.upsert({
      where: {
        proofType_externalId_userId: { proofType, externalId, userId },
      },
      create: {
        userId,
        proofType,
        externalId,
        verifiedMetric: metric,
        metricLabel: suggestion.metricLabel,
        multiplier: suggestion.multiplier,
        verifiedData: payload ? (payload as object) : undefined,
        reverified: false,
      },
      update: {
        verifiedMetric: metric,
        metricLabel: suggestion.metricLabel,
        multiplier: suggestion.multiplier,
        verifiedData: payload ? (payload as object) : undefined,
        reverified: true,
      },
    });

    await this.ddollarEngine.grant(
      userId,
      'COMPANY_MILESTONE_VERIFIED',
      `${proofType}:${externalId}`,
      suggestion.ddollar,
      {
        proofType,
        proofData: { recordId: record.id, verifiedMetric: metric, multiplier: suggestion.multiplier },
        label: `Verified milestone: ${suggestion.metricLabel} = ${metric}`,
      },
    );

    return {
      verified: true,
      proofType,
      externalId,
      verifiedMetric: metric,
      metricLabel: suggestion.metricLabel,
      suggestedDdollarGrant: suggestion.ddollar,
      rawPayload: payload,
    };
  }

  /**
   * Best-effort live metric fetch. Returns null when credentials/APIs are
   * unavailable — never fabricates a metric.
   */
  private async fetchMetricFromExternal(
    userId: string,
    proofType: ProofType,
    externalId: string,
  ): Promise<{ metric: number; rawPayload: unknown; detail: string } | null> {
    try {
      switch (proofType) {
        case 'GITHUB_REPO_STARS':
          return this.fetchGithubStars(userId, externalId);
        case 'GITHUB_COMMITS':
          return this.fetchGithubCommits(userId, externalId);
        case 'VERCEL_DEPLOYMENTS':
          return this.fetchVercelDeployments(userId, externalId);
        case 'STRIPE_ARR':
        case 'STRIPE_PAYING_USERS':
          return this.fetchStripeMetric(userId, proofType, externalId);
        default:
          return null;
      }
    } catch (err) {
      this.logger.warn(
        `Proof fetch failed for ${proofType}:${externalId}: ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
  }

  private async resolveGithubToken(userId: string): Promise<string | null> {
    const fromConn = await this.github.getToken(userId);
    if (fromConn) return fromConn;
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'github' } },
    });
    return cred?.token?.trim() || null;
  }

  private async fetchGithubStars(
    userId: string,
    repo: string,
  ): Promise<{ metric: number; rawPayload: unknown; detail: string } | null> {
    const token = await this.resolveGithubToken(userId);
    if (!token) return null;
    const res = await fetch(`https://api.github.com/repos/${repo}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      signal: AbortSignal.timeout(15_000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { stargazers_count?: number; full_name?: string };
    const stars = data.stargazers_count ?? 0;
    return {
      metric: stars,
      rawPayload: data,
      detail: `GitHub stars for ${data.full_name ?? repo}: ${stars}`,
    };
  }

  private async fetchGithubCommits(
    userId: string,
    repo: string,
  ): Promise<{ metric: number; rawPayload: unknown; detail: string } | null> {
    const commits = await this.github.listCommits(userId, repo, 100);
    if (!commits.length) {
      // Fall back to IntegrationCredential token + REST if gitHubConnection empty.
      const token = await this.resolveGithubToken(userId);
      if (!token) return null;
      const res = await fetch(`https://api.github.com/repos/${repo}/commits?per_page=100`, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
        },
        signal: AbortSignal.timeout(15_000),
      });
      if (!res.ok) return null;
      const data = (await res.json()) as unknown[];
      return {
        metric: Array.isArray(data) ? data.length : 0,
        rawPayload: data,
        detail: `GitHub commits (page) for ${repo}: ${Array.isArray(data) ? data.length : 0}`,
      };
    }
    return {
      metric: commits.length,
      rawPayload: commits,
      detail: `GitHub commits for ${repo}: ${commits.length}`,
    };
  }

  private async fetchVercelDeployments(
    userId: string,
    projectIdOrName: string,
  ): Promise<{ metric: number; rawPayload: unknown; detail: string } | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'vercel' } },
    });
    const token = cred?.token?.trim();
    if (!token) return null;
    const url = `https://api.vercel.com/v6/deployments?projectId=${encodeURIComponent(projectIdOrName)}&limit=100`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(15_000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { deployments?: unknown[] };
    const count = Array.isArray(data.deployments) ? data.deployments.length : 0;
    return {
      metric: count,
      rawPayload: data,
      detail: `Vercel deployments for ${projectIdOrName}: ${count}`,
    };
  }

  private async fetchStripeMetric(
    userId: string,
    proofType: 'STRIPE_ARR' | 'STRIPE_PAYING_USERS',
    accountHint: string,
  ): Promise<{ metric: number; rawPayload: unknown; detail: string } | null> {
    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'stripe' } },
    });
    const token = cred?.token?.trim();
    if (!token) return null;

    if (proofType === 'STRIPE_PAYING_USERS') {
      const res = await fetch('https://api.stripe.com/v1/customers?limit=100', {
        headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(15_000),
      });
      if (!res.ok) return null;
      const data = (await res.json()) as { data?: unknown[]; has_more?: boolean };
      const count = Array.isArray(data.data) ? data.data.length : 0;
      return {
        metric: count,
        rawPayload: data,
        detail: `Stripe customers (page) for ${accountHint}: ${count}${data.has_more ? '+' : ''}`,
      };
    }

    // ARR approximation: sum active subscription amounts * 12 (first page).
    const res = await fetch('https://api.stripe.com/v1/subscriptions?status=active&limit=100', {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(15_000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      data?: { items?: { data?: { price?: { unit_amount?: number; recurring?: { interval?: string } } }[] } }[];
    };
    let monthlyCents = 0;
    for (const sub of data.data ?? []) {
      for (const item of sub.items?.data ?? []) {
        const amount = item.price?.unit_amount ?? 0;
        const interval = item.price?.recurring?.interval;
        if (interval === 'year') monthlyCents += Math.round(amount / 12);
        else monthlyCents += amount;
      }
    }
    const arr = Math.round((monthlyCents / 100) * 12);
    return {
      metric: arr,
      rawPayload: data,
      detail: `Stripe ARR estimate for ${accountHint}: $${arr}`,
    };
  }

  /** List a founder's verified milestones. */
  async founderProofs(userId: string) {
    return this.prisma.proofOfSuccess.findMany({
      where: { userId },
      orderBy: { verifiedAt: 'desc' },
    });
  }

  /** Count of verified milestones for a founder — feeds reputation multiplier. */
  async verifiedMilestoneCount(userId: string): Promise<number> {
    return this.prisma.proofOfSuccess.count({ where: { userId } });
  }

  /** All milestone tiers — for dashboard rendering. */
  milestoneTiersTable() {
    return MILESTONE_TIERS;
  }

  /** Lookup a single record by id (used by the controller GET). */
  async getRecord(id: string) {
    const record = await this.prisma.proofOfSuccess.findUnique({ where: { id } });
    if (!record) throw new NotFoundException('Proof of success record not found.');
    return record;
  }
}
