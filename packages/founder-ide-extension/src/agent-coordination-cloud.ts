export type FounderCoordinationCredentials = {
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
};

export type FounderCloudTaskCard = {
  clientTaskId: string;
  workspaceKey: string;
  title: string;
  branch?: string;
  provider?: string;
  scope?: unknown;
  permissions?: unknown;
  budgetWeightedUnits?: number;
};

export type FounderCloudTask = FounderCloudTaskCard & {
  id: string;
  ownerUserId: string;
  teamId?: string | null;
  status: 'ACTIVE' | 'WAITING' | 'COMPLETE' | 'CANCELED';
  heartbeatAt: string;
  expiresAt: string;
  claims: Array<{ path: string; generation: number; fencingToken: string }>;
};

export type FounderCloudClaimResult =
  | { ok: true; synced: boolean }
  | { ok: false; reason: string; ownerTaskId?: string; ownerTitle?: string };

type Registration = {
  credentials: FounderCoordinationCredentials;
  workspaceKey: string;
  task: Promise<FounderCloudTask | null>;
};

type FetchLike = typeof fetch;

export class FounderCoordinationCloud {
  private readonly registrations = new Map<string, Registration>();

  constructor(private readonly fetchImpl: FetchLike = fetch) {}

  begin(credentials: FounderCoordinationCredentials, card: FounderCloudTaskCard): void {
    const registration: Registration = {
      credentials,
      workspaceKey: card.workspaceKey,
      task: this.request<FounderCloudTask>(credentials, '/coordination/tasks', {
        method: 'POST',
        body: JSON.stringify(card),
      }).catch(() => null),
    };
    this.registrations.set(card.clientTaskId, registration);
  }

  async heartbeat(localTaskId: string, status: 'ACTIVE' | 'WAITING' = 'ACTIVE'): Promise<void> {
    const registration = this.registrations.get(localTaskId);
    const task = await registration?.task;
    if (!registration || !task) return;
    await this.request(registration.credentials, `/coordination/tasks/${encodeURIComponent(task.id)}/heartbeat`, {
      method: 'POST', body: JSON.stringify({ status }),
    }).catch(() => undefined);
  }

  async peers(localTaskId: string): Promise<FounderCloudTask[]> {
    const registration = this.registrations.get(localTaskId);
    const task = await registration?.task;
    if (!registration || !task) return [];
    const query = encodeURIComponent(registration.workspaceKey);
    return this.request<FounderCloudTask[]>(registration.credentials, `/coordination/tasks?workspaceKey=${query}`)
      .catch(() => []);
  }

  async claim(localTaskId: string, claimedPath: string): Promise<FounderCloudClaimResult> {
    const registration = this.registrations.get(localTaskId);
    const task = await registration?.task;
    if (!registration || !task) return { ok: true, synced: false };
    try {
      await this.request(registration.credentials, `/coordination/tasks/${encodeURIComponent(task.id)}/claims`, {
        method: 'POST', body: JSON.stringify({ path: claimedPath }),
      });
      return { ok: true, synced: true };
    } catch (error) {
      if (error instanceof FounderCoordinationHttpError && error.status === 409) {
        return {
          ok: false,
          reason: error.message || `${claimedPath} is claimed by another Founder task.`,
          ownerTaskId: stringValue(error.body?.ownerTaskId),
          ownerTitle: stringValue(error.body?.ownerTitle),
        };
      }
      // Offline and Local modes remain usable. The local fencing layer still
      // protects this machine; the awareness panel reports cloud degradation.
      return { ok: true, synced: false };
    }
  }

  async finish(localTaskId: string, status: 'COMPLETE' | 'CANCELED' = 'COMPLETE'): Promise<void> {
    const registration = this.registrations.get(localTaskId);
    this.registrations.delete(localTaskId);
    const task = await registration?.task;
    if (!registration || !task) return;
    await this.request(registration.credentials, `/coordination/tasks/${encodeURIComponent(task.id)}/finish`, {
      method: 'PATCH', body: JSON.stringify({ status }),
    }).catch(() => undefined);
  }

  private async request<T = unknown>(
    credentials: FounderCoordinationCredentials,
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5_000);
    timer.unref?.();
    try {
      const response = await this.fetchImpl(
        `${credentials.apiBaseUrl.replace(/\/$/, '')}/api/founder-node${path}`,
        {
          ...init,
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            Authorization: `FounderNode ${credentials.nodeId}:${credentials.nodeToken}`,
            ...(init.headers ?? {}),
          },
        },
      );
      const body = await response.json().catch(() => ({})) as Record<string, unknown>;
      if (!response.ok) {
        throw new FounderCoordinationHttpError(response.status, safeMessage(body), body);
      }
      return body as T;
    } finally {
      clearTimeout(timer);
    }
  }
}

class FounderCoordinationHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: Record<string, unknown>,
  ) {
    super(message);
  }
}

function safeMessage(body: Record<string, unknown>): string {
  if (typeof body.message === 'string') return body.message.slice(0, 300);
  if (typeof body.error === 'string') return body.error.slice(0, 300);
  return 'Founder coordination request failed.';
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

export const founderCoordinationCloud = new FounderCoordinationCloud();
