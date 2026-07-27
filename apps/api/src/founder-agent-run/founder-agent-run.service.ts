import { Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import {
  buildCommandCenterRuntimeSteps,
  type BuildAdapterId,
  type FounderDecisionRequest,
  type FounderDecisionResearchFinding,
  type FounderDecisionResolution,
  type FounderAgentRunRecord,
  type FounderAgentRunWorker,
  type FounderGoalContract,
  type FounderGoalControlState,
  resolveFounderDecision,
  taskCanContinue,
  validateFounderDecisionRequest,
  validateFounderGoal,
  workerToBuildAdapter,
  buildAdapterLabel,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

const ACTIVE_RUN_KEY = '_activeAgentRun';
const GOAL_CONTROL_KEY = '_founderGoalControl';
const MAX_DECISIONS = 50;
const MAX_RESEARCH_FINDINGS = 20;
const MAX_GOAL_WORKSPACES = 50;
const DEFAULT_GOAL_WORKSPACE = 'default';

type StoredGoalControls = {
  schemaVersion: 1;
  workspaces: Record<string, FounderGoalControlState>;
};

@Injectable()
export class FounderAgentRunService {
  private readonly goalWrites = new Map<string, Promise<void>>();

  constructor(private readonly prisma: PrismaService) {}

  async getActive(userId: string): Promise<FounderAgentRunRecord | null> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const graph = settings?.memoryGraph;
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return null;
    const raw = (graph as Record<string, unknown>)[ACTIVE_RUN_KEY];
    if (!raw || typeof raw !== 'object') return null;
    return raw as FounderAgentRunRecord;
  }

  async start(
    userId: string,
    input: {
      worker: FounderAgentRunWorker;
      adapterId?: BuildAdapterId;
      adapterLabel?: string;
      status: string;
      task: string;
      repository?: string | null;
      agentId?: string | null;
      runId?: string | null;
      conversationId?: string | null;
      prUrl?: string | null;
      branch?: string | null;
      steps?: FounderAgentRunRecord['steps'];
    },
  ) {
    const now = new Date().toISOString();
    const adapterId = input.adapterId ?? workerToBuildAdapter(input.worker);
    const record: FounderAgentRunRecord = {
      worker: input.worker,
      adapterId,
      adapterLabel: input.adapterLabel ?? buildAdapterLabel(adapterId),
      status: input.status,
      task: input.task.slice(0, 1200),
      repository: input.repository ?? null,
      agentId: input.agentId ?? null,
      runId: input.runId ?? null,
      conversationId: input.conversationId ?? null,
      prUrl: input.prUrl ?? null,
      branch: input.branch ?? null,
      steps:
        input.steps ??
        buildCommandCenterRuntimeSteps({
          worker: input.worker,
          status: input.status,
          prUrl: input.prUrl,
          branch: input.branch,
        }),
      terminal: false,
      startedAt: now,
      updatedAt: now,
    };
    await this.save(userId, record);
    return record;
  }

  async patch(
    userId: string,
    patch: Partial<
      Pick<
        FounderAgentRunRecord,
        | 'status'
        | 'prUrl'
        | 'branch'
        | 'terminal'
        | 'agentId'
        | 'runId'
        | 'conversationId'
        | 'steps'
      >
    >,
  ) {
    const current = (await this.getActive(userId)) ?? null;
    if (!current) return null;
    const record: FounderAgentRunRecord = {
      ...current,
      ...patch,
      updatedAt: new Date().toISOString(),
    };
    await this.save(userId, record);
    return record;
  }

  async clear(userId: string) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const base =
      settings?.memoryGraph && typeof settings.memoryGraph === 'object' && !Array.isArray(settings.memoryGraph)
        ? { ...(settings.memoryGraph as Record<string, unknown>) }
        : {};
    delete base[ACTIVE_RUN_KEY];
    await this.prisma.founderBuilderSettings.updateMany({
      where: { userId },
      data: { memoryGraph: base as Prisma.InputJsonValue },
    });
  }

  async getGoalControl(
    userId: string,
    workspaceKey = DEFAULT_GOAL_WORKSPACE,
  ): Promise<FounderGoalControlState> {
    const graph = await this.readMemoryGraph(userId);
    const controls = normalizeStoredGoalControls(graph[GOAL_CONTROL_KEY]);
    return controls.workspaces[normalizeWorkspaceKey(workspaceKey)]
      ?? emptyGoalControlState();
  }

  async saveGoal(
    userId: string,
    goal: FounderGoalContract,
    workspaceKey = DEFAULT_GOAL_WORKSPACE,
  ) {
    const errors = validateFounderGoal(goal);
    if (errors.length > 0) throw new Error(errors.join(' '));
    const current = await this.getGoalControl(userId, workspaceKey);
    if (current.goal && goal.version < current.goal.version) {
      throw new Error('A goal update cannot move to an older version.');
    }
    const next: FounderGoalControlState = {
      ...current,
      goal: structuredClone(goal),
      updatedAt: new Date().toISOString(),
    };
    return this.saveGoalControl(userId, next, workspaceKey);
  }

  async queueDecision(
    userId: string,
    decision: FounderDecisionRequest,
    workspaceKey = DEFAULT_GOAL_WORKSPACE,
  ) {
    const errors = validateFounderDecisionRequest(decision);
    if (errors.length > 0) throw new Error(errors.join(' '));
    const current = await this.getGoalControl(userId, workspaceKey);
    if (current.goal && decision.goalId !== current.goal.id) {
      throw new Error('The decision does not belong to the active goal.');
    }
    if (current.goal && decision.goalVersion !== current.goal.version) {
      throw new Error('The decision targets a stale goal version.');
    }
    const existing = current.decisions.find((item) => item.id === decision.id);
    if (existing) {
      if (JSON.stringify(existing) !== JSON.stringify(decision)) {
        throw new Error('This decision id already belongs to another request.');
      }
      return current;
    }
    const next: FounderGoalControlState = {
      ...current,
      decisions: [...current.decisions, structuredClone(decision)].slice(-MAX_DECISIONS),
      updatedAt: new Date().toISOString(),
    };
    return this.saveGoalControl(userId, next, workspaceKey);
  }

  async appendDecisionResearch(
    userId: string,
    decisionId: string,
    finding: FounderDecisionResearchFinding,
    workspaceKey = DEFAULT_GOAL_WORKSPACE,
  ) {
    const current = await this.getGoalControl(userId, workspaceKey);
    const decision = current.decisions.find(
      (item) => item.id === decisionId && item.status === 'pending',
    );
    if (!decision) throw new Error('Founder decision is not pending.');
    const normalized = normalizeResearchFinding(finding);
    const existing = (decision.researchFindings ?? []).find(
      (item) => item.id === normalized.id,
    );
    if (existing && JSON.stringify(existing) !== JSON.stringify(normalized)) {
      throw new Error('This research id already belongs to different evidence.');
    }
    if (existing) return current;
    const next: FounderGoalControlState = {
      ...current,
      decisions: current.decisions.map((item) =>
        item.id === decision.id
          ? {
            ...item,
            researchFindings: [
              ...(item.researchFindings ?? []),
              normalized,
            ].slice(-MAX_RESEARCH_FINDINGS),
          }
          : item,
      ),
      updatedAt: new Date().toISOString(),
    };
    return this.saveGoalControl(userId, next, workspaceKey);
  }

  async resolveDecision(
    userId: string,
    input: {
      requestId: string;
      selectedOptionId?: string;
      selectedCandidateIds?: string[];
      customAnswer?: string;
    },
    workspaceKey = DEFAULT_GOAL_WORKSPACE,
  ) {
    const current = await this.getGoalControl(userId, workspaceKey);
    const request = current.decisions.find((item) => item.id === input.requestId);
    if (!request) throw new Error('Founder decision was not found.');
    const resolution = resolveFounderDecision(request, {
      selectedOptionId: input.selectedOptionId,
      selectedCandidateIds: input.selectedCandidateIds,
      customAnswer: input.customAnswer,
      resolvedBy: 'founder',
    });
    const next: FounderGoalControlState = {
      ...current,
      decisions: current.decisions.map((item) =>
        item.id === request.id ? { ...item, status: 'resolved' as const } : item,
      ),
      resolutions: [
        ...current.resolutions.filter((item) => item.requestId !== request.id),
        resolution,
      ].slice(-MAX_DECISIONS),
      updatedAt: resolution.resolvedAt,
    };
    return this.saveGoalControl(userId, next, workspaceKey);
  }

  async taskCanContinue(userId: string, taskId: string) {
    const normalizedTaskId = taskId.trim();
    if (!normalizedTaskId) return false;
    const states = await this.getAllGoalControls(userId);
    return states.every((state) =>
      taskCanContinue(normalizedTaskId, state.decisions));
  }

  async getBlockingDecisionIds(userId: string, taskId: string) {
    const normalizedTaskId = taskId.trim();
    if (!normalizedTaskId) return [];
    const states = await this.getAllGoalControls(userId);
    return Array.from(new Set(states.flatMap((state) => state.decisions
      .filter(
        (decision) =>
          decision.status === 'pending'
          && decision.blockingTaskIds.includes(normalizedTaskId),
      )
      .map((decision) => decision.id))));
  }

  private async save(userId: string, record: FounderAgentRunRecord) {
    const base = await this.readMemoryGraph(userId);
    base[ACTIVE_RUN_KEY] = record;
    await this.writeMemoryGraph(userId, base);
  }

  private async saveGoalControl(
    userId: string,
    state: FounderGoalControlState,
    workspaceKey: string,
  ) {
    let persisted = state;
    await this.withGoalWrite(userId, async () => {
      const base = await this.readMemoryGraph(userId);
      const controls = normalizeStoredGoalControls(base[GOAL_CONTROL_KEY]);
      const key = normalizeWorkspaceKey(workspaceKey);
      persisted = mergeGoalControlStates(
        controls.workspaces[key] ?? emptyGoalControlState(),
        state,
      );
      controls.workspaces[key] = persisted;
      controls.workspaces = Object.fromEntries(
        Object.entries(controls.workspaces)
          .sort((left, right) =>
            right[1].updatedAt.localeCompare(left[1].updatedAt))
          .slice(0, MAX_GOAL_WORKSPACES),
      );
      base[GOAL_CONTROL_KEY] = controls;
      await this.writeMemoryGraph(userId, base);
    });
    return persisted;
  }

  private async getAllGoalControls(userId: string) {
    const graph = await this.readMemoryGraph(userId);
    return Object.values(
      normalizeStoredGoalControls(graph[GOAL_CONTROL_KEY]).workspaces,
    );
  }

  private async withGoalWrite(
    userId: string,
    operation: () => Promise<void>,
  ) {
    const previous = this.goalWrites.get(userId) ?? Promise.resolve();
    const current = previous
      .catch(() => undefined)
      .then(operation);
    this.goalWrites.set(userId, current);
    try {
      await current;
    } finally {
      if (this.goalWrites.get(userId) === current) {
        this.goalWrites.delete(userId);
      }
    }
  }

  private async readMemoryGraph(userId: string): Promise<Record<string, unknown>> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    return settings?.memoryGraph
      && typeof settings.memoryGraph === 'object'
      && !Array.isArray(settings.memoryGraph)
      ? { ...(settings.memoryGraph as Record<string, unknown>) }
      : {};
  }

  private async writeMemoryGraph(
    userId: string,
    memoryGraph: Record<string, unknown>,
  ) {
    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        memoryGraph: memoryGraph as Prisma.InputJsonValue,
      },
      update: {
        memoryGraph: memoryGraph as Prisma.InputJsonValue,
      },
    });
  }
}

function normalizeGoalControlState(value: unknown): FounderGoalControlState {
  const now = new Date().toISOString();
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { goal: null, decisions: [], resolutions: [], updatedAt: now };
  }
  const raw = value as Partial<FounderGoalControlState>;
  const goal = raw.goal && isValidGoal(raw.goal)
    ? structuredClone(raw.goal)
    : null;
  const decisions = Array.isArray(raw.decisions)
    ? raw.decisions
      .filter(
        (item): item is FounderDecisionRequest =>
          Boolean(item)
          && typeof item === 'object'
          && isValidDecisionRequest(item),
      )
      .map((item) => ({
        ...structuredClone(item),
        researchFindings: Array.isArray(item.researchFindings)
          ? item.researchFindings
            .map((finding) => tryNormalizeResearchFinding(finding))
            .filter(
              (finding): finding is FounderDecisionResearchFinding =>
                Boolean(finding),
            )
            .slice(-MAX_RESEARCH_FINDINGS)
          : [],
      }))
      .slice(-MAX_DECISIONS)
    : [];
  const resolutions = Array.isArray(raw.resolutions)
    ? raw.resolutions
      .filter(isDecisionResolution)
      .map((item) => structuredClone(item))
      .slice(-MAX_DECISIONS)
    : [];
  return {
    goal,
    decisions,
    resolutions,
    updatedAt:
      typeof raw.updatedAt === 'string' && Number.isFinite(Date.parse(raw.updatedAt))
        ? raw.updatedAt
        : now,
  };
}

function normalizeStoredGoalControls(value: unknown): StoredGoalControls {
  if (
    value
    && typeof value === 'object'
    && !Array.isArray(value)
  ) {
    const raw = value as {
      schemaVersion?: unknown;
      workspaces?: unknown;
    };
    if (
      raw.schemaVersion === 1
      && raw.workspaces
      && typeof raw.workspaces === 'object'
      && !Array.isArray(raw.workspaces)
    ) {
      const workspaces = Object.fromEntries(
        Object.entries(raw.workspaces as Record<string, unknown>)
          .map(([key, state]) => [
            tryNormalizeWorkspaceKey(key),
            normalizeGoalControlState(state),
          ] as const)
          .filter(
            (entry): entry is readonly [string, FounderGoalControlState] =>
              Boolean(entry[0]),
          )
          .sort((left, right) =>
            right[1].updatedAt.localeCompare(left[1].updatedAt))
          .slice(0, MAX_GOAL_WORKSPACES),
      );
      return { schemaVersion: 1, workspaces };
    }
  }
  const legacy = normalizeGoalControlState(value);
  return {
    schemaVersion: 1,
    workspaces:
      legacy.goal || legacy.decisions.length > 0 || legacy.resolutions.length > 0
        ? { [DEFAULT_GOAL_WORKSPACE]: legacy }
        : {},
  };
}

function emptyGoalControlState(): FounderGoalControlState {
  return {
    goal: null,
    decisions: [],
    resolutions: [],
    updatedAt: new Date().toISOString(),
  };
}

function mergeGoalControlStates(
  existing: FounderGoalControlState,
  incoming: FounderGoalControlState,
): FounderGoalControlState {
  const decisions = new Map<string, FounderDecisionRequest>();
  for (const decision of [...existing.decisions, ...incoming.decisions]) {
    const prior = decisions.get(decision.id);
    decisions.set(
      decision.id,
      prior ? mergeDecisionRequests(prior, decision) : structuredClone(decision),
    );
  }

  const resolutions = new Map<string, FounderDecisionResolution>();
  for (const resolution of [...existing.resolutions, ...incoming.resolutions]) {
    const prior = resolutions.get(resolution.requestId);
    resolutions.set(
      resolution.requestId,
      prior ? mergeDecisionResolutions(prior, resolution) : structuredClone(resolution),
    );
  }

  for (const requestId of resolutions.keys()) {
    const decision = decisions.get(requestId);
    if (decision) decisions.set(requestId, { ...decision, status: 'resolved' });
  }

  const mergedDecisions = Array.from(decisions.values())
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
    .slice(-MAX_DECISIONS);
  const retainedIds = new Set(mergedDecisions.map((decision) => decision.id));
  const mergedResolutions = Array.from(resolutions.values())
    .filter((resolution) => retainedIds.has(resolution.requestId))
    .sort((left, right) => left.resolvedAt.localeCompare(right.resolvedAt))
    .slice(-MAX_DECISIONS);

  return {
    goal: mergeGoals(existing.goal, incoming.goal),
    decisions: mergedDecisions,
    resolutions: mergedResolutions,
    updatedAt: latestTimestamp(
      existing.updatedAt,
      incoming.updatedAt,
      ...mergedResolutions.map((resolution) => resolution.resolvedAt),
      ...mergedDecisions.flatMap((decision) =>
        (decision.researchFindings ?? []).map((finding) => finding.createdAt)),
    ),
  };
}

function mergeGoals(
  existing: FounderGoalContract | null,
  incoming: FounderGoalContract | null,
): FounderGoalContract | null {
  if (!existing) return incoming ? structuredClone(incoming) : null;
  if (!incoming) return structuredClone(existing);
  if (existing.id === incoming.id && existing.version !== incoming.version) {
    return structuredClone(
      incoming.version > existing.version ? incoming : existing,
    );
  }
  return structuredClone(
    incoming.updatedAt > existing.updatedAt ? incoming : existing,
  );
}

function mergeDecisionRequests(
  existing: FounderDecisionRequest,
  incoming: FounderDecisionRequest,
): FounderDecisionRequest {
  if (
    JSON.stringify(decisionIdentity(existing))
    !== JSON.stringify(decisionIdentity(incoming))
  ) {
    throw new Error('This decision id already belongs to another request.');
  }

  const research = new Map<string, FounderDecisionResearchFinding>();
  for (const finding of [
    ...(existing.researchFindings ?? []),
    ...(incoming.researchFindings ?? []),
  ]) {
    const prior = research.get(finding.id);
    if (prior && JSON.stringify(prior) !== JSON.stringify(finding)) {
      throw new Error('This research id already belongs to different evidence.');
    }
    research.set(finding.id, structuredClone(finding));
  }

  const statusRank = { pending: 0, cancelled: 1, resolved: 2 } as const;
  const status =
    statusRank[incoming.status] > statusRank[existing.status]
      ? incoming.status
      : existing.status;
  return {
    ...structuredClone(existing),
    status,
    researchFindings: Array.from(research.values())
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
      .slice(-MAX_RESEARCH_FINDINGS),
  };
}

function decisionIdentity(request: FounderDecisionRequest) {
  const { status: _status, researchFindings: _research, ...identity } = request;
  return identity;
}

function mergeDecisionResolutions(
  existing: FounderDecisionResolution,
  incoming: FounderDecisionResolution,
): FounderDecisionResolution {
  if (resolutionIdentity(existing) !== resolutionIdentity(incoming)) {
    throw new Error('This decision already has a conflicting resolution.');
  }
  return structuredClone(
    incoming.resolvedAt > existing.resolvedAt ? incoming : existing,
  );
}

function resolutionIdentity(resolution: FounderDecisionResolution) {
  return JSON.stringify({
    requestId: resolution.requestId,
    selectedOptionId: resolution.selectedOptionId ?? null,
    selectedCandidateIds: [...(resolution.selectedCandidateIds ?? [])].sort(),
    customAnswer: resolution.customAnswer ?? null,
    resolvedBy: resolution.resolvedBy,
  });
}

function latestTimestamp(...values: string[]) {
  return values.reduce(
    (latest, value) => value > latest ? value : latest,
    new Date(0).toISOString(),
  );
}

function normalizeWorkspaceKey(value: string): string {
  const normalized = tryNormalizeWorkspaceKey(value);
  if (!normalized) throw new Error('Founder workspace key is invalid.');
  return normalized;
}

function tryNormalizeWorkspaceKey(value: unknown): string {
  if (typeof value !== 'string') return '';
  const normalized = value.trim().slice(0, 120);
  return /^[a-z0-9][a-z0-9:_-]{0,119}$/i.test(normalized)
    ? normalized
    : '';
}

function normalizeResearchFinding(value: unknown): FounderDecisionResearchFinding {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Founder research finding is invalid.');
  }
  const raw = value as Partial<FounderDecisionResearchFinding>;
  const id = cleanText(raw.id, 120);
  const title = cleanText(raw.title, 160);
  const summary = cleanText(raw.summary, 2_000);
  if (!id || !title || !summary) {
    throw new Error('Founder research finding is incomplete.');
  }
  if (looksSecretLike(title) || looksSecretLike(summary)) {
    throw new Error('Founder research cannot store secret-like content.');
  }
  const sources = Array.isArray(raw.sources)
    ? raw.sources
      .map((source) => cleanText(source, 500))
      .filter((source) => source && !looksSecretLike(source))
      .slice(0, 20)
    : [];
  return {
    id,
    title,
    summary,
    sources,
    createdAt:
      typeof raw.createdAt === 'string' && Number.isFinite(Date.parse(raw.createdAt))
        ? raw.createdAt
        : new Date().toISOString(),
  };
}

function tryNormalizeResearchFinding(
  value: unknown,
): FounderDecisionResearchFinding | null {
  try {
    return normalizeResearchFinding(value);
  } catch {
    return null;
  }
}

function isValidDecisionRequest(value: unknown): value is FounderDecisionRequest {
  try {
    return validateFounderDecisionRequest(
      value as FounderDecisionRequest,
    ).length === 0;
  } catch {
    return false;
  }
}

function isValidGoal(value: unknown): value is FounderGoalContract {
  try {
    return validateFounderGoal(value as FounderGoalContract).length === 0;
  } catch {
    return false;
  }
}

function isDecisionResolution(value: unknown): value is FounderDecisionResolution {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const raw = value as Partial<FounderDecisionResolution>;
  return (
    typeof raw.requestId === 'string'
    && Boolean(raw.requestId.trim())
    && typeof raw.resolvedAt === 'string'
    && Number.isFinite(Date.parse(raw.resolvedAt))
    && (raw.resolvedBy === 'founder' || raw.resolvedBy === 'approved_policy')
  );
}

function cleanText(value: unknown, max: number) {
  return typeof value === 'string'
    ? value.replace(/\s+/g, ' ').trim().slice(0, max)
    : '';
}

function looksSecretLike(value: string) {
  return /(bearer\s+[a-z0-9._-]{12,}|(?:api[_ -]?key|secret|token)\s*[:=]\s*\S{8,}|sk-[a-z0-9_-]{12,})/i
    .test(value);
}
