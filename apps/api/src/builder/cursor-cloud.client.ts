import { BadRequestException } from '@nestjs/common';
import {
  CURSOR_CLOUD_API_BASE,
  CursorCloudDispatchInput,
  CursorCloudDispatchResult,
  buildCursorAgentUrl,
  githubRepoToUrl,
  resolveCursorStartingRef,
} from '@dcf/utils';

export type CursorCredentialMeta = {
  accountName?: string;
  agentId?: string | null;
  agentRepoUrl?: string | null;
  latestRunId?: string | null;
};

function authHeader(apiKey: string): string {
  const token = Buffer.from(`${apiKey.trim()}:`).toString('base64');
  return `Basic ${token}`;
}

export async function verifyCursorCloudConnection(apiKey: string): Promise<{ accountName: string }> {
  const res = await fetch(`${CURSOR_CLOUD_API_BASE}/v1/me`, {
    headers: { Authorization: authHeader(apiKey) },
  });
  if (!res.ok) {
    throw new BadRequestException(
      'Invalid Cursor API key — generate one in Cursor Dashboard → Integrations.',
    );
  }
  const data = (await res.json()) as {
    apiKeyName?: string;
    userEmail?: string;
  };
  return {
    accountName: data.userEmail ?? data.apiKeyName ?? 'Cursor Cloud',
  };
}

export async function dispatchCursorCloudTask(
  input: CursorCloudDispatchInput,
): Promise<CursorCloudDispatchResult> {
  const repoUrl = input.repository ? githubRepoToUrl(input.repository) : null;
  const canFollowUp =
    Boolean(input.agentId) && (!repoUrl || input.agentRepoUrl === repoUrl);

  if (canFollowUp && input.agentId) {
    return followUpRun(input.apiKey, input.agentId, input.taskPrompt);
  }

  return createAgent(input.apiKey, input.taskPrompt, repoUrl, input.startingRef);
}

async function createAgent(
  apiKey: string,
  promptText: string,
  repoUrl: string | null,
  startingRef?: string,
): Promise<CursorCloudDispatchResult> {
  const body: Record<string, unknown> = {
    prompt: { text: promptText },
    autoCreatePR: true,
    name: promptText.slice(0, 100),
  };
  if (repoUrl) {
    body.repos = [{ url: repoUrl, startingRef: resolveCursorStartingRef(startingRef) }];
  }

  const res = await fetch(`${CURSOR_CLOUD_API_BASE}/v1/agents`, {
    method: 'POST',
    headers: {
      Authorization: authHeader(apiKey),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new BadRequestException(
      `Cursor agent create failed (${res.status}): ${errText.slice(0, 240)}`,
    );
  }

  const data = (await res.json()) as {
    agent?: { id?: string; url?: string; latestRunId?: string };
    run?: { id?: string; status?: string };
  };

  const agentId = data.agent?.id;
  const runId = data.run?.id ?? data.agent?.latestRunId;
  if (!agentId || !runId) {
    throw new BadRequestException('Cursor API returned an incomplete agent response');
  }

  return {
    agentId,
    runId,
    status: data.run?.status ?? 'CREATING',
    agentUrl: data.agent?.url ?? buildCursorAgentUrl(agentId),
    mode: 'create',
  };
}

async function followUpRun(
  apiKey: string,
  agentId: string,
  promptText: string,
): Promise<CursorCloudDispatchResult> {
  const res = await fetch(`${CURSOR_CLOUD_API_BASE}/v1/agents/${agentId}/runs`, {
    method: 'POST',
    headers: {
      Authorization: authHeader(apiKey),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      prompt: { text: promptText },
    }),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    if (res.status === 409) {
      throw new BadRequestException(
        'Cursor agent is busy — wait for the current run to finish, then try Continue again.',
      );
    }
    throw new BadRequestException(
      `Cursor follow-up run failed (${res.status}): ${errText.slice(0, 240)}`,
    );
  }

  const data = (await res.json()) as {
    run?: { id?: string; status?: string };
  };

  const runId = data.run?.id;
  if (!runId) {
    throw new BadRequestException('Cursor API returned an incomplete run response');
  }

  return {
    agentId,
    runId,
    status: data.run?.status ?? 'CREATING',
    agentUrl: buildCursorAgentUrl(agentId),
    mode: 'follow_up',
  };
}

export type CursorRunSnapshot = {
  id: string;
  agentId: string;
  status: string;
  result?: string | null;
  durationMs?: number | null;
  git?: {
    branches?: { repoUrl?: string; branch?: string; prUrl?: string }[];
  } | null;
  createdAt?: string;
  updatedAt?: string;
};

const TERMINAL_RUN_STATUSES = new Set(['FINISHED', 'ERROR', 'CANCELLED', 'EXPIRED']);

export function isCursorRunTerminal(status: string): boolean {
  return TERMINAL_RUN_STATUSES.has(status.toUpperCase());
}

export async function fetchCursorRun(
  apiKey: string,
  agentId: string,
  runId: string,
): Promise<CursorRunSnapshot> {
  const res = await fetch(`${CURSOR_CLOUD_API_BASE}/v1/agents/${agentId}/runs/${runId}`, {
    headers: { Authorization: authHeader(apiKey) },
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new BadRequestException(
      `Cursor run status failed (${res.status}): ${errText.slice(0, 240)}`,
    );
  }
  const data = (await res.json()) as CursorRunSnapshot & { result?: string };
  return {
    id: data.id ?? runId,
    agentId: data.agentId ?? agentId,
    status: data.status ?? 'UNKNOWN',
    result: data.result ?? null,
    durationMs: data.durationMs ?? null,
    git: data.git ?? null,
    createdAt: data.createdAt,
    updatedAt: data.updatedAt,
  };
}

export type { CursorCloudDispatchResult };
