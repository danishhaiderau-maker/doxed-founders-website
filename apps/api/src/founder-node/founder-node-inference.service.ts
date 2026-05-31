import { Injectable } from '@nestjs/common';
import { FounderNodeInferenceStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

const ONLINE_WINDOW_MS = 3 * 60 * 1000;
const JOB_TTL_MS = 2 * 60 * 1000;
const POLL_MS = 400;

@Injectable()
export class FounderNodeInferenceService {
  constructor(private readonly prisma: PrismaService) {}

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
}
