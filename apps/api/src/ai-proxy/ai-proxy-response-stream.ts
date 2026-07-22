import { AI_PROXY_DDOLLAR_COST, type AiProxyTier } from '@dcf/utils';
import type { Response } from 'express';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { ClientPromptEfficiencyEstimate } from './ai-proxy-efficiency';
import { estimateDeepseekInputSavingsUsd } from './deepseek-provider-cost';

type PipeAiProxySseResponseArgs = {
  res: Response;
  upstreamBody: ReadableStream<Uint8Array>;
  includeMetadata: boolean;
  requestId: string;
  status: number;
  tier: AiProxyTier;
  provider: string;
  model: string;
  routeCacheLevel?: 'hit' | 'partial' | 'miss';
  promptEfficiency?: ClientPromptEfficiencyEstimate;
  routePolicy?: 'free_flash_only' | 'managed_auto';
};

/** Own the Express response until the provider SSE stream reaches EOF. */
export async function pipeAiProxySseResponse({
  res,
  upstreamBody,
  includeMetadata,
  requestId,
  status,
  tier,
  provider,
  model,
  routeCacheLevel,
  promptEfficiency,
  routePolicy,
}: PipeAiProxySseResponseArgs): Promise<void> {
  res.status(status);
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  if (includeMetadata) {
    const ddollarCost = AI_PROXY_DDOLLAR_COST[tier] ?? 0;
    const inputCostComparison = provider === 'deepseek' && promptEfficiency
      ? estimateDeepseekInputSavingsUsd(model, promptEfficiency.avoidedTokens)
      : null;
    res.write(
      `data: ${JSON.stringify({
        founderOs: {
          requestId,
          tier,
          provider,
          model,
          ddollarCost,
          routeCacheLevel: routeCacheLevel ?? 'miss',
          ...(promptEfficiency ? { promptEfficiency } : {}),
          ...(inputCostComparison ? { inputCostComparison } : {}),
          ...(routePolicy ? { routePolicy } : {}),
        },
      })}\n\n`,
    );
  }

  const nodeStream = Readable.fromWeb(
    upstreamBody as unknown as Parameters<typeof Readable.fromWeb>[0],
  );
  await pipeline(nodeStream, res);
}
