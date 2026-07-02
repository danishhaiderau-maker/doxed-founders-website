import { founderNodeAuthHeader } from '@dcf/founder-vault';
import { ollamaChat, type OllamaConfig } from './ollama-client';
import { throwIfFounderNodeAuthResponse } from './sync-client';
import { InferenceUsageReporter } from './inference-usage-reporter';

function apiBase(apiBaseUrl: string, path: string): string {
  const base = apiBaseUrl.replace(/\/$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

type PendingJob = {
  id: string;
  system: string;
  userPrompt: string;
  model: string | null;
};

export async function fetchPendingInferenceJob(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
): Promise<PendingJob | null> {
  const res = await fetch(apiBase(apiBaseUrl, '/api/founder-node/inference/pending'), {
    headers: { Authorization: founderNodeAuthHeader(nodeId, nodeToken) },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throwIfFounderNodeAuthResponse(res.status, text);
    return null;
  }
  const body = (await res.json().catch(() => null)) as PendingJob | null;
  if (!body?.id) return null;
  return body;
}

export async function completeInferenceJob(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  jobId: string,
  input: { result?: string; error?: string },
): Promise<void> {
  const res = await fetch(apiBase(apiBaseUrl, `/api/founder-node/inference/${jobId}/complete`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: founderNodeAuthHeader(nodeId, nodeToken),
    },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Inference complete failed (${res.status}): ${text}`);
  }
}

/**
 * Pull one pending inference job from the cloud, run it through Ollama, and
 * report the real token usage back to the platform. Usage is enqueued into the
 * shared `InferenceUsageReporter` regardless of whether `completeInferenceJob`
 * succeeds, so local inference always counts toward the adoption chart even if
 * the result upload fails.
 */
export async function processPendingInference(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  ollama: OllamaConfig,
  usageReporter?: InferenceUsageReporter,
): Promise<boolean> {
  const job = await fetchPendingInferenceJob(apiBaseUrl, nodeId, nodeToken);
  if (!job) return false;

  const model = job.model?.trim() || ollama.model;
  try {
    const { text, usage } = await ollamaChat({ ...ollama, model }, job.system, job.userPrompt);
    if (usageReporter) {
      usageReporter.enqueue({
        promptTokens: usage.promptTokens,
        completionTokens: usage.completionTokens,
        provider: 'ollama',
        model: usage.model,
        source: 'founder_node_local',
      });
    }
    await completeInferenceJob(apiBaseUrl, nodeId, nodeToken, job.id, { result: text });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Ollama inference failed';
    await completeInferenceJob(apiBaseUrl, nodeId, nodeToken, job.id, { error: message });
  }
  return true;
}
