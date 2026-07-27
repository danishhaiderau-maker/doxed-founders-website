import { randomUUID } from 'node:crypto';

export type FounderGoalUiStatus =
  | 'active'
  | 'paused'
  | 'blocked'
  | 'verifying'
  | 'complete';

export interface FounderGoalUiOption {
  id: string;
  label: string;
  description: string;
  recommended?: boolean;
}

export interface FounderHousekeepingUiCandidate {
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
  recommendedAction: 'keep' | 'archive' | 'delete';
  reversible: boolean;
}

export interface FounderDecisionResearchFinding {
  id: string;
  title: string;
  summary: string;
  sources: string[];
  createdAt: string;
}

export interface FounderGoalUiDecision {
  id: string;
  kind: 'goal_amendment' | 'permission' | 'housekeeping' | 'research_preference';
  title: string;
  question: string;
  options: [FounderGoalUiOption, FounderGoalUiOption, FounderGoalUiOption?];
  allowCustomAnswer: boolean;
  independentWorkMayContinue: boolean;
  risk: 'read_only' | 'reversible_write' | 'external_write' | 'destructive';
  status: 'pending' | 'resolved' | 'cancelled';
  evidence: string[];
  blockingTaskIds: string[];
  researchFindings: FounderDecisionResearchFinding[];
  proposedGoalObjective?: string;
  housekeepingCandidates?: FounderHousekeepingUiCandidate[];
  selectedCandidateIds?: string[];
  selectedOptionId?: string;
  customAnswer?: string;
  createdAt: string;
  resolvedAt?: string;
}

export interface FounderGoalUiState {
  id: string;
  version: number;
  objective: string;
  status: FounderGoalUiStatus;
  updatedAt: string;
  decisions: FounderGoalUiDecision[];
}

export function initialFounderGoalState(
  workspaceName: string,
  now = new Date(),
): FounderGoalUiState {
  const project = workspaceName.trim() || 'this project';
  return {
    id: `goal-${randomUUID()}`,
    version: 1,
    objective: `Build and ship ${project}`,
    status: 'active',
    updatedAt: now.toISOString(),
    decisions: [],
  };
}

export function normalizeFounderGoalState(
  value: unknown,
  workspaceName: string,
  now = new Date(),
): FounderGoalUiState {
  if (!value || typeof value !== 'object') {
    return initialFounderGoalState(workspaceName, now);
  }
  const candidate = value as Partial<FounderGoalUiState>;
  const objective = typeof candidate.objective === 'string'
    ? candidate.objective.replace(/\s+/g, ' ').trim().slice(0, 500)
    : '';
  const status = normalizeStatus(candidate.status);
  if (!objective) {
    return initialFounderGoalState(workspaceName, now);
  }
  return {
    id: typeof candidate.id === 'string' && candidate.id.trim()
      ? candidate.id.trim().slice(0, 120)
      : `goal-${randomUUID()}`,
    version: Number.isInteger(candidate.version) && Number(candidate.version) > 0
      ? Number(candidate.version)
      : 1,
    objective,
    status,
    updatedAt: validIso(candidate.updatedAt) ?? now.toISOString(),
    decisions: Array.isArray(candidate.decisions)
      ? candidate.decisions
        .map(normalizeDecision)
        .filter((decision): decision is FounderGoalUiDecision => Boolean(decision))
        .slice(-50)
      : [],
  };
}

export function updateFounderGoalObjective(
  state: FounderGoalUiState,
  objective: string,
  now = new Date(),
): FounderGoalUiState {
  const normalized = objective.replace(/\s+/g, ' ').trim().slice(0, 500);
  if (!normalized || normalized === state.objective) return state;
  return {
    ...state,
    objective: normalized,
    version: state.version + 1,
    status: 'active',
    updatedAt: now.toISOString(),
  };
}

export function createFounderGoalAmendmentDecision(
  state: FounderGoalUiState,
  proposedObjective: string,
  now = new Date(),
): FounderGoalUiDecision {
  const normalized = proposedObjective.replace(/\s+/g, ' ').trim().slice(0, 500);
  if (!normalized || normalized === state.objective) {
    throw new Error('The proposed goal must be different from the current goal.');
  }
  return {
    id: `goal-amendment-${randomUUID()}`,
    kind: 'goal_amendment',
    title: 'Review goal change',
    question: `Replace "${state.objective}" with "${normalized}"?`,
    options: [
      {
        id: 'apply',
        label: 'Apply new goal',
        description: 'Use the proposed goal at the next safe task boundary.',
        recommended: true,
      },
      {
        id: 'keep',
        label: 'Keep current goal',
        description: 'Reject this proposal and continue with the current goal.',
      },
    ],
    allowCustomAnswer: true,
    independentWorkMayContinue: true,
    risk: 'reversible_write',
    status: 'pending',
    blockingTaskIds: [],
    researchFindings: [],
    evidence: [
      `Current goal version: ${state.version}`,
      'Completed external actions are not reversed by a goal amendment.',
    ],
    proposedGoalObjective: normalized,
    createdAt: now.toISOString(),
  };
}

export function createFounderHousekeepingDecision(
  state: FounderGoalUiState,
  candidates: unknown[],
  now = new Date(),
): FounderGoalUiDecision {
  const normalizedCandidates = candidates
    .map(normalizeHousekeepingCandidate)
    .filter((candidate): candidate is FounderHousekeepingUiCandidate =>
      Boolean(candidate))
    .slice(0, 100);
  const deletionCandidates = normalizedCandidates.filter(
    (candidate) => candidate.recommendedAction === 'delete',
  );
  if (deletionCandidates.length === 0) {
    throw new Error('Housekeeping found no deletion candidates to review.');
  }
  const deleteBytes = deletionCandidates.reduce(
    (total, candidate) => total + candidate.sizeBytes,
    0,
  );
  const reversible = deletionCandidates.every(
    (candidate) => candidate.reversible,
  );
  return {
    id: `housekeeping-${randomUUID()}`,
    kind: 'housekeeping',
    title: 'Review housekeeping',
    question:
      `Founder found ${deletionCandidates.length} proposed deletion`
      + `${deletionCandidates.length === 1 ? '' : 's'} (${formatBytes(deleteBytes)}).`,
    options: [
      {
        id: 'approve_selected',
        label: 'Approve checked',
        description: 'Grant permission only for the checked deletion candidates.',
        ...(reversible ? { recommended: true } : {}),
      },
      {
        id: 'keep_all',
        label: 'Keep everything',
        description: 'Reject this housekeeping batch without deleting files.',
        ...(!reversible ? { recommended: true } : {}),
      },
    ],
    allowCustomAnswer: true,
    independentWorkMayContinue: true,
    risk: reversible ? 'reversible_write' : 'destructive',
    status: 'pending',
    blockingTaskIds: [],
    researchFindings: [],
    evidence: [
      `Goal version reviewed: ${state.version}`,
      'The audit is read-only. Approval does not itself delete files.',
      'A deleting agent must re-check every selected path immediately before acting.',
    ],
    housekeepingCandidates: normalizedCandidates,
    createdAt: now.toISOString(),
  };
}

export function enqueueFounderDecision(
  state: FounderGoalUiState,
  decision: unknown,
): FounderGoalUiState {
  const normalized = normalizeDecision(decision);
  if (!normalized) throw new Error('Founder decision is invalid.');
  const existing = state.decisions.find((item) => item.id === normalized.id);
  if (existing && JSON.stringify(existing) !== JSON.stringify(normalized)) {
    throw new Error('Founder decision id already belongs to another request.');
  }
  if (existing) return state;
  return {
    ...state,
    decisions: [...state.decisions, normalized].slice(-50),
  };
}

export function attachFounderDecisionResearch(
  state: FounderGoalUiState,
  input: {
    decisionId: string;
    finding: unknown;
    now?: Date;
  },
): FounderGoalUiState {
  const decision = state.decisions.find(
    (item) => item.id === input.decisionId && item.status === 'pending',
  );
  if (!decision) throw new Error('Founder decision is not pending.');
  const finding = normalizeResearchFinding(
    input.finding,
    input.now ?? new Date(),
  );
  if (!finding) throw new Error('Founder research finding is invalid.');
  const existing = decision.researchFindings.find(
    (item) => item.id === finding.id,
  );
  if (existing && JSON.stringify(existing) !== JSON.stringify(finding)) {
    throw new Error('Research finding id already belongs to different evidence.');
  }
  if (existing) return state;
  const updatedAt = (input.now ?? new Date()).toISOString();
  return {
    ...state,
    updatedAt,
    decisions: state.decisions.map((item) =>
      item.id === decision.id
        ? {
          ...item,
          researchFindings: [...item.researchFindings, finding].slice(-20),
        }
        : item,
    ),
  };
}

export function founderGoalUiTaskCanContinue(
  state: FounderGoalUiState,
  taskId: string,
): boolean {
  const normalizedTaskId = taskId.trim();
  if (!normalizedTaskId) return false;
  return !state.decisions.some(
    (decision) =>
      decision.status === 'pending'
      && decision.blockingTaskIds.includes(normalizedTaskId),
  );
}

export function resolveFounderGoalUiDecision(
  state: FounderGoalUiState,
  input: {
    decisionId: string;
    selectedOptionId?: string;
    customAnswer?: string;
    selectedCandidateIds?: string[];
    now?: Date;
  },
): FounderGoalUiState {
  const decision = state.decisions.find((item) => item.id === input.decisionId);
  if (!decision || decision.status !== 'pending') {
    throw new Error('Founder decision is not pending.');
  }
  const selectedOptionId = input.selectedOptionId?.trim();
  const customAnswer = input.customAnswer?.replace(/\s+/g, ' ').trim().slice(0, 1_000);
  const optionIds = decision.options
    .filter((option): option is FounderGoalUiOption => Boolean(option))
    .map((option) => option.id);
  if (selectedOptionId && !optionIds.includes(selectedOptionId)) {
    throw new Error('Founder decision option is invalid.');
  }
  if (customAnswer && !decision.allowCustomAnswer) {
    throw new Error('This decision does not accept a custom answer.');
  }
  if (!selectedOptionId && !customAnswer) {
    throw new Error('Choose an option or provide an answer.');
  }
  const selectedCandidateIds = decision.kind === 'housekeeping'
    && selectedOptionId === 'approve_selected'
    ? normalizeSelectedCandidateIds(
      input.selectedCandidateIds,
      decision.housekeepingCandidates ?? [],
    )
    : [];
  if (
    decision.kind === 'housekeeping'
    && selectedOptionId === 'approve_selected'
    && selectedCandidateIds.length === 0
  ) {
    throw new Error('Select at least one housekeeping candidate.');
  }
  const resolvedAt = (input.now ?? new Date()).toISOString();
  const resolvedState: FounderGoalUiState = {
    ...state,
    updatedAt: resolvedAt,
    decisions: state.decisions.map((item) =>
      item.id === decision.id
        ? {
          ...item,
          status: 'resolved' as const,
          ...(selectedOptionId ? { selectedOptionId } : {}),
          ...(customAnswer ? { customAnswer } : {}),
          ...(selectedCandidateIds.length > 0
            ? { selectedCandidateIds }
            : {}),
          resolvedAt,
        }
        : item,
    ),
  };
  if (decision.kind !== 'goal_amendment') return resolvedState;
  if (customAnswer) {
    return updateFounderGoalObjective(
      resolvedState,
      customAnswer,
      input.now ?? new Date(),
    );
  }
  if (selectedOptionId === 'apply' && decision.proposedGoalObjective) {
    return updateFounderGoalObjective(
      resolvedState,
      decision.proposedGoalObjective,
      input.now ?? new Date(),
    );
  }
  return resolvedState;
}

export function pendingFounderGoalDecisions(
  state: FounderGoalUiState,
): FounderGoalUiDecision[] {
  return state.decisions.filter((decision) => decision.status === 'pending');
}

function normalizeDecision(value: unknown): FounderGoalUiDecision | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<FounderGoalUiDecision>;
  const id = typeof candidate.id === 'string' ? candidate.id.trim().slice(0, 120) : '';
  const title = typeof candidate.title === 'string'
    ? candidate.title.replace(/\s+/g, ' ').trim().slice(0, 160)
    : '';
  const question = typeof candidate.question === 'string'
    ? candidate.question.replace(/\s+/g, ' ').trim().slice(0, 500)
    : '';
  const options = Array.isArray(candidate.options)
    ? candidate.options.map(normalizeOption).filter(
      (option): option is FounderGoalUiOption => Boolean(option),
    )
    : [];
  if (!id || !title || !question || options.length < 2 || options.length > 3) {
    return null;
  }
  if (new Set(options.map((option) => option.id)).size !== options.length) return null;
  if (options.filter((option) => option.recommended).length > 1) return null;
  return {
    id,
    kind: normalizeKind(candidate.kind),
    title,
    question,
    options: [
      options[0]!,
      options[1]!,
      options[2],
    ],
    allowCustomAnswer: candidate.allowCustomAnswer === true,
    independentWorkMayContinue: candidate.independentWorkMayContinue === true,
    risk: normalizeRisk(candidate.risk),
    status:
      candidate.status === 'resolved' || candidate.status === 'cancelled'
        ? candidate.status
        : 'pending',
    evidence: Array.isArray(candidate.evidence)
      ? candidate.evidence
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.replace(/\s+/g, ' ').trim().slice(0, 500))
        .filter(Boolean)
        .slice(0, 20)
      : [],
    blockingTaskIds: normalizeIds(candidate.blockingTaskIds, 100),
    researchFindings: Array.isArray(candidate.researchFindings)
      ? candidate.researchFindings
        .map((item) => normalizeResearchFinding(item))
        .filter((item): item is FounderDecisionResearchFinding =>
          Boolean(item))
        .slice(-20)
      : [],
    createdAt: validIso(candidate.createdAt) ?? new Date().toISOString(),
    ...(typeof candidate.proposedGoalObjective === 'string'
      && candidate.proposedGoalObjective.trim()
      ? {
        proposedGoalObjective: candidate.proposedGoalObjective
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 500),
      }
      : {}),
    ...(Array.isArray(candidate.housekeepingCandidates)
      ? {
        housekeepingCandidates: candidate.housekeepingCandidates
          .map(normalizeHousekeepingCandidate)
          .filter((item): item is FounderHousekeepingUiCandidate =>
            Boolean(item))
          .slice(0, 100),
      }
      : {}),
    ...(Array.isArray(candidate.selectedCandidateIds)
      ? {
        selectedCandidateIds: candidate.selectedCandidateIds
          .filter((item): item is string => typeof item === 'string')
          .map((item) => item.trim().slice(0, 120))
          .filter(Boolean)
          .slice(0, 100),
      }
      : {}),
    ...(typeof candidate.selectedOptionId === 'string'
      ? { selectedOptionId: candidate.selectedOptionId.trim().slice(0, 120) }
      : {}),
    ...(typeof candidate.customAnswer === 'string'
      ? { customAnswer: candidate.customAnswer.trim().slice(0, 1_000) }
      : {}),
    ...(validIso(candidate.resolvedAt)
      ? { resolvedAt: validIso(candidate.resolvedAt)! }
      : {}),
  };
}

function normalizeOption(value: unknown): FounderGoalUiOption | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<FounderGoalUiOption>;
  const id = typeof candidate.id === 'string' ? candidate.id.trim().slice(0, 120) : '';
  const label = typeof candidate.label === 'string'
    ? candidate.label.replace(/\s+/g, ' ').trim().slice(0, 80)
    : '';
  const description = typeof candidate.description === 'string'
    ? candidate.description.replace(/\s+/g, ' ').trim().slice(0, 240)
    : '';
  if (!id || !label || !description) return null;
  return {
    id,
    label,
    description,
    ...(candidate.recommended ? { recommended: true } : {}),
  };
}

function normalizeStatus(value: unknown): FounderGoalUiStatus {
  return value === 'paused'
    || value === 'blocked'
    || value === 'verifying'
    || value === 'complete'
    ? value
    : 'active';
}

function normalizeRisk(value: unknown): FounderGoalUiDecision['risk'] {
  return value === 'reversible_write'
    || value === 'external_write'
    || value === 'destructive'
    ? value
    : 'read_only';
}

function normalizeKind(value: unknown): FounderGoalUiDecision['kind'] {
  return value === 'goal_amendment'
    || value === 'permission'
    || value === 'housekeeping'
    ? value
    : 'research_preference';
}

function normalizeHousekeepingCandidate(
  value: unknown,
): FounderHousekeepingUiCandidate | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<FounderHousekeepingUiCandidate>;
  const id = typeof candidate.id === 'string'
    ? candidate.id.trim().slice(0, 120)
    : '';
  const path = typeof candidate.path === 'string'
    ? candidate.path.replaceAll('\\', '/').trim().slice(0, 1_000)
    : '';
  if (!id || !path) return null;
  const category = candidate.category === 'generated'
    || candidate.category === 'cache'
    || candidate.category === 'duplicate'
    || candidate.category === 'obsolete_source'
    || candidate.category === 'stale_worktree'
    || candidate.category === 'archive'
    ? candidate.category
    : 'generated';
  const recommendedAction = candidate.recommendedAction === 'keep'
    || candidate.recommendedAction === 'archive'
    || candidate.recommendedAction === 'delete'
    ? candidate.recommendedAction
    : 'keep';
  return {
    id,
    path,
    sizeBytes: Number.isFinite(candidate.sizeBytes)
      ? Math.max(0, Math.floor(Number(candidate.sizeBytes)))
      : 0,
    category,
    evidence: Array.isArray(candidate.evidence)
      ? candidate.evidence
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.replace(/\s+/g, ' ').trim().slice(0, 500))
        .filter(Boolean)
        .slice(0, 20)
      : [],
    recommendedAction,
    reversible: candidate.reversible === true,
  };
}

function normalizeResearchFinding(
  value: unknown,
  fallbackCreatedAt = new Date(),
): FounderDecisionResearchFinding | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<FounderDecisionResearchFinding>;
  const id = compactText(candidate.id, 120);
  const title = compactText(candidate.title, 160);
  const summary = compactText(candidate.summary, 2_000);
  if (
    !id
    || !title
    || !summary
    || likelyContainsSecret(title)
    || likelyContainsSecret(summary)
  ) return null;
  const sources = Array.isArray(candidate.sources)
    ? candidate.sources
      .map((item) => compactText(item, 1_000))
      .filter((item): item is string => Boolean(item))
      .filter((item) => !likelyContainsSecret(item))
      .slice(0, 12)
    : [];
  return {
    id,
    title,
    summary,
    sources,
    createdAt: validIso(candidate.createdAt) ?? fallbackCreatedAt.toISOString(),
  };
}

function normalizeSelectedCandidateIds(
  value: unknown,
  candidates: FounderHousekeepingUiCandidate[],
): string[] {
  if (!Array.isArray(value)) return [];
  const allowed = new Set(
    candidates
      .filter((candidate) => candidate.recommendedAction === 'delete')
      .map((candidate) => candidate.id),
  );
  return Array.from(new Set(
    value
      .filter((item): item is string => typeof item === 'string')
      .map((item) => item.trim())
      .filter((item) => allowed.has(item)),
  )).slice(0, 100);
}

function normalizeIds(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(
    value
      .map((item) => compactText(item, 120))
      .filter((item): item is string => Boolean(item)),
  )).slice(0, limit);
}

function compactText(value: unknown, limit: number): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.replace(/\s+/g, ' ').trim().slice(0, limit);
  return normalized || null;
}

function likelyContainsSecret(value: string): boolean {
  return /\b(?:api[_-]?key|access[_-]?token|authorization|bearer)\b\s*[:=]\s*\S+/i
    .test(value)
    || /[?&](?:token|api[_-]?key|access[_-]?token)=/i.test(value);
}

export function formatFounderGoalBytes(value: number): string {
  return formatBytes(Math.max(0, Math.floor(value)));
}

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`;
  if (value < 1_024 ** 2) return `${(value / 1_024).toFixed(1)} KB`;
  if (value < 1_024 ** 3) return `${(value / 1_024 ** 2).toFixed(1)} MB`;
  return `${(value / 1_024 ** 3).toFixed(1)} GB`;
}

function validIso(value: unknown): string | null {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return null;
  return new Date(value).toISOString();
}
