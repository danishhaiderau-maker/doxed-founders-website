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
  proposedGoalObjective?: string;
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
    evidence: [
      `Current goal version: ${state.version}`,
      'Completed external actions are not reversed by a goal amendment.',
    ],
    proposedGoalObjective: normalized,
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

export function resolveFounderGoalUiDecision(
  state: FounderGoalUiState,
  input: {
    decisionId: string;
    selectedOptionId?: string;
    customAnswer?: string;
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

function validIso(value: unknown): string | null {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return null;
  return new Date(value).toISOString();
}
