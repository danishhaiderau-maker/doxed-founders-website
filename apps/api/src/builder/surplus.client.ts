export const DEFAULT_SURPLUS_BASE_URL = 'https://www.surplusintelligence.ai/api/inference/v1';

export type SurplusVerifyResult =
  | { ok: true; accountName: string }
  | { ok: false; reason: string };

export function normalizeSurplusBaseUrl(url?: string | null): string {
  const raw = (url?.trim() || process.env.SURPLUS_BASE_URL?.trim() || DEFAULT_SURPLUS_BASE_URL).replace(
    /\/+$/,
    '',
  );
  return raw.endsWith('/v1') ? raw : `${raw}/v1`;
}

export function surplusErrorMessage(status: number, body?: string): string {
  const snippet = body?.slice(0, 200);
  switch (status) {
    case 401:
      return 'Invalid Surplus API key — use an inf_… key from surplusintelligence.ai/buy';
    case 402:
      return 'Surplus balance empty — add funds at surplusintelligence.ai/buy';
    case 429:
      return 'Surplus rate limit — retry in a moment';
    default:
      return snippet ? `Surplus HTTP ${status}: ${snippet}` : `Surplus HTTP ${status}`;
  }
}

export async function verifySurplusConnection(apiKey: string, baseUrl?: string): Promise<SurplusVerifyResult> {
  const root = normalizeSurplusBaseUrl(baseUrl);
  const res = await fetch(`${root}/models`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(12_000),
  });
  if (!res.ok) {
    const ping = await fetch(`${root}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'claude-opus-4.8',
        messages: [{ role: 'user', content: 'ping' }],
        max_tokens: 8,
      }),
      signal: AbortSignal.timeout(12_000),
    });
    if (!ping.ok) {
      const body = await ping.text().catch(() => '');
      return { ok: false, reason: surplusErrorMessage(ping.status, body) };
    }
    return { ok: true, accountName: 'Surplus Intelligence' };
  }
  return { ok: true, accountName: 'Surplus Intelligence' };
}

export async function callSurplusChat(params: {
  apiKey: string;
  system: string;
  userPrompt: string;
  model?: string;
  baseUrl?: string;
}): Promise<{ text: string; usage: { promptTokens: number; completionTokens: number } | null } | null> {
  const root = normalizeSurplusBaseUrl(params.baseUrl);
  const res = await fetch(`${root}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${params.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: params.model?.trim() || 'claude-opus-4.8',
      messages: [
        { role: 'system', content: params.system },
        { role: 'user', content: params.userPrompt },
      ],
      temperature: 0.4,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(surplusErrorMessage(res.status, body));
  }
  const data = (await res.json()) as {
    choices?: { message?: { content?: string } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number };
  };
  const text = data.choices?.[0]?.message?.content;
  if (!text?.trim()) return null;
  const usage =
    data.usage != null
      ? {
          promptTokens: data.usage.prompt_tokens ?? 0,
          completionTokens: data.usage.completion_tokens ?? 0,
        }
      : null;
  return { text: text.trim(), usage };
}
