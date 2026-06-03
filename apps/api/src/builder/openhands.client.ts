import { BadRequestException } from '@nestjs/common';
import {
  OpenHandsDispatchInput,
  OpenHandsDispatchResult,
  buildOpenHandsConversationUrl,
  normalizeOpenHandsBaseUrl,
} from '@dcf/utils';

type OpenHandsCredentialMeta = {
  baseUrl: string;
  accountName?: string;
  apiVersion?: 'v1' | 'v0';
};

export async function verifyOpenHandsConnection(
  baseUrl: string,
  apiKey: string,
): Promise<{ accountName: string; apiVersion: 'v1' | 'v0' }> {
  const root = normalizeOpenHandsBaseUrl(baseUrl);

  const v1 = await fetch(`${root}/api/v1/app-conversations/search?limit=1`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (v1.ok) {
    return { accountName: 'OpenHands (V1 API)', apiVersion: 'v1' };
  }

  const v0 = await fetch(`${root}/api/conversations`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (v0.ok || v0.status === 405 || v0.status === 400) {
    return { accountName: 'OpenHands (legacy API)', apiVersion: 'v0' };
  }

  throw new BadRequestException(
    'Could not reach OpenHands — check base URL (e.g. https://app.all-hands.dev or your self-hosted URL) and API key.',
  );
}

export async function dispatchOpenHandsTask(
  input: OpenHandsDispatchInput,
  preferredVersion?: 'v1' | 'v0',
): Promise<OpenHandsDispatchResult> {
  const root = normalizeOpenHandsBaseUrl(input.baseUrl);
  const versions: ('v1' | 'v0')[] =
    preferredVersion === 'v0' ? ['v0', 'v1'] : ['v1', 'v0'];

  for (const version of versions) {
    try {
      if (version === 'v1') {
        return await dispatchV1(root, input);
      }
      return await dispatchV0(root, input);
    } catch {
      continue;
    }
  }

  throw new BadRequestException('OpenHands task dispatch failed on V1 and legacy API');
}

async function dispatchV1(root: string, input: OpenHandsDispatchInput): Promise<OpenHandsDispatchResult> {
  const body: Record<string, unknown> = {
    initial_message: {
      content: [{ type: 'text', text: input.taskPrompt }],
    },
  };
  if (input.repository?.trim()) {
    body.selected_repository = input.repository.trim();
  }

  const res = await fetch(`${root}/api/v1/app-conversations`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${input.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new Error(`V1 dispatch failed (${res.status}): ${errText.slice(0, 200)}`);
  }

  const data = (await res.json()) as {
    id?: string;
    status?: string;
    app_conversation_id?: string;
  };

  const startTaskId = data.id ?? '';
  const conversationId = data.app_conversation_id ?? null;

  return {
    apiVersion: 'v1',
    startTaskId,
    conversationId,
    status: data.status ?? 'WORKING',
    conversationUrl: conversationId ? buildOpenHandsConversationUrl(root, conversationId) : null,
  };
}

async function dispatchV0(root: string, input: OpenHandsDispatchInput): Promise<OpenHandsDispatchResult> {
  const body: Record<string, string> = {
    initial_user_msg: input.taskPrompt,
  };
  if (input.repository?.trim()) {
    body.repository = input.repository.trim();
  }

  const res = await fetch(`${root}/api/conversations`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${input.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new Error(`V0 dispatch failed (${res.status}): ${errText.slice(0, 200)}`);
  }

  const data = (await res.json()) as {
    conversation_id?: string;
    status?: string;
  };

  const conversationId = data.conversation_id ?? null;

  return {
    apiVersion: 'v0',
    startTaskId: conversationId ?? '',
    conversationId,
    status: data.status ?? 'ok',
    conversationUrl: conversationId ? buildOpenHandsConversationUrl(root, conversationId) : null,
  };
}

export type OpenHandsRunSnapshot = {
  conversationId: string;
  status: string;
  result?: string | null;
  terminal?: boolean;
  conversationUrl?: string | null;
};

const OPENHANDS_TERMINAL = new Set([
  'COMPLETED',
  'FINISHED',
  'ERROR',
  'FAILED',
  'CANCELLED',
  'STOPPED',
  'DONE',
]);

export function isOpenHandsRunTerminal(status: string): boolean {
  return OPENHANDS_TERMINAL.has(status.toUpperCase());
}

function extractTextFromEvents(items: unknown[]): string {
  const parts: string[] = [];
  for (const raw of items) {
    if (!raw || typeof raw !== 'object') continue;
    const ev = raw as Record<string, unknown>;
    const kind = String(ev.kind ?? '');
    if (kind === 'MessageEvent') {
      const msg = ev.message ?? ev.content;
      if (typeof msg === 'string' && msg.trim()) parts.push(msg.trim());
      else if (Array.isArray(msg)) {
        for (const c of msg) {
          if (c && typeof c === 'object' && (c as { type?: string }).type === 'text') {
            const t = (c as { text?: string }).text;
            if (t?.trim()) parts.push(t.trim());
          }
        }
      }
    }
    if (kind === 'ObservationEvent' && ev.observation && typeof ev.observation === 'object') {
      const obs = ev.observation as Record<string, unknown>;
      if (typeof obs.content === 'string' && obs.content.trim()) parts.push(obs.content.trim());
      else if (typeof obs.text === 'string' && obs.text.trim()) parts.push(obs.text.trim());
    }
  }
  return parts.slice(-12).join('\n\n');
}

export async function fetchOpenHandsConversationSnapshot(
  baseUrl: string,
  apiKey: string,
  conversationId: string,
  apiVersion: 'v1' | 'v0' = 'v1',
  conversationUrl?: string | null,
): Promise<OpenHandsRunSnapshot> {
  const root = normalizeOpenHandsBaseUrl(baseUrl);
  let status = 'WORKING';
  let items: unknown[] = [];

  if (apiVersion === 'v1') {
    const convRes = await fetch(
      `${root}/api/v1/app-conversations?ids=${encodeURIComponent(conversationId)}`,
      { headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' } },
    );
    if (convRes.ok) {
      const convData = (await convRes.json()) as {
        items?: { status?: string }[];
      };
      status = convData.items?.[0]?.status ?? status;
    }
    const evRes = await fetch(
      `${root}/api/v1/conversation/${encodeURIComponent(conversationId)}/events/search?limit=60&sort_order=TIMESTAMP_DESC`,
      { headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' } },
    );
    if (evRes.ok) {
      const evData = (await evRes.json()) as { items?: unknown[] };
      items = [...(evData.items ?? [])].reverse();
    }
  } else {
    const evRes = await fetch(
      `${root}/api/conversations/${encodeURIComponent(conversationId)}/events?limit=60`,
      { headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' } },
    );
    if (evRes.ok) {
      const evData = (await evRes.json()) as { events?: unknown[] };
      items = evData.events ?? [];
    }
  }

  const result = extractTextFromEvents(items);
  const terminal = isOpenHandsRunTerminal(status);

  return {
    conversationId,
    status,
    result: result || null,
    terminal,
    conversationUrl: conversationUrl ?? buildOpenHandsConversationUrl(root, conversationId),
  };
}

export type { OpenHandsCredentialMeta };
