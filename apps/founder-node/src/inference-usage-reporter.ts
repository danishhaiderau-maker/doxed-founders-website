import { founderNodeAuthHeader } from '@dcf/founder-vault';
import { throwIfFounderNodeAuthResponse } from './sync-client';
import type { OllamaUsage } from './ollama-client';

/**
 * Founder Node local inference (Ollama / BYO local model) runs on the user's
 * laptop, not in the cloud API. The cloud adoption chart sums `AiTokenUsageLog`
 * rows, so to count local inference we POST real usage counts back to the
 * platform via this dedicated channel. The server receiver calls
 * `PlatformAdoptionService.recordAiUsage` with `source: 'founder_node_local'`.
 *
 * Reports are batched in-memory and flushed periodically (or immediately when
 * the buffer hits a size limit) to keep heartbeat-cycles cheap and to survive
 * transient cloud failures (usage is retried on the next flush).
 */

export type InferenceUsageEntry = {
  promptTokens: number;
  completionTokens: number;
  provider: string;
  model: string;
  source?: string;
  projectId?: string | null;
  occurredAt?: string;
};

const MAX_BUFFER = 50;
const FLUSH_PATH = '/api/founder-node/inference-usage';

function apiBase(apiBaseUrl: string, path: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export function usageFromOllama(usage: OllamaUsage, source = 'copilot_via_founder_node'): InferenceUsageEntry {
  return {
    promptTokens: usage.promptTokens,
    completionTokens: usage.completionTokens,
    provider: 'ollama',
    model: usage.model,
    source,
  };
}

export class InferenceUsageReporter {
  private buffer: InferenceUsageEntry[] = [];

  enqueue(entry: InferenceUsageEntry): void {
    if (entry.promptTokens <= 0 && entry.completionTokens <= 0) return;
    this.buffer.push(entry);
    if (this.buffer.length > MAX_BUFFER) {
      // Drop oldest if unbounded growth — should never happen with periodic flush.
      this.buffer.splice(0, this.buffer.length - MAX_BUFFER);
    }
  }

  pendingCount(): number {
    return this.buffer.length;
  }

  async flush(apiBaseUrl: string, nodeId: string, nodeToken: string): Promise<number> {
    if (this.buffer.length === 0) return 0;
    const entries = this.buffer.splice(0, this.buffer.length);
    try {
      const res = await fetch(apiBase(apiBaseUrl, FLUSH_PATH), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: founderNodeAuthHeader(nodeId, nodeToken),
        },
        body: JSON.stringify({ entries }),
        signal: AbortSignal.timeout(15_000),
      });
      if (res.status === 401 || res.status === 403) {
        // Re-enqueue so a future re-pair can retry — but bail out to surface auth.
        this.buffer.unshift(...entries);
        const text = await res.text().catch(() => '');
        throwIfFounderNodeAuthResponse(res.status, text);
        return 0;
      }
      if (!res.ok) {
        // Re-enqueue for next cycle; the platform may be temporarily unavailable.
        this.buffer.unshift(...entries);
        const text = await res.text().catch(() => '');
        console.warn(`Inference usage flush failed (${res.status}): ${text.slice(0, 200)}`);
        return 0;
      }
      return entries.length;
    } catch (err) {
      // Network error — keep the entries for the next flush.
      this.buffer.unshift(...entries);
      console.warn('Inference usage flush error:', err);
      return 0;
    }
  }
}
