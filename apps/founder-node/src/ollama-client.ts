const DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434';
const DEFAULT_MODEL = 'llama3.2';

export type OllamaConfig = {
  enabled: boolean;
  baseUrl: string;
  model: string;
};

export function defaultOllamaConfig(): OllamaConfig {
  return { enabled: true, baseUrl: DEFAULT_OLLAMA_URL, model: DEFAULT_MODEL };
}

export async function probeOllama(baseUrl = DEFAULT_OLLAMA_URL): Promise<OllamaConfig | null> {
  try {
    const url = baseUrl.replace(/\/+$/, '');
    const res = await fetch(`${url}/api/tags`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return null;
    const data = (await res.json()) as { models?: { name?: string }[] };
    const first = data.models?.[0]?.name?.split(':')[0];
    return {
      enabled: true,
      baseUrl: url,
      model: first ?? DEFAULT_MODEL,
    };
  } catch {
    return null;
  }
}

export async function ollamaChat(
  config: OllamaConfig,
  system: string,
  userPrompt: string,
): Promise<string> {
  const res = await fetch(`${config.baseUrl.replace(/\/+$/, '')}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: config.model,
      stream: false,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: userPrompt },
      ],
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Ollama ${res.status}${text ? `: ${text.slice(0, 160)}` : ''}`);
  }
  const data = (await res.json()) as { message?: { content?: string } };
  const content = data.message?.content?.trim();
  if (!content) throw new Error('Empty Ollama response');
  return content;
}
