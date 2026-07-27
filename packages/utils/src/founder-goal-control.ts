export type FounderGoalStatus =
  | 'draft'
  | 'active'
  | 'paused'
  | 'blocked'
  | 'verifying'
  | 'complete'
  | 'cancelled';

export type FounderEvidenceKind =
  | 'test'
  | 'build'
  | 'visual'
  | 'remote'
  | 'receipt'
  | 'human';

export type FounderGoalEvidenceRequirement = {
  id: string;
  label: string;
  kind: FounderEvidenceKind;
  required: boolean;
};

export type FounderGoalContract = {
  id: string;
  version: number;
  objective: string;
  constraints: string[];
  successEvidence: FounderGoalEvidenceRequirement[];
  status: FounderGoalStatus;
  updatedAt: string;
  tokenBudget?: number;
  timeBudgetMinutes?: number;
};

export type FounderDecisionKind =
  | 'goal_amendment'
  | 'permission'
  | 'housekeeping'
  | 'research_preference';

export type FounderDecisionRisk =
  | 'read_only'
  | 'reversible_write'
  | 'external_write'
  | 'destructive';

export type FounderDecisionOption = {
  id: string;
  label: string;
  description: string;
  impact: string;
  recommended?: boolean;
};

export type FounderDecisionResearchFinding = {
  id: string;
  title: string;
  summary: string;
  sources: string[];
  createdAt: string;
};

export type FounderDecisionRequest = {
  id: string;
  goalId: string;
  goalVersion: number;
  kind: FounderDecisionKind;
  risk: FounderDecisionRisk;
  title: string;
  question: string;
  options: [
    FounderDecisionOption,
    FounderDecisionOption,
    FounderDecisionOption?,
  ];
  allowCustomAnswer: boolean;
  blockingTaskIds: string[];
  independentWorkMayContinue: boolean;
  evidence: string[];
  createdAt: string;
  status: 'pending' | 'resolved' | 'cancelled';
  autoResolveOptionId?: string;
  expiresAt?: string;
  proposedGoalObjective?: string;
  housekeepingCandidates?: FounderHousekeepingCandidate[];
  researchFindings?: FounderDecisionResearchFinding[];
};

export type FounderDecisionResolution = {
  requestId: string;
  selectedOptionId?: string;
  selectedCandidateIds?: string[];
  customAnswer?: string;
  resolvedAt: string;
  resolvedBy: 'founder' | 'approved_policy';
};

export type FounderHousekeepingCandidate = {
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
};

export type FounderGoalControlState = {
  goal: FounderGoalContract | null;
  decisions: FounderDecisionRequest[];
  resolutions: FounderDecisionResolution[];
  updatedAt: string;
};

export function validateFounderGoal(
  goal: FounderGoalContract,
): string[] {
  const errors: string[] = [];
  if (!goal.id.trim()) errors.push('Goal id is required.');
  if (!goal.objective.trim()) errors.push('Goal objective is required.');
  if (!Number.isInteger(goal.version) || goal.version < 1) {
    errors.push('Goal version must be a positive integer.');
  }
  if (goal.successEvidence.length === 0) {
    errors.push('At least one success-evidence requirement is required.');
  }
  if (new Set(goal.successEvidence.map((item) => item.id)).size !== goal.successEvidence.length) {
    errors.push('Success-evidence ids must be unique.');
  }
  return errors;
}

export function validateFounderDecisionRequest(
  request: FounderDecisionRequest,
): string[] {
  const errors: string[] = [];
  const options = request.options.filter(
    (option): option is FounderDecisionOption => Boolean(option),
  );
  if (!request.id.trim()) errors.push('Decision id is required.');
  if (!request.goalId.trim()) errors.push('Decision goal id is required.');
  if (!request.title.trim()) errors.push('Decision title is required.');
  if (!request.question.trim()) errors.push('Decision question is required.');
  if (options.length < 2 || options.length > 3) {
    errors.push('A decision must present two or three options.');
  }
  if (new Set(options.map((option) => option.id)).size !== options.length) {
    errors.push('Decision option ids must be unique.');
  }
  if (options.filter((option) => option.recommended).length > 1) {
    errors.push('Only one option may be recommended.');
  }
  if (
    request.autoResolveOptionId
    && !options.some((option) => option.id === request.autoResolveOptionId)
  ) {
    errors.push('The auto-resolve option must be one of the presented options.');
  }
  if (
    request.autoResolveOptionId
    && !canUseApprovedPolicy(request)
  ) {
    errors.push('This decision kind or risk cannot be auto-resolved.');
  }
  return errors;
}

export function taskCanContinue(
  taskId: string,
  decisions: FounderDecisionRequest[],
): boolean {
  return !decisions.some(
    (decision) =>
      decision.status === 'pending'
      && decision.blockingTaskIds.includes(taskId),
  );
}

export function canUseApprovedPolicy(
  request: FounderDecisionRequest,
): boolean {
  return (
    request.kind === 'research_preference'
    && request.risk === 'read_only'
    && request.independentWorkMayContinue
  );
}

export function resolveFounderDecision(
  request: FounderDecisionRequest,
  input: {
    selectedOptionId?: string;
    customAnswer?: string;
    selectedCandidateIds?: string[];
    resolvedBy?: FounderDecisionResolution['resolvedBy'];
    now?: Date;
  },
): FounderDecisionResolution {
  if (request.status !== 'pending') {
    throw new Error('Only a pending decision can be resolved.');
  }
  const selectedOptionId = input.selectedOptionId?.trim();
  const customAnswer = input.customAnswer?.trim();
  const optionIds = request.options
    .filter((option): option is FounderDecisionOption => Boolean(option))
    .map((option) => option.id);
  if (selectedOptionId && !optionIds.includes(selectedOptionId)) {
    throw new Error('The selected decision option is invalid.');
  }
  if (customAnswer && !request.allowCustomAnswer) {
    throw new Error('This decision does not accept a custom answer.');
  }
  if (!selectedOptionId && !customAnswer) {
    throw new Error('Select an option or provide a custom answer.');
  }
  const selectedCandidateIds =
    request.kind === 'housekeeping'
    && selectedOptionId === 'approve_selected'
      ? normalizeSelectedHousekeepingIds(
        input.selectedCandidateIds,
        request.housekeepingCandidates ?? [],
      )
      : [];
  if (
    request.kind === 'housekeeping'
    && selectedOptionId === 'approve_selected'
    && selectedCandidateIds.length === 0
  ) {
    throw new Error('Select at least one housekeeping candidate.');
  }
  const resolvedBy = input.resolvedBy ?? 'founder';
  if (resolvedBy === 'approved_policy' && !canUseApprovedPolicy(request)) {
    throw new Error('This decision requires the founder.');
  }
  return {
    requestId: request.id,
    ...(selectedOptionId ? { selectedOptionId } : {}),
    ...(selectedCandidateIds.length > 0 ? { selectedCandidateIds } : {}),
    ...(customAnswer ? { customAnswer } : {}),
    resolvedAt: (input.now ?? new Date()).toISOString(),
    resolvedBy,
  };
}

export function canCompleteFounderGoal(
  goal: FounderGoalContract,
  satisfiedEvidenceIds: Iterable<string>,
): boolean {
  if (validateFounderGoal(goal).length > 0) return false;
  const satisfied = new Set(satisfiedEvidenceIds);
  return goal.successEvidence
    .filter((requirement) => requirement.required)
    .every((requirement) => satisfied.has(requirement.id));
}

export function createHousekeepingDecision(
  input: {
    id: string;
    goal: FounderGoalContract;
    candidates: FounderHousekeepingCandidate[];
    createdAt?: Date;
    blockingTaskIds?: string[];
  },
): FounderDecisionRequest {
  const deleteCandidates = input.candidates.filter(
    (candidate) => candidate.recommendedAction === 'delete',
  );
  const deleteBytes = deleteCandidates.reduce(
    (total, candidate) => total + Math.max(0, candidate.sizeBytes),
    0,
  );
  const reversible = deleteCandidates.every((candidate) => candidate.reversible);
  return {
    id: input.id,
    goalId: input.goal.id,
    goalVersion: input.goal.version,
    kind: 'housekeeping',
    risk: reversible ? 'reversible_write' : 'destructive',
    title: 'Review housekeeping candidates',
    question:
      `Review ${deleteCandidates.length} proposed deletions `
      + `(${formatBytes(deleteBytes)}). What should Founder do?`,
    options: [
      {
        id: 'approve_selected',
        label: 'Approve checked',
        description: 'Grant permission only for checked deletion candidates.',
        impact: reversible
          ? 'A checkpoint is retained for restore.'
          : 'At least one selected deletion is not automatically reversible.',
        ...(reversible ? { recommended: true } : {}),
      },
      {
        id: 'keep_all',
        label: 'Keep everything',
        description: 'Cancel this deletion batch.',
        impact: 'No disk space is reclaimed.',
        ...(!reversible ? { recommended: true } : {}),
      },
    ],
    allowCustomAnswer: true,
    blockingTaskIds: input.blockingTaskIds ?? [],
    independentWorkMayContinue: true,
    evidence: input.candidates.flatMap((candidate) =>
      candidate.evidence.map((item) => `${candidate.path}: ${item}`),
    ),
    createdAt: (input.createdAt ?? new Date()).toISOString(),
    status: 'pending',
    housekeepingCandidates: input.candidates,
  };
}

function normalizeSelectedHousekeepingIds(
  value: unknown,
  candidates: FounderHousekeepingCandidate[],
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

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`;
  if (value < 1_024 ** 2) return `${(value / 1_024).toFixed(1)} KB`;
  if (value < 1_024 ** 3) return `${(value / 1_024 ** 2).toFixed(1)} MB`;
  return `${(value / 1_024 ** 3).toFixed(1)} GB`;
}
