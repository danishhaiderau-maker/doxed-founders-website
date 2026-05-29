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

export type { OpenHandsCredentialMeta };
