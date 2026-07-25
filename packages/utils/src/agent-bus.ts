import { createHash } from 'node:crypto';

/** Agent-to-agent contracts. V2 keeps the original fields API-compatible. */

export type AgentBusEventKind =
  | 'RESEARCH_COMPLETED'
  | 'BUILD_COMPLETED'
  | 'BUILD_FAILED'
  | 'CONTENT_DRAFT_READY';

export type AgentBusRole = 'research' | 'builder' | 'content' | 'founder_brain';
export type AgentBusTarget = 'builder' | 'content' | 'founder_queue';

export interface AgentBusBelief {
  tag: string;
  value: unknown;
  confidence: number;
  ttlMs: number;
}

export interface AgentBusVerificationCheck {
  name: string;
  passed: boolean;
  detail?: string;
}

export type AgentBusHandoff = {
  version?: 1 | 2;
  id: string;
  from: AgentBusRole;
  to: AgentBusTarget;
  kind: AgentBusEventKind;
  title: string;
  detail: string;
  dependsOn?: string[];
  supersedes?: string;
  replyTo?: string;
  priorAttempts?: number;
  stallThreshold?: number;
  /** Disk-backed contract. `scope` is authoritative for mutating work. */
  payload: {
    spec?: string;
    prompt?: string;
    sourceTask?: string;
    artifactPath?: string;
    scope?: string[];
    budgetTokens?: number;
    budgetMs?: number;
    capabilityTags?: string[];
    beliefs?: AgentBusBelief[];
  };
};

export type AgentBusInput = {
  kind: AgentBusEventKind;
  founderId: string;
  projectId?: string | null;
  title: string;
  detail: string;
  sourceTask?: string;
  buildOutput?: { status: string; prUrl?: string | null; result?: string | null };
  researchSummary?: string;
  dependsOn?: string[];
  supersedes?: string;
  replyTo?: string;
  artifactPath?: string;
  scope?: string[];
  budgetTokens?: number;
  budgetMs?: number;
  capabilityTags?: string[];
  priorAttempts?: number;
  stallThreshold?: number;
};

export interface AgentBusGraphResult {
  ordered: AgentBusHandoff[];
  ready: AgentBusHandoff[];
  blocked: Array<{ handoff: AgentBusHandoff; waitingFor: string[] }>;
  supersededIds: string[];
}

export interface AgentBusScopeClaim {
  handoffId: string;
  path: string;
  fencingToken: string;
  generation: number;
  expiresAt: string;
}

export interface AgentBusCompletionReceipt {
  version: 1;
  handoffId: string;
  artifactPath?: string;
  changedFiles: string[];
  claims: Array<{ path: string; fencingToken: string; generation: number }>;
  checks: AgentBusVerificationCheck[];
  durationMs?: number;
  tokensUsed?: number;
}

export type AgentBusCompletionValidation =
  | { ok: true }
  | { ok: false; reason: string };

export type AgentBusLedgerState =
  | 'planned'
  | 'claimed'
  | 'running'
  | 'blocked'
  | 'verifying'
  | 'complete'
  | 'failed'
  | 'superseded';

export type AgentBusLedgerEventType =
  | 'CREATED'
  | 'CLAIMED'
  | 'STARTED'
  | 'BLOCKED'
  | 'RESUMED'
  | 'VERIFYING'
  | 'COMPLETED'
  | 'FAILED'
  | 'SUPERSEDED';

export interface AgentBusLedgerEvent {
  eventId: string;
  sequence: number;
  handoffId: string;
  type: AgentBusLedgerEventType;
  at: string;
  actor: string;
  reason?: string;
}

export interface AgentBusReplayResult {
  acceptedEvents: AgentBusLedgerEvent[];
  stateByHandoff: Map<string, AgentBusLedgerState>;
}

export class AgentBusInvariantError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AgentBusInvariantError';
  }
}

/** Declarative rules only. IDs are deterministic so retries do not duplicate work. */
export function planAgentBusHandoffs(input: AgentBusInput): AgentBusHandoff[] {
  const out: AgentBusHandoff[] = [];
  const add = (handoff: Omit<AgentBusHandoff, 'id' | 'version'>): void => {
    const id = deterministicHandoffId(input, out.length, handoff.to, handoff.title);
    out.push({
      ...handoff,
      id,
      version: 2,
      dependsOn: normalizeIds(input.dependsOn),
      supersedes: cleanId(input.supersedes),
      replyTo: cleanId(input.replyTo),
      priorAttempts: boundedInteger(input.priorAttempts, 0, 100) ?? 0,
      stallThreshold: boundedInteger(input.stallThreshold, 1, 10) ?? 2,
      payload: {
        ...handoff.payload,
        artifactPath: cleanRelativePath(input.artifactPath),
        scope: normalizeScope(input.scope),
        budgetTokens: boundedInteger(input.budgetTokens, 256, 2_000_000),
        budgetMs: boundedInteger(input.budgetMs, 1_000, 86_400_000),
        capabilityTags: normalizeTags(input.capabilityTags),
      },
    });
  };

  if (input.kind === 'RESEARCH_COMPLETED' && input.researchSummary) {
    const wantsBuild =
      /\b(implement|build|ship|add|fix|competitor|feature|gap)\b/i.test(
        `${input.title} ${input.detail} ${input.researchSummary}`,
      );
    if (wantsBuild) {
      add({
        from: 'research',
        to: 'builder',
        kind: 'RESEARCH_COMPLETED',
        title: 'Implementation proposal from research',
        detail: input.researchSummary.slice(0, 400),
        payload: {
          spec: `Based on research: ${input.title}. ${input.detail}`.slice(0, 1200),
          sourceTask: input.sourceTask,
        },
      });
    }
  }

  if (input.kind === 'BUILD_COMPLETED') {
    const pr = input.buildOutput?.prUrl;
    add({
      from: 'builder',
      to: 'content',
      kind: 'BUILD_COMPLETED',
      title: pr ? 'Draft founder update for shipped work' : 'Draft update for completed build',
      detail: input.buildOutput?.result?.slice(0, 300) ?? input.detail,
      payload: {
        prompt: `Write a short founder build update for: ${input.title}. ${input.detail}`.slice(
          0,
          800,
        ),
        spec: input.sourceTask,
      },
    });
    if (pr) {
      add({
        from: 'builder',
        to: 'founder_queue',
        kind: 'BUILD_COMPLETED',
        title: 'Review and merge PR',
        detail: pr,
        payload: { prompt: `Review PR: ${pr}` },
      });
    }
  }

  if (input.kind === 'BUILD_FAILED') {
    add({
      from: 'builder',
      to: 'founder_queue',
      kind: 'BUILD_FAILED',
      title: 'Builder run needs attention',
      detail: input.buildOutput?.result?.slice(0, 200) ?? input.detail,
      payload: {
        prompt: `Builder failed on: ${input.sourceTask ?? input.title}. Suggest a smaller fix.`,
      },
    });
  }

  return out;
}

/** Resolve dependencies, revisions, and cycles before any worker is started. */
export function resolveAgentBusGraph(
  handoffs: readonly AgentBusHandoff[],
  completedIds: ReadonlySet<string> = new Set(),
): AgentBusGraphResult {
  const byId = new Map<string, AgentBusHandoff>();
  for (const handoff of handoffs) {
    if (!handoff.id.trim()) throw new AgentBusInvariantError('A handoff ID cannot be empty.');
    if (byId.has(handoff.id)) {
      throw new AgentBusInvariantError(`Duplicate handoff ID: ${handoff.id}`);
    }
    byId.set(handoff.id, handoff);
  }

  const replacements = new Map<string, string>();
  for (const handoff of handoffs) {
    if (!handoff.supersedes) continue;
    if (handoff.supersedes === handoff.id) {
      throw new AgentBusInvariantError(`${handoff.id} cannot supersede itself.`);
    }
    if (!byId.has(handoff.supersedes)) {
      throw new AgentBusInvariantError(
        `${handoff.id} supersedes missing handoff ${handoff.supersedes}.`,
      );
    }
    if (replacements.has(handoff.supersedes)) {
      throw new AgentBusInvariantError(`${handoff.supersedes} has more than one replacement.`);
    }
    replacements.set(handoff.supersedes, handoff.id);
  }

  const canonical = (id: string): string => {
    const seen = new Set<string>();
    let current = id;
    while (replacements.has(current)) {
      if (seen.has(current)) {
        throw new AgentBusInvariantError(`Supersession cycle includes ${current}.`);
      }
      seen.add(current);
      current = replacements.get(current)!;
    }
    return current;
  };

  for (const id of replacements.keys()) canonical(id);
  const supersededIds = [...replacements.keys()].sort();
  const active = handoffs.filter((handoff) => !replacements.has(handoff.id));
  const dependencies = new Map<string, string[]>();
  for (const handoff of active) {
    const next = [...new Set(normalizeIds(handoff.dependsOn).map(canonical))]
      .filter((id) => id !== handoff.id);
    for (const dependency of next) {
      if (!byId.has(dependency) && !completedIds.has(dependency)) {
        throw new AgentBusInvariantError(
          `${handoff.id} depends on missing handoff ${dependency}.`,
        );
      }
    }
    dependencies.set(handoff.id, next);
  }

  const visiting = new Set<string>();
  const visited = new Set<string>();
  const ordered: AgentBusHandoff[] = [];
  const visit = (handoff: AgentBusHandoff): void => {
    if (visited.has(handoff.id)) return;
    if (visiting.has(handoff.id)) {
      throw new AgentBusInvariantError(`Dependency cycle includes ${handoff.id}.`);
    }
    visiting.add(handoff.id);
    for (const dependency of dependencies.get(handoff.id) ?? []) {
      const dependencyHandoff = byId.get(dependency);
      if (dependencyHandoff && !replacements.has(dependencyHandoff.id)) visit(dependencyHandoff);
    }
    visiting.delete(handoff.id);
    visited.add(handoff.id);
    ordered.push(handoff);
  };
  for (const handoff of active) visit(handoff);

  const ready: AgentBusHandoff[] = [];
  const blocked: Array<{ handoff: AgentBusHandoff; waitingFor: string[] }> = [];
  for (const handoff of ordered) {
    const waitingFor = (dependencies.get(handoff.id) ?? [])
      .filter((dependency) => !completedIds.has(dependency));
    if (waitingFor.length === 0) ready.push(handoff);
    else blocked.push({ handoff, waitingFor });
  }
  return { ordered, ready, blocked, supersededIds };
}

/** Enforce the declared mutation boundary. No scope means read-only. */
export function agentBusScopeAllows(
  scope: readonly string[] | undefined,
  candidatePath: string,
): boolean {
  const candidate = normalizeRelativePath(candidatePath);
  if (!candidate || !scope?.length) return false;
  return normalizeScope(scope).some((pattern) => globMatches(pattern, candidate));
}

/** Reject stale workers and unverified output before integration. */
export function validateAgentBusCompletion(
  handoff: AgentBusHandoff,
  receipt: AgentBusCompletionReceipt,
  activeClaims: readonly AgentBusScopeClaim[],
  now = Date.now(),
): AgentBusCompletionValidation {
  if (receipt.version !== 1) {
    return { ok: false, reason: 'The completion evidence version is unsupported.' };
  }
  if (receipt.handoffId !== handoff.id) {
    return { ok: false, reason: 'The completion receipt belongs to another handoff.' };
  }
  if (handoff.payload.artifactPath) {
    const expected = normalizeRelativePath(handoff.payload.artifactPath);
    if (!expected || normalizeRelativePath(receipt.artifactPath ?? '') !== expected) {
      return { ok: false, reason: 'The required handoff artifact is missing or mismatched.' };
    }
  }
  if (receipt.checks.length === 0 || receipt.checks.some((check) => !check.passed)) {
    return { ok: false, reason: 'Completion requires at least one passing verification check.' };
  }
  if (
    handoff.payload.budgetMs != null
    && (receipt.durationMs == null || receipt.durationMs > handoff.payload.budgetMs)
  ) {
    return { ok: false, reason: 'Completion exceeded the handoff time budget.' };
  }
  if (
    handoff.payload.budgetTokens != null
    && (receipt.tokensUsed == null || receipt.tokensUsed > handoff.payload.budgetTokens)
  ) {
    return { ok: false, reason: 'Completion exceeded the handoff token budget.' };
  }
  const receiptClaims = new Map(
    receipt.claims.map((claim) => [normalizeRelativePath(claim.path), claim]),
  );
  for (const changedFile of receipt.changedFiles) {
    const normalized = normalizeRelativePath(changedFile);
    if (!normalized || !agentBusScopeAllows(handoff.payload.scope, normalized)) {
      return { ok: false, reason: `${changedFile} is outside the handoff scope.` };
    }
    const claim = activeClaims.find(
      (candidate) =>
        candidate.handoffId === handoff.id
        && normalizeRelativePath(candidate.path) === normalized,
    );
    const supplied = receiptClaims.get(normalized);
    if (!claim || !supplied) {
      return { ok: false, reason: `${changedFile} has no active ownership claim.` };
    }
    if (Date.parse(claim.expiresAt) <= now) {
      return { ok: false, reason: `${changedFile} ownership expired before completion.` };
    }
    if (
      claim.fencingToken !== supplied.fencingToken
      || claim.generation !== supplied.generation
    ) {
      return { ok: false, reason: `${changedFile} has a stale fencing token.` };
    }
  }
  return { ok: true };
}

export function agentBusRetryDecision(
  handoff: Pick<AgentBusHandoff, 'priorAttempts' | 'stallThreshold'>,
): 'retry' | 'escalate' {
  const attempts = boundedInteger(handoff.priorAttempts, 0, 100) ?? 0;
  const threshold = boundedInteger(handoff.stallThreshold, 1, 10) ?? 2;
  return attempts >= threshold ? 'escalate' : 'retry';
}

/** Replay an append-only ledger. Duplicate event IDs are idempotent. */
export function replayAgentBusEvents(
  events: readonly AgentBusLedgerEvent[],
): AgentBusReplayResult {
  const acceptedEvents: AgentBusLedgerEvent[] = [];
  const byEventId = new Map<string, string>();
  const sequences = new Set<number>();
  const stateByHandoff = new Map<string, AgentBusLedgerState>();
  let previousSequence = -1;

  for (const event of events) {
    const serialized = JSON.stringify(event);
    const duplicate = byEventId.get(event.eventId);
    if (duplicate) {
      if (duplicate !== serialized) {
        throw new AgentBusInvariantError(`Event ID ${event.eventId} was reused with new data.`);
      }
      continue;
    }
    if (!Number.isInteger(event.sequence) || event.sequence < 0) {
      throw new AgentBusInvariantError(`Invalid event sequence for ${event.eventId}.`);
    }
    if (sequences.has(event.sequence) || event.sequence <= previousSequence) {
      throw new AgentBusInvariantError(`Event sequence ${event.sequence} is stale or duplicated.`);
    }
    if (!Number.isFinite(Date.parse(event.at))) {
      throw new AgentBusInvariantError(`Event ${event.eventId} has an invalid timestamp.`);
    }
    const current = stateByHandoff.get(event.handoffId);
    const next = nextLedgerState(current, event.type);
    stateByHandoff.set(event.handoffId, next);
    byEventId.set(event.eventId, serialized);
    sequences.add(event.sequence);
    previousSequence = event.sequence;
    acceptedEvents.push(event);
  }
  return { acceptedEvents, stateByHandoff };
}

/** Stable handoff key for dedupe across retries and process restarts. */
export function agentBusHandoffFingerprint(h: AgentBusHandoff): string {
  const stable = JSON.stringify({
    to: h.to,
    kind: h.kind,
    title: h.title.trim().toLowerCase().slice(0, 80),
    spec: h.payload.spec?.trim().slice(0, 240) ?? '',
    prompt: h.payload.prompt?.trim().slice(0, 240) ?? '',
    scope: normalizeScope(h.payload.scope),
    dependsOn: normalizeIds(h.dependsOn),
    supersedes: cleanId(h.supersedes),
    replyTo: cleanId(h.replyTo),
    artifactPath: cleanRelativePath(h.payload.artifactPath),
    budgetTokens: boundedInteger(h.payload.budgetTokens, 256, 2_000_000),
    budgetMs: boundedInteger(h.payload.budgetMs, 1_000, 86_400_000),
    capabilityTags: normalizeTags(h.payload.capabilityTags),
    priorAttempts: boundedInteger(h.priorAttempts, 0, 100) ?? 0,
    stallThreshold: boundedInteger(h.stallThreshold, 1, 10) ?? 2,
  });
  return createHash('sha256').update(stable).digest('hex');
}

function deterministicHandoffId(
  input: AgentBusInput,
  index: number,
  target: AgentBusTarget,
  title: string,
): string {
  const digest = createHash('sha256')
    .update(JSON.stringify({
      founderId: input.founderId,
      projectId: input.projectId ?? null,
      kind: input.kind,
      title: input.title.trim(),
      detail: input.detail.trim(),
      sourceTask: input.sourceTask?.trim() ?? '',
      target,
      handoffTitle: title,
      index,
      dependsOn: normalizeIds(input.dependsOn),
      supersedes: cleanId(input.supersedes),
      replyTo: cleanId(input.replyTo),
      artifactPath: cleanRelativePath(input.artifactPath),
      scope: normalizeScope(input.scope),
      budgetTokens: boundedInteger(input.budgetTokens, 256, 2_000_000),
      budgetMs: boundedInteger(input.budgetMs, 1_000, 86_400_000),
      capabilityTags: normalizeTags(input.capabilityTags),
      priorAttempts: boundedInteger(input.priorAttempts, 0, 100) ?? 0,
    }))
    .digest('hex')
    .slice(0, 24);
  return `bus-${digest}`;
}

function nextLedgerState(
  current: AgentBusLedgerState | undefined,
  event: AgentBusLedgerEventType,
): AgentBusLedgerState {
  if (event === 'CREATED') {
    if (current) throw new AgentBusInvariantError('A handoff can only be created once.');
    return 'planned';
  }
  if (!current) throw new AgentBusInvariantError(`${event} arrived before CREATED.`);
  const allowed: Record<Exclude<AgentBusLedgerEventType, 'CREATED'>, AgentBusLedgerState[]> = {
    CLAIMED: ['planned', 'blocked'],
    STARTED: ['claimed'],
    BLOCKED: ['claimed', 'running'],
    RESUMED: ['blocked'],
    VERIFYING: ['running'],
    COMPLETED: ['verifying'],
    FAILED: ['claimed', 'running', 'blocked', 'verifying'],
    SUPERSEDED: ['planned', 'claimed', 'running', 'blocked', 'verifying'],
  };
  if (!allowed[event].includes(current)) {
    throw new AgentBusInvariantError(`Illegal ${current} -> ${event} transition.`);
  }
  return {
    CLAIMED: 'claimed',
    STARTED: 'running',
    BLOCKED: 'blocked',
    RESUMED: 'running',
    VERIFYING: 'verifying',
    COMPLETED: 'complete',
    FAILED: 'failed',
    SUPERSEDED: 'superseded',
  }[event] as AgentBusLedgerState;
}

function normalizeIds(ids: readonly string[] | undefined): string[] {
  return [...new Set((ids ?? []).map(cleanId).filter((id): id is string => Boolean(id)))].sort();
}

function cleanId(value: string | undefined): string | undefined {
  const cleaned = value?.trim().slice(0, 160);
  return cleaned || undefined;
}

function normalizeScope(scope: readonly string[] | undefined): string[] {
  return [...new Set((scope ?? [])
    .map((entry) => entry.trim().replaceAll('\\', '/').replace(/^\.\//, ''))
    .filter((entry) => Boolean(entry) && !isUnsafePath(entry))
    .slice(0, 100))]
    .sort();
}

function normalizeTags(tags: readonly string[] | undefined): string[] {
  return [...new Set((tags ?? [])
    .map((entry) => entry.trim().toLowerCase().replace(/[^a-z0-9._-]/g, ''))
    .filter(Boolean)
    .slice(0, 32))]
    .sort();
}

function cleanRelativePath(value: string | undefined): string | undefined {
  const normalized = normalizeRelativePath(value ?? '');
  return normalized || undefined;
}

function normalizeRelativePath(value: string): string {
  const normalized = value.trim().replaceAll('\\', '/').replace(/^\.\//, '');
  return !normalized || isUnsafePath(normalized) ? '' : normalized.toLowerCase();
}

function isUnsafePath(value: string): boolean {
  return value.startsWith('/')
    || /^[a-z]:\//i.test(value)
    || value.split('/').some((part) => part === '..');
}

function globMatches(pattern: string, candidate: string): boolean {
  const escaped = pattern
    .toLowerCase()
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replaceAll('**', '\u0000')
    .replaceAll('*', '[^/]*')
    .replaceAll('\u0000', '.*');
  return new RegExp(`^${escaped}$`).test(candidate);
}

function boundedInteger(
  value: number | undefined,
  minimum: number,
  maximum: number,
): number | undefined {
  if (!Number.isInteger(value)) return undefined;
  return Math.max(minimum, Math.min(maximum, value!));
}
