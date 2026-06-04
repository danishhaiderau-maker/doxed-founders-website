export const DEFAULT_JATEVO_BASE_URL = 'https://2.lb.jatevo.ai/v1';

export type JatevoVerifyResult =
  | { ok: true; accountName: string }
  | { ok: false; reason: string };

export function normalizeJatevoBaseUrl(url?: string | null): string {
  const raw = (url?.trim() || process.env.JATEVO_BASE_URL?.trim() || DEFAULT_JATEVO_BASE_URL).replace(
    /\/+$/,
    '',
  );
  return raw.endsWith('/v1') ? raw : `${raw}/v1`;
}

export function jatevoErrorMessage(status: number, body?: string): string {
  const snippet = body?.slice(0, 200);
  switch (status) {
    case 401:
      return 'Invalid Jatevo API key — use a sk-clb-… key from jatevo.ai';
    case 403:
      return 'Jatevo key disabled — refresh wallet verification on jatevo.ai';
    case 429:
      return 'Jatevo daily quota exhausted — wait for UTC reset or increase $JTVO holdings';
    case 502:
    case 504:
      return 'Jatevo upstream route unavailable — retry in a moment';
    default:
      return snippet ? `Jatevo HTTP ${status}: ${snippet}` : `Jatevo HTTP ${status}`;
  }
}

export async function verifyJatevoConnection(apiKey: string, baseUrl?: string): Promise<JatevoVerifyResult> {
  const root = normalizeJatevoBaseUrl(baseUrl);
  const res = await fetch(`${root}/models`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(12_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    return { ok: false, reason: jatevoErrorMessage(res.status, body) };
  }
  return { ok: true, accountName: 'Jatevo gateway' };
}

export async function callJatevoChat(params: {
  apiKey: string;
  system: string;
  userPrompt: string;
  model?: string;
  baseUrl?: string;
}): Promise<{ text: string; usage: { promptTokens: number; completionTokens: number } | null } | null> {
  const root = normalizeJatevoBaseUrl(params.baseUrl);
  const res = await fetch(`${root}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${params.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: params.model?.trim() || 'auto',
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
    throw new Error(jatevoErrorMessage(res.status, body));
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
