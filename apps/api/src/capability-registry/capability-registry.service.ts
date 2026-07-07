import { Injectable } from '@nestjs/common';
import { Capability } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import type {
  AiRuntimeIntent,
  CapabilityRequirement,
} from './capability-registry.types';

/**
 * Capability Registry — kernel service backed by the Prisma `Capability` model.
 * See docs/KERNEL.md §8.
 */
@Injectable()
export class CapabilityRegistryService {
  constructor(private readonly prisma: PrismaService) {}

  async findAll(filter?: { onlyActive?: boolean }): Promise<Capability[]> {
    return this.prisma.capability.findMany({
      where: filter?.onlyActive ? { isActive: true } : undefined,
      orderBy: [{ provider: 'asc' }, { model: 'asc' }],
    });
  }

  async findByProvider(provider: string): Promise<Capability[]> {
    return this.prisma.capability.findMany({
      where: { provider },
      orderBy: { model: 'asc' },
    });
  }

  async findByProviderModel(
    provider: string,
    model: string,
  ): Promise<Capability | null> {
    return this.prisma.capability.findUnique({
      where: { provider_model: { provider, model } },
    });
  }

  /**
   * Layer 2 of the routing pipeline (docs/KERNEL.md §6): the capability gate.
   * Returns candidates whose declared capabilities satisfy every requirement,
   * sorted by the per-intent score (highest first).
   */
  async findBestForIntent(
    intent: AiRuntimeIntent,
    requirements: CapabilityRequirement[] = [],
  ): Promise<Capability[]> {
    const rows = await this.prisma.capability.findMany({
      where: { isActive: true },
    });
    const filtered = rows.filter((row) =>
      this.meetsAllRequirements(row, requirements),
    );
    const sorted = filtered.sort(
      (a, b) => this.intentScore(b, intent) - this.intentScore(a, intent),
    );
    return sorted;
  }

  /**
   * Update a capability's reputation after an outcome is observed. Owned by
   * the Learning Engine in Phase 4; exposed on the registry so the Flight
   * Recorder / AI Gateway can do fast feedback loops before that lands.
   *
   * Exponential moving average with alpha = 0.05:
   *   success: successRate = 0.95 * successRate + 0.05 * 1
   *   failure: successRate = 0.95 * successRate + 0.05 * 0
   *            retryRate   = 0.95 * retryRate   + 0.05 * 1
   *   sampleCount += 1
   */
  async updateReputation(id: string, success: boolean): Promise<void> {
    const current = await this.prisma.capability.findUnique({ where: { id } });
    if (!current) return;

    const alpha = 0.05;
    const successRate =
      (1 - alpha) * current.successRate + alpha * (success ? 1 : 0);
    const retryRate = success
      ? (1 - alpha) * current.retryRate
      : (1 - alpha) * current.retryRate + alpha * 1;

    await this.prisma.capability.update({
      where: { id },
      data: {
        successRate,
        retryRate,
        sampleCount: { increment: 1 },
      },
    });
  }

  private meetsAllRequirements(
    row: Capability,
    requirements: CapabilityRequirement[],
  ): boolean {
    return requirements.every((req) => this.meetsRequirement(row, req));
  }

  private meetsRequirement(
    row: Capability,
    req: CapabilityRequirement,
  ): boolean {
    if (req.toolUse && !row.toolUse) return false;
    if (req.jsonMode && !row.jsonMode) return false;
    if (req.largeContext && !row.largeContext) return false;
    if (req.vision && !row.vision) return false;
    return true;
  }

  private intentScore(row: Capability, intent: AiRuntimeIntent): number {
    switch (intent) {
      case 'code':
        return row.codeScore;
      case 'reasoning':
        return row.reasoningScore;
      case 'simple_qa':
        return row.simpleQaScore;
      case 'agent':
        return row.agentScore;
      case 'vision':
        return row.visionScore;
    }
  }
}
