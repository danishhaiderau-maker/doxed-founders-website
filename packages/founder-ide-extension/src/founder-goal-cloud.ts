import type {
  FounderDecisionResearchFinding,
  FounderGoalUiDecision,
  FounderGoalUiState,
  FounderGoalUiStatus,
} from './founder-goal-state';

export type FounderGoalCloudCredentials = {
  apiBaseUrl: string;
  nodeId: string;
  nodeToken: string;
};

type CloudGoal = {
  id: string;
  version: number;
  objective: string;
  constraints: string[];
  successEvidence: Array<{
    id: string;
    label: string;
    kind: 'test' | 'build' | 'visual' | 'remote' | 'receipt' | 'human';
    required: boolean;
  }>;
  status:
    | 'draft'
    | 'active'
    | 'paused'
    | 'blocked'
    | 'verifying'
    | 'complete'
    | 'cancelled';
  updatedAt: string;
};

type CloudDecision = {
  id: string;
  goalId: string;
  goalVersion: number;
  kind: FounderGoalUiDecision['kind'];
  risk: FounderGoalUiDecision['risk'];
  title: string;
  question: string;
  options: Array<{
    id: string;
    label: string;
    description: string;
    impact: string;
    recommended?: boolean;
  }>;
  allowCustomAnswer: boolean;
  blockingTaskIds: string[];
  independentWorkMayContinue: boolean;
  evidence: string[];
  createdAt: string;
  status: FounderGoalUiDecision['status'];
  proposedGoalObjective?: string;
  housekeepingCandidates?: Array<{
    id: string;
    path: string;
    sizeBytes: number;
    category:
      | 'generated'
      | 'cache'
      | 'duplicate'
      | 'obsolete_source'
      | 'stale_worktree'
      | 'archive';
    evidence: string[];
    referencedBy: string[];
    recommendedAction: 'keep' | 'archive' | 'delete';
    reversible: boolean;
  }>;
  researchFindings?: FounderDecisionResearchFinding[];
};

type CloudResolution = {
  requestId: string;
  selectedOptionId?: string;
  selectedCandidateIds?: string[];
  customAnswer?: string;
  resolvedAt: string;
  resolvedBy: 'founder' | 'approved_policy';
};

export type FounderGoalCloudState = {
  goal: CloudGoal | null;
  decisions: CloudDecision[];
  resolutions: CloudResolution[];
  updatedAt: string;
};

type FetchLike = typeof fetch;

export class FounderGoalCloudClient {
  constructor(private readonly fetchImpl: FetchLike = fetch) {}

  load(
    credentials: FounderGoalCloudCredentials,
    workspaceKey: string,
  ): Promise<FounderGoalCloudState> {
    return this.request(
      credentials,
      `/goal-control?workspaceKey=${encodeURIComponent(workspaceKey)}`,
    ).then(normalizeCloudState);
  }

  saveGoal(
    credentials: FounderGoalCloudCredentials,
    workspaceKey: string,
    goal: FounderGoalUiState,
  ): Promise<FounderGoalCloudState> {
    return this.request(credentials, '/goal-control/goal', {
      method: 'POST',
      body: JSON.stringify({
        ...goalToCloud(goal),
        workspaceKey,
      }),
    }).then(normalizeCloudState);
  }

  queueDecision(
    credentials: FounderGoalCloudCredentials,
    workspaceKey: string,
    goal: FounderGoalUiState,
    decision: FounderGoalUiDecision,
  ): Promise<FounderGoalCloudState> {
    const cloudDecision = decisionToCloud(goal, decision);
    return this.request(credentials, '/goal-control/decisions', {
      method: 'POST',
      body: JSON.stringify({
        ...cloudDecision,
        status:
          cloudDecision.status === 'resolved'
            ? 'pending'
            : cloudDecision.status,
        workspaceKey,
      }),
    }).then(normalizeCloudState);
  }

  appendResearch(
    credentials: FounderGoalCloudCredentials,
    workspaceKey: string,
    decisionId: string,
    finding: FounderDecisionResearchFinding,
  ): Promise<FounderGoalCloudState> {
    return this.request(credentials, '/goal-control/decisions/research', {
      method: 'POST',
      body: JSON.stringify({
        workspaceKey,
        decisionId,
        finding,
      }),
    }).then(normalizeCloudState);
  }

  resolveDecision(
    credentials: FounderGoalCloudCredentials,
    workspaceKey: string,
    decision: FounderGoalUiDecision,
  ): Promise<FounderGoalCloudState> {
    return this.request(credentials, '/goal-control/decisions/resolve', {
      method: 'POST',
      body: JSON.stringify({
        workspaceKey,
        requestId: decision.id,
        selectedOptionId: decision.selectedOptionId,
        selectedCandidateIds: decision.selectedCandidateIds,
        customAnswer: decision.customAnswer,
      }),
    }).then(normalizeCloudState);
  }

  private async request(
    credentials: FounderGoalCloudCredentials,
    path: string,
    init: RequestInit = {},
  ): Promise<unknown> {
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
            Authorization:
              `FounderNode ${credentials.nodeId}:${credentials.nodeToken}`,
            ...(init.headers ?? {}),
          },
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(safeCloudMessage(body));
      }
      return body;
    } finally {
      clearTimeout(timer);
    }
  }
}

export async function synchronizeFounderGoalState(
  local: FounderGoalUiState,
  credentials: FounderGoalCloudCredentials,
  workspaceKey: string,
  client = new FounderGoalCloudClient(),
): Promise<FounderGoalUiState> {
  let cloud = await client.load(credentials, workspaceKey);
  const localOwnsGoal = goalWins(local, cloud.goal);
  if (localOwnsGoal) {
    cloud = await client.saveGoal(credentials, workspaceKey, local);
  }
  const activeGoal = localOwnsGoal
    ? local
    : mergeFounderGoalCloudState(local, cloud);
  const cloudById = new Map(cloud.decisions.map((item) => [item.id, item]));
  const cloudResolutionIds = new Set(
    cloud.resolutions.map((item) => item.requestId),
  );

  for (const decision of local.decisions) {
    let remote = cloudById.get(decision.id);
    if (
      !remote
      && decision.status !== 'cancelled'
      && activeGoal.id === local.id
      && activeGoal.version === local.version
    ) {
      cloud = await client.queueDecision(
        credentials,
        workspaceKey,
        local,
        decision,
      );
      remote = cloud.decisions.find((item) => item.id === decision.id);
      if (remote) cloudById.set(remote.id, remote);
    }
    if (!remote) continue;
    for (const finding of decision.researchFindings) {
      if (!(remote.researchFindings ?? []).some((item) => item.id === finding.id)) {
        cloud = await client.appendResearch(
          credentials,
          workspaceKey,
          decision.id,
          finding,
        );
        remote = cloud.decisions.find((item) => item.id === decision.id);
        if (remote) cloudById.set(remote.id, remote);
        else break;
      }
    }
    if (!remote) continue;
    if (
      decision.status === 'resolved'
      && remote.status === 'pending'
      && !cloudResolutionIds.has(decision.id)
    ) {
      cloud = await client.resolveDecision(
        credentials,
        workspaceKey,
        decision,
      );
      cloudResolutionIds.add(decision.id);
    }
  }
  return mergeFounderGoalCloudState(local, cloud);
}

export function mergeFounderGoalCloudState(
  local: FounderGoalUiState,
  cloud: FounderGoalCloudState,
): FounderGoalUiState {
  const cloudGoalWins = !goalWins(local, cloud.goal) && Boolean(cloud.goal);
  const goal = cloudGoalWins ? cloud.goal! : goalToCloud(local);
  const resolutions = new Map(
    cloud.resolutions.map((resolution) => [resolution.requestId, resolution]),
  );
  const localById = new Map(local.decisions.map((item) => [item.id, item]));
  const decisions = cloud.decisions.map((decision) => {
    const localDecision = localById.get(decision.id);
    const resolution = resolutions.get(decision.id);
    localById.delete(decision.id);
    return cloudDecisionToUi(decision, localDecision, resolution);
  });
  for (const localDecision of localById.values()) {
    if (!cloudGoalWins) decisions.push(structuredClone(localDecision));
  }
  return {
    id: goal.id,
    version: goal.version,
    objective: goal.objective,
    status: cloudStatusToUi(goal.status),
    updatedAt: goal.updatedAt,
    decisions: decisions
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
      .slice(-50),
  };
}

function goalWins(local: FounderGoalUiState, cloud: CloudGoal | null) {
  if (!cloud) return true;
  if (local.id === cloud.id) {
    if (local.version !== cloud.version) return local.version > cloud.version;
    return local.updatedAt.localeCompare(cloud.updatedAt) >= 0;
  }
  return local.updatedAt.localeCompare(cloud.updatedAt) > 0;
}

function goalToCloud(goal: FounderGoalUiState): CloudGoal {
  return {
    id: goal.id,
    version: goal.version,
    objective: goal.objective,
    constraints: [
      'Preserve founder-approved boundaries and verified work.',
      'Do not treat silence, research, or a timeout as permission.',
    ],
    successEvidence: [
      {
        id: 'founder-goal-evidence',
        label: 'The goal has verified completion evidence.',
        kind: 'human',
        required: true,
      },
    ],
    status: goal.status,
    updatedAt: goal.updatedAt,
  };
}

function decisionToCloud(
  goal: FounderGoalUiState,
  decision: FounderGoalUiDecision,
): CloudDecision {
  return {
    id: decision.id,
    goalId: goal.id,
    goalVersion: goal.version,
    kind: decision.kind,
    risk: decision.risk,
    title: decision.title,
    question: decision.question,
    options: decision.options
      .filter((option): option is NonNullable<typeof option> => Boolean(option))
      .map((option) => ({
        ...option,
        impact: option.description,
      })),
    allowCustomAnswer: decision.allowCustomAnswer,
    blockingTaskIds: [...decision.blockingTaskIds],
    independentWorkMayContinue: decision.independentWorkMayContinue,
    evidence: [...decision.evidence],
    createdAt: decision.createdAt,
    status: decision.status,
    proposedGoalObjective: decision.proposedGoalObjective,
    housekeepingCandidates: decision.housekeepingCandidates?.map((item) => ({
      ...item,
      referencedBy: [],
    })),
    researchFindings: structuredClone(decision.researchFindings),
  };
}

function cloudDecisionToUi(
  decision: CloudDecision,
  local: FounderGoalUiDecision | undefined,
  resolution: CloudResolution | undefined,
): FounderGoalUiDecision {
  const research = new Map<string, FounderDecisionResearchFinding>();
  for (const finding of [
    ...(local?.researchFindings ?? []),
    ...(decision.researchFindings ?? []),
  ]) {
    research.set(finding.id, structuredClone(finding));
  }
  return {
    id: decision.id,
    kind: decision.kind,
    title: decision.title,
    question: decision.question,
    options: [
      ...decision.options.slice(0, 3).map((option) => ({
        id: option.id,
        label: option.label,
        description: option.description,
        ...(option.recommended ? { recommended: true } : {}),
      })),
    ] as FounderGoalUiDecision['options'],
    allowCustomAnswer: decision.allowCustomAnswer,
    independentWorkMayContinue: decision.independentWorkMayContinue,
    risk: decision.risk,
    status: resolution ? 'resolved' : decision.status,
    evidence: [...decision.evidence],
    blockingTaskIds: [...decision.blockingTaskIds],
    researchFindings: [...research.values()].slice(-20),
    proposedGoalObjective: decision.proposedGoalObjective,
    housekeepingCandidates: decision.housekeepingCandidates?.map((item) => ({
      id: item.id,
      path: item.path,
      sizeBytes: item.sizeBytes,
      category: item.category,
      evidence: [...item.evidence],
      recommendedAction: item.recommendedAction,
      reversible: item.reversible,
    })),
    selectedOptionId: resolution?.selectedOptionId
      ?? local?.selectedOptionId,
    selectedCandidateIds: resolution?.selectedCandidateIds
      ?? local?.selectedCandidateIds,
    customAnswer: resolution?.customAnswer ?? local?.customAnswer,
    createdAt: decision.createdAt,
    resolvedAt: resolution?.resolvedAt ?? local?.resolvedAt,
  };
}

function normalizeCloudState(value: unknown): FounderGoalCloudState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Founder returned an invalid Goal state.');
  }
  const raw = value as Partial<FounderGoalCloudState>;
  const goal = normalizeCloudGoal(raw.goal);
  const decisions = Array.isArray(raw.decisions)
    ? raw.decisions.map(normalizeCloudDecision).filter(Boolean)
    : [];
  const resolutions = Array.isArray(raw.resolutions)
    ? raw.resolutions.map(normalizeCloudResolution).filter(Boolean)
    : [];
  return {
    goal,
    decisions: decisions as CloudDecision[],
    resolutions: resolutions as CloudResolution[],
    updatedAt: validIso(raw.updatedAt) ?? new Date().toISOString(),
  };
}

function normalizeCloudGoal(value: unknown): CloudGoal | null {
  if (value === null || value === undefined) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Founder returned an invalid Goal contract.');
  }
  const raw = value as Partial<CloudGoal>;
  const id = compact(raw.id, 120);
  const objective = compact(raw.objective, 500);
  const updatedAt = validIso(raw.updatedAt);
  if (
    !id
    || !objective
    || !Number.isInteger(raw.version)
    || Number(raw.version) < 1
    || !updatedAt
  ) {
    throw new Error('Founder returned an incomplete Goal contract.');
  }
  return {
    id,
    objective,
    version: Number(raw.version),
    constraints: [],
    successEvidence: [],
    status: normalizeCloudStatus(raw.status),
    updatedAt,
  };
}

function normalizeCloudDecision(value: unknown): CloudDecision | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Partial<CloudDecision>;
  const options = Array.isArray(raw.options)
    ? raw.options.filter((item) =>
      item && typeof item.id === 'string' && typeof item.label === 'string')
    : [];
  if (
    !compact(raw.id, 120)
    || !compact(raw.goalId, 120)
    || !compact(raw.title, 160)
    || !compact(raw.question, 500)
    || options.length < 2
    || options.length > 3
    || !validIso(raw.createdAt)
  ) return null;
  return {
    ...(raw as CloudDecision),
    id: compact(raw.id, 120),
    goalId: compact(raw.goalId, 120),
    title: compact(raw.title, 160),
    question: compact(raw.question, 500),
    options: options.slice(0, 3),
    evidence: Array.isArray(raw.evidence) ? raw.evidence.slice(0, 100) : [],
    blockingTaskIds: Array.isArray(raw.blockingTaskIds)
      ? raw.blockingTaskIds.slice(0, 100)
      : [],
    researchFindings: Array.isArray(raw.researchFindings)
      ? raw.researchFindings.slice(-20)
      : [],
  };
}

function normalizeCloudResolution(value: unknown): CloudResolution | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Partial<CloudResolution>;
  if (!compact(raw.requestId, 120) || !validIso(raw.resolvedAt)) return null;
  return {
    requestId: compact(raw.requestId, 120),
    selectedOptionId: compact(raw.selectedOptionId, 120) || undefined,
    selectedCandidateIds: Array.isArray(raw.selectedCandidateIds)
      ? raw.selectedCandidateIds
        .filter((item): item is string => typeof item === 'string')
        .slice(0, 100)
      : undefined,
    customAnswer: compact(raw.customAnswer, 1_000) || undefined,
    resolvedAt: raw.resolvedAt!,
    resolvedBy:
      raw.resolvedBy === 'approved_policy'
        ? 'approved_policy'
        : 'founder',
  };
}

function normalizeCloudStatus(value: unknown): CloudGoal['status'] {
  return [
    'draft',
    'active',
    'paused',
    'blocked',
    'verifying',
    'complete',
    'cancelled',
  ].includes(String(value))
    ? value as CloudGoal['status']
    : 'active';
}

function cloudStatusToUi(value: CloudGoal['status']): FounderGoalUiStatus {
  if (value === 'draft' || value === 'cancelled') return 'paused';
  return value;
}

function compact(value: unknown, max: number) {
  return typeof value === 'string'
    ? value.replace(/\s+/g, ' ').trim().slice(0, max)
    : '';
}

function validIso(value: unknown): string | null {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? value
    : null;
}

function safeCloudMessage(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return 'Founder Goal sync is temporarily unavailable.';
  }
  const raw = value as { message?: unknown; error?: unknown };
  const candidate =
    typeof raw.message === 'string'
      ? raw.message
      : typeof raw.error === 'string'
        ? raw.error
        : '';
  return compact(candidate, 300)
    || 'Founder Goal sync is temporarily unavailable.';
}
