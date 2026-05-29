export type OpenHandsDispatchInput = {
  baseUrl: string;
  apiKey: string;
  taskPrompt: string;
  repository?: string;
};

export type OpenHandsDispatchResult = {
  apiVersion: 'v1' | 'v0';
  startTaskId: string;
  conversationId: string | null;
  status: string;
  conversationUrl: string | null;
};

export function normalizeOpenHandsBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, '');
  if (!trimmed) throw new Error('OpenHands base URL required');
  if (!/^https?:\/\//i.test(trimmed)) {
    return `https://${trimmed}`;
  }
  return trimmed;
}

export function buildOpenHandsConversationUrl(baseUrl: string, conversationId: string): string {
  return `${normalizeOpenHandsBaseUrl(baseUrl)}/conversations/${conversationId}`;
}

export function buildOpenHandsTaskMessage(spec: string, cursorPrompt?: string): string {
  const parts = [
    'Founder OS build dispatch — execute this task in the connected repository.',
    '',
    cursorPrompt?.trim() ? `## Agent prompt\n${cursorPrompt.trim()}` : '',
    spec?.trim() ? `## Spec\n${spec.trim()}` : '',
  ].filter(Boolean);
  return parts.join('\n');
}
