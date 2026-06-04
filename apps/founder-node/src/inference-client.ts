import { founderNodeAuthHeader } from '@dcf/founder-vault';
import { ollamaChat, type OllamaConfig } from './ollama-client';
import { throwIfFounderNodeAuthResponse } from './sync-client';

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

export async function processPendingInference(
  apiBaseUrl: string,
  nodeId: string,
  nodeToken: string,
  ollama: OllamaConfig,
): Promise<boolean> {
  const job = await fetchPendingInferenceJob(apiBaseUrl, nodeId, nodeToken);
  if (!job) return false;

  const model = job.model?.trim() || ollama.model;
  try {
    const result = await ollamaChat({ ...ollama, model }, job.system, job.userPrompt);
    await completeInferenceJob(apiBaseUrl, nodeId, nodeToken, job.id, { result });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Ollama inference failed';
    await completeInferenceJob(apiBaseUrl, nodeId, nodeToken, job.id, { error: message });
  }
  return true;
}
