import { Injectable, Logger } from '@nestjs/common';
import { FounderNodeInferenceStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdoptionService } from '../projects/platform-adoption.service';

const ONLINE_WINDOW_MS = 3 * 60 * 1000;
const JOB_TTL_MS = 2 * 60 * 1000;
const POLL_MS = 400;

export type FounderNodeUsageEntry = {
  promptTokens: number;
  completionTokens: number;
  provider: string;
  model?: string;
  source?: string;
  billingSource?: string;
  projectId?: string | null;
};

@Injectable()
export class FounderNodeInferenceService {
  private readonly logger = new Logger(FounderNodeInferenceService.name);
  constructor(
    private readonly prisma: PrismaService,
    private readonly adoption: PlatformAdoptionService,
  ) {}

  async isOllamaReady(userId: string): Promise<boolean> {
    const node = await this.findOnlineOllamaNode(userId);
    if (node) return true;

    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'ollama' } },
    });
    const meta = cred?.metadata as { baseUrl?: string } | null;
    return Boolean(meta?.baseUrl?.trim());
  }

  async getOllamaStatus(userId: string) {
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });
    const onlineNode = nodes.find(
      (n) =>
        n.ollamaEnabled &&
        n.lastSeenAt != null &&
        Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
    );

    const cred = await this.prisma.integrationCredential.findUnique({
      where: { userId_provider: { userId, provider: 'ollama' } },
    });
    const meta = cred?.metadata as { baseUrl?: string; model?: string } | null;

    return {
      paired: nodes.length > 0,
      online: nodes.some(
        (n) => n.lastSeenAt != null && Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
      ),
      ollamaReady: Boolean(onlineNode) || Boolean(meta?.baseUrl?.trim()),
      ollamaModel: onlineNode?.ollamaModel ?? meta?.model ?? null,
      nodeLabel: onlineNode?.label ?? nodes[0]?.label ?? null,
      directOllamaUrl: meta?.baseUrl ?? null,
    };
  }

  async runViaFounderNode(
    userId: string,
    system: string,
    userPrompt: string,
    preferredModel?: string | null,
  ): Promise<{ ok: true; text: string } | { ok: false; errors: string[] }> {
    const node = await this.findOnlineOllamaNode(userId);
    if (!node) {
      return { ok: false, errors: ['Founder Node with Ollama is offline — open the tray app and ensure Ollama is running'] };
    }

    const model = preferredModel?.trim() || node.ollamaModel || 'llama3.2';
    const job = await this.prisma.founderNodeInferenceJob.create({
      data: {
        userId,
        nodeId: node.nodeId,
        system,
        userPrompt,
        model,
        expiresAt: new Date(Date.now() + JOB_TTL_MS),
      },
    });

    const result = await this.waitForJob(job.id);
    if (result.ok) return { ok: true, text: result.text };
    return { ok: false, errors: [result.error] };
  }

  async claimPending(nodeId: string) {
    const now = new Date();
    const job = await this.prisma.founderNodeInferenceJob.findFirst({
      where: {
        nodeId,
        status: FounderNodeInferenceStatus.PENDING,
        expiresAt: { gt: now },
      },
      orderBy: { createdAt: 'asc' },
    });
    if (!job) return null;

    await this.prisma.founderNodeInferenceJob.update({
      where: { id: job.id },
      data: { status: FounderNodeInferenceStatus.PROCESSING },
    });

    return {
      id: job.id,
      system: job.system,
      userPrompt: job.userPrompt,
      model: job.model,
    };
  }

  async completeJob(nodeId: string, jobId: string, input: { result?: string; error?: string }) {
    const job = await this.prisma.founderNodeInferenceJob.findFirst({
      where: { id: jobId, nodeId },
    });
    if (!job) return { success: false };

    if (input.error?.trim()) {
      await this.prisma.founderNodeInferenceJob.update({
        where: { id: jobId },
        data: {
          status: FounderNodeInferenceStatus.FAILED,
          error: input.error.trim().slice(0, 500),
        },
      });
      return { success: true };
    }

    const text = input.result?.trim();
    if (!text) {
      await this.prisma.founderNodeInferenceJob.update({
        where: { id: jobId },
        data: {
          status: FounderNodeInferenceStatus.FAILED,
          error: 'Empty Ollama response',
        },
      });
      return { success: true };
    }

    await this.prisma.founderNodeInferenceJob.update({
      where: { id: jobId },
      data: {
        status: FounderNodeInferenceStatus.DONE,
        result: text,
      },
    });
    return { success: true };
  }

  private async waitForJob(jobId: string, timeoutMs = 45_000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const job = await this.prisma.founderNodeInferenceJob.findUnique({ where: { id: jobId } });
      if (!job) return { ok: false as const, error: 'Inference job not found' };
      if (job.status === FounderNodeInferenceStatus.DONE && job.result?.trim()) {
        return { ok: true as const, text: job.result.trim() };
      }
      if (job.status === FounderNodeInferenceStatus.FAILED) {
        return { ok: false as const, error: job.error ?? 'Ollama inference failed' };
      }
      if (job.status === FounderNodeInferenceStatus.EXPIRED) {
        return { ok: false as const, error: 'Inference timed out — is Founder Node running?' };
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }

    await this.prisma.founderNodeInferenceJob.update({
      where: { id: jobId },
      data: { status: FounderNodeInferenceStatus.EXPIRED, error: 'Timed out waiting for Founder Node' },
    });
    return { ok: false as const, error: 'Timed out waiting for Founder Node + Ollama' };
  }

  private async findOnlineOllamaNode(userId: string) {
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId, ollamaEnabled: true },
      orderBy: { lastSeenAt: 'desc' },
    });
    return (
      nodes.find(
        (n) => n.lastSeenAt != null && Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
      ) ?? null
    );
  }

  /**
   * Persist a batch of local-inference token usage reports pushed by a paired
   * Founder Node (Ollama / BYO local model). Each entry becomes one
   * `AiTokenUsageLog` row via `PlatformAdoptionService.recordAiUsage`, so it
   * flows into the adoption chart totals. Best-effort: never throws into the
   * request path; returns the number of entries actually recorded.
   */
  async recordUsageBatch(userId: string, entries: FounderNodeUsageEntry[]): Promise<{
    received: number;
    recorded: number;
  }> {
    const received = entries.length;
    if (received === 0) return { received: 0, recorded: 0 };

    let recorded = 0;
    for (const entry of entries) {
      if (!entry || (entry.promptTokens <= 0 && entry.completionTokens <= 0)) continue;
      try {
        await this.adoption.recordAiUsage({
          userId,
          provider: entry.provider || 'ollama',
          source: entry.source || 'founder_node_local',
          promptTokens: Math.max(0, Math.floor(entry.promptTokens)),
          completionTokens: Math.max(0, Math.floor(entry.completionTokens)),
          projectId: entry.projectId ?? null,
          billingSource: entry.billingSource ?? 'founder_os_local',
        });
        recorded += 1;
      } catch (err) {
        this.logger.warn(
          `recordAiUsage failed for founder-node entry: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
    return { received, recorded };
  }
}
