/** Estimate LLM tokens when providers omit usage (≈4 chars per token). */
export function estimateLlmTokensFromText(text: string): number {
  const len = text?.length ?? 0;
  if (len <= 0) return 0;
  return Math.max(1, Math.ceil(len / 4));
}

export function parseOpenAiStyleUsage(data: {
  usage?: { prompt_tokens?: number; completion_tokens?: number };
}): { promptTokens: number; completionTokens: number } | null {
  const u = data.usage;
  if (!u || (u.prompt_tokens == null && u.completion_tokens == null)) return null;
  return {
    promptTokens: Math.max(0, u.prompt_tokens ?? 0),
    completionTokens: Math.max(0, u.completion_tokens ?? 0),
  };
}

export function parseAnthropicUsage(data: {
  usage?: { input_tokens?: number; output_tokens?: number };
}): { promptTokens: number; completionTokens: number } | null {
  const u = data.usage;
  if (!u) return null;
  return {
    promptTokens: Math.max(0, u.input_tokens ?? 0),
    completionTokens: Math.max(0, u.output_tokens ?? 0),
  };
}
