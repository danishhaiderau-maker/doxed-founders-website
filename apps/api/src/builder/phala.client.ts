export type PhalaCredentialMeta = {
  accountName: string;
  inferenceUrl: string;
  model: string;
};

export const DEFAULT_PHALA_INFERENCE_URL = 'https://api.redpill.ai/v1';
export const DEFAULT_PHALA_MODEL = 'phala/deepseek-chat-v3-0324';

export function normalizePhalaBaseUrl(url?: string | null): string {
  const raw = (url?.trim() || DEFAULT_PHALA_INFERENCE_URL).replace(/\/$/, '');
  return raw.endsWith('/v1') ? raw : `${raw}/v1`;
}

export async function verifyPhalaConnection(input: {
  apiKey: string;
  inferenceUrl?: string | null;
}): Promise<{ ok: true } | { ok: false; reason: string }> {
  const baseUrl = normalizePhalaBaseUrl(input.inferenceUrl);
  try {
    const res = await fetch(`${baseUrl}/models`, {
      headers: { Authorization: `Bearer ${input.apiKey.trim()}` },
      signal: AbortSignal.timeout(12_000),
    });
    if (res.ok) return { ok: true };
    const ping = await fetch(`${baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${input.apiKey.trim()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: DEFAULT_PHALA_MODEL,
        messages: [{ role: 'user', content: 'ping' }],
        max_tokens: 8,
      }),
      signal: AbortSignal.timeout(15_000),
    });
    if (ping.ok) return { ok: true };
    return { ok: false, reason: `Phala API rejected key (HTTP ${res.status})` };
  } catch (err) {
    return {
      ok: false,
      reason: err instanceof Error ? err.message : 'Could not reach Phala inference URL',
    };
  }
}

export async function callPhalaChat(input: {
  apiKey: string;
  inferenceUrl?: string | null;
  model?: string | null;
  system: string;
  userPrompt: string;
}): Promise<string | null> {
  const baseUrl = normalizePhalaBaseUrl(input.inferenceUrl);
  const model = input.model?.trim() || DEFAULT_PHALA_MODEL;
  const res = await fetch(`${baseUrl}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${input.apiKey.trim()}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: input.system },
        { role: 'user', content: input.userPrompt },
      ],
      temperature: 0.4,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Phala HTTP ${res.status}${body ? `: ${body.slice(0, 180)}` : ''}`);
  }
  const data = (await res.json()) as { choices?: { message?: { content?: string } }[] };
  return data.choices?.[0]?.message?.content ?? null;
}
