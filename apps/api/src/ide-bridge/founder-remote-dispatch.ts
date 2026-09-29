import {
  createHash,
  randomBytes as nodeRandomBytes,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';

export const FOUNDER_REMOTE_PROVIDER = 'founder-ide-next';
export const FOUNDER_REMOTE_CAPABILITY_VERSION = 1;
export const FOUNDER_REMOTE_CAPABILITY = 'remote-build-v1';

export const FOUNDER_REMOTE_PENDING = 'REMOTE_PENDING';
export const FOUNDER_REMOTE_CLAIMED = 'REMOTE_CLAIMED';
export const FOUNDER_REMOTE_COMPLETE = 'REMOTE_COMPLETE';
export const FOUNDER_REMOTE_FAILED = 'REMOTE_FAILED';

export const FOUNDER_REMOTE_MAX_PROMPT_CHARS = 12_000;
export const FOUNDER_REMOTE_PENDING_TTL_MS = 15 * 60_000;
export const FOUNDER_REMOTE_CLAIM_TTL_MS = 2 * 60 * 60_000;

const MAX_RESULT_CHARS = 4_000;
const MAX_ERROR_CHARS = 4_000;
const CLAIM_TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,1024}\.[A-Za-z0-9_-]{20,128}$/;

export type FounderRemoteState =
  | typeof FOUNDER_REMOTE_PENDING
  | typeof FOUNDER_REMOTE_CLAIMED
  | typeof FOUNDER_REMOTE_COMPLETE
  | typeof FOUNDER_REMOTE_FAILED;

export class FounderRemoteDispatchError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = 'FounderRemoteDispatchError';
    this.code = code;
    this.status = status;
  }
}

export type FounderRemoteDispatchRow = {
  id: string;
  userId: string;
  sessionId: string;
  prompt: string;
  ideProvider: string;
  status: string;
  result: string | null;
  dispatchedAt: Date | null;
  createdAt: Date;
  targetNodeId: string | null;
  capabilityVersion: number | null;
  targetCapability: string | null;
  envelopeHash: string | null;
  claimTokenHash: string | null;
  claimedAt: Date | null;
  expiresAt: Date | null;
  error: string | null;
  /** Set by the owner when a claimed desktop run must stop. */
  cancelRequestedAt?: Date | null;
  cancelReason?: string | null;
};

export type FounderRemoteDispatchCreateData = Omit<FounderRemoteDispatchRow, 'createdAt'> & {
  createdAt?: Date;
};

export type FounderRemoteWhere = Record<string, unknown>;

export interface FounderRemoteDispatchRepository {
  create(args: { data: FounderRemoteDispatchCreateData }): Promise<FounderRemoteDispatchRow>;
  findFirst(args: { where: FounderRemoteWhere }): Promise<FounderRemoteDispatchRow | null>;
  findMany(args: {
    where: FounderRemoteWhere;
    orderBy?: FounderRemoteWhere;
    take?: number;
  }): Promise<FounderRemoteDispatchRow[]>;
  updateMany(args: {
    where: FounderRemoteWhere;
    data: Partial<FounderRemoteDispatchRow>;
  }): Promise<{ count: number }>;
}

export type FounderRemoteResolvedTarget = {
  sessionId: string;
  targetNodeId: string;
  ideProvider: string;
};

export type FounderRemoteNodeAssertion = {
  nodeId: string;
  ideProvider: string;
  capabilityVersion: number;
  capabilities: readonly string[];
};

export type FounderRemoteTargetResolver = (
  userId: string,
  sessionId: string,
  nodeId: string,
) => Promise<FounderRemoteResolvedTarget | null>;

export type FounderRemoteNodeAsserter = (
  userId: string,
  nodeId: string,
) => Promise<FounderRemoteNodeAssertion>;

export type FounderRemoteEnvelope = {
  id: string;
  sessionId: string;
  ideProvider: typeof FOUNDER_REMOTE_PROVIDER;
  targetNodeId: string;
  capabilityVersion: typeof FOUNDER_REMOTE_CAPABILITY_VERSION;
  targetCapability: typeof FOUNDER_REMOTE_CAPABILITY;
  prompt: string;
  status: 'pending';
};

export type FounderRemoteClaimBody = {
  targetNodeId: string;
  ideProvider: string;
  capabilityVersion: number;
  targetCapability: string;
};

export type FounderRemoteCompleteBody = {
  claimToken: string;
  result?: string;
  error?: string;
};

export type FounderRemoteClaim = FounderRemoteEnvelope & {
  claimToken: string;
  claimedAt: string;
  expiresAt: string;
};

export type FounderRemoteStatus = {
  id: string;
  status: string;
  executionStatus?: 'pending' | 'claimed' | 'cancellation_requested' | 'complete' | 'failed' | 'expired';
  result: string | null;
  error: string | null;
  dispatchedAt: string | null;
  createdAt: string;
  sessionId: string;
  delivered: false;
  failed: boolean;
  cancellationRequested?: boolean;
  cancellationReason?: string | null;
};

export type FounderRemoteCancelStatus = {
  id: string;
  cancellationRequested: boolean;
  reason: string | null;
};

export type FounderRemoteDispatchDependencies = {
  repository: FounderRemoteDispatchRepository;
  resolveTarget: FounderRemoteTargetResolver;
  assertNode: FounderRemoteNodeAsserter;
  now?: () => Date;
  idFactory?: () => string;
  randomBytes?: (size: number) => Uint8Array;
};

function cleanText(value: unknown, maxChars: number): { value: string; truncated: boolean } {
  const text = String(value ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
    .trim();
  return { value: text.slice(0, maxChars), truncated: text.length > maxChars };
}

function cleanIdentifier(value: unknown, maxChars = 200): string | null {
  const cleaned = cleanText(value, maxChars).value;
  if (!cleaned || /[\s/\\?#]/.test(cleaned)) return null;
  return cleaned;
}

function sha256(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

export function founderRemoteEnvelopeHash(envelope: {
  id: string;
  sessionId: string;
  ideProvider: string;
  targetNodeId: string;
  capabilityVersion: number;
  targetCapability: string;
  prompt: string;
}): string {
  return sha256(
    JSON.stringify([
      envelope.id,
      envelope.sessionId,
      envelope.ideProvider,
      envelope.targetNodeId,
      envelope.capabilityVersion,
      envelope.targetCapability,
      envelope.prompt,
    ]),
  );
}

function encodeBase64Url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString('base64url');
}

function safeHashEqual(left: string | null, right: string): boolean {
  if (!left || !/^[a-f0-9]{64}$/i.test(left) || !/^[a-f0-9]{64}$/i.test(right)) return false;
  return timingSafeEqual(Buffer.from(left, 'hex'), Buffer.from(right, 'hex'));
}

function executionStatus(status: string): FounderRemoteStatus['executionStatus'] {
  if (status === FOUNDER_REMOTE_PENDING) return 'pending';
  if (status === FOUNDER_REMOTE_CLAIMED) return 'claimed';
  if (status === FOUNDER_REMOTE_COMPLETE) return 'complete';
  if (status === FOUNDER_REMOTE_FAILED) return 'failed';
  return undefined;
}

export class FounderRemoteDispatchService {
  private readonly repository: FounderRemoteDispatchRepository;
  private readonly resolveTarget: FounderRemoteTargetResolver;
  private readonly assertNode: FounderRemoteNodeAsserter;
  private readonly now: () => Date;
  private readonly idFactory: () => string;
  private readonly randomBytes: (size: number) => Uint8Array;

  constructor({
    repository,
    resolveTarget,
    assertNode,
    now = () => new Date(),
    idFactory = randomUUID,
    randomBytes = nodeRandomBytes,
  }: FounderRemoteDispatchDependencies) {
    this.repository = repository;
    this.resolveTarget = resolveTarget;
    this.assertNode = assertNode;
    this.now = now;
    this.idFactory = idFactory;
    this.randomBytes = randomBytes;
  }

  async create(
    userIdValue: string,
    sessionIdValue: string,
    promptValue: string,
    targetNodeIdValue: string,
  ): Promise<FounderRemoteEnvelope> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const sessionId = this.requireIdentifier(sessionIdValue, 'session');
    const targetNodeId = this.requireIdentifier(targetNodeIdValue, 'node');
    const prompt = cleanText(promptValue, FOUNDER_REMOTE_MAX_PROMPT_CHARS);
    if (!prompt.value) throw this.error('Prompt is required.', 'INVALID_PROMPT', 400);
    if (prompt.truncated) {
      throw this.error(
        `Prompt exceeds the ${FOUNDER_REMOTE_MAX_PROMPT_CHARS}-character remote limit.`,
        'PROMPT_TOO_LARGE',
        413,
      );
    }

    const [target, node] = await Promise.all([
      this.resolveTarget(userId, sessionId, targetNodeId),
      this.assertNode(userId, targetNodeId),
    ]);
    this.assertTarget(target, sessionId, targetNodeId);
    this.assertCapableNode(node, targetNodeId);

    const id = this.requireIdentifier(this.idFactory(), 'dispatch');
    const createdAt = this.now();
    const expiresAt = new Date(createdAt.getTime() + FOUNDER_REMOTE_PENDING_TTL_MS);
    const immutable = {
      id,
      sessionId,
      ideProvider: FOUNDER_REMOTE_PROVIDER,
      targetNodeId,
      capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
      targetCapability: FOUNDER_REMOTE_CAPABILITY,
      prompt: prompt.value,
    } as const;
    const row = await this.repository.create({
      data: {
        ...immutable,
        userId,
        status: FOUNDER_REMOTE_PENDING,
        result: null,
        error: null,
        dispatchedAt: null,
        createdAt,
        envelopeHash: founderRemoteEnvelopeHash(immutable),
        claimTokenHash: null,
        claimedAt: null,
        expiresAt,
        cancelRequestedAt: null,
        cancelReason: null,
      },
    });
    return this.publicEnvelope(row);
  }

  async pending(userIdValue: string, nodeIdValue: string): Promise<FounderRemoteEnvelope[]> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const nodeId = this.requireIdentifier(nodeIdValue, 'node');
    const node = await this.assertNode(userId, nodeId);
    this.assertCapableNode(node, nodeId);
    const rows = await this.repository.findMany({
      where: {
        userId,
        targetNodeId: nodeId,
        status: FOUNDER_REMOTE_PENDING,
        ideProvider: FOUNDER_REMOTE_PROVIDER,
        capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
        targetCapability: FOUNDER_REMOTE_CAPABILITY,
        expiresAt: { gt: this.now() },
      },
      orderBy: { createdAt: 'asc' },
      take: 10,
    });
    return rows.filter((row) => this.hasIntactEnvelope(row)).map((row) => this.publicEnvelope(row));
  }

  async claim(
    userIdValue: string,
    nodeIdValue: string,
    idValue: string,
    body: unknown,
  ): Promise<FounderRemoteClaim> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const nodeId = this.requireIdentifier(nodeIdValue, 'node');
    const id = this.requireIdentifier(idValue, 'dispatch');
    this.assertClaimBody(body, nodeId);

    const node = await this.assertNode(userId, nodeId);
    this.assertCapableNode(node, nodeId);
    const row = await this.repository.findFirst({
      where: {
        id,
        userId,
        targetNodeId: nodeId,
        status: FOUNDER_REMOTE_PENDING,
        expiresAt: { gt: this.now() },
      },
    });
    if (!row || !this.hasIntactEnvelope(row)) throw this.conflict();

    const target = await this.resolveTarget(userId, row.sessionId, nodeId);
    this.assertTarget(target, row.sessionId, nodeId);

    const claimToken = `${encodeBase64Url(this.randomBytes(32))}.${encodeBase64Url(this.randomBytes(24))}`;
    if (!CLAIM_TOKEN_PATTERN.test(claimToken)) {
      throw this.error('Could not create a valid execution claim.', 'CLAIM_TOKEN_FAILURE', 500);
    }
    const claimTokenHash = sha256(claimToken);
    const claimedAt = this.now();
    if (!row.expiresAt || row.expiresAt.getTime() <= claimedAt.getTime()) throw this.conflict();
    const expiresAt = new Date(claimedAt.getTime() + FOUNDER_REMOTE_CLAIM_TTL_MS);
    const updated = await this.repository.updateMany({
      where: {
        id: row.id,
        userId: row.userId,
        sessionId: row.sessionId,
        prompt: row.prompt,
        ideProvider: row.ideProvider,
        targetNodeId: row.targetNodeId,
        capabilityVersion: row.capabilityVersion,
        targetCapability: row.targetCapability,
        envelopeHash: row.envelopeHash,
        status: FOUNDER_REMOTE_PENDING,
        claimTokenHash: null,
        claimedAt: null,
        expiresAt: row.expiresAt,
      },
      data: {
        status: FOUNDER_REMOTE_CLAIMED,
        claimTokenHash,
        claimedAt,
        expiresAt,
      },
    });
    if (updated.count !== 1) throw this.conflict();

    return {
      ...this.publicEnvelope(row),
      claimToken,
      claimedAt: claimedAt.toISOString(),
      expiresAt: expiresAt.toISOString(),
    };
  }

  async complete(
    userIdValue: string,
    nodeIdValue: string,
    idValue: string,
    body: unknown,
  ): Promise<FounderRemoteStatus> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const nodeId = this.requireIdentifier(nodeIdValue, 'node');
    const id = this.requireIdentifier(idValue, 'dispatch');
    const payload = this.readCompleteBody(body);
    const claimToken = payload.claimToken;
    if (!CLAIM_TOKEN_PATTERN.test(claimToken)) throw this.invalidClaim();
    const completion = this.cleanCompletion(payload);

    const node = await this.assertNode(userId, nodeId);
    this.assertCapableNode(node, nodeId);
    const row = await this.repository.findFirst({
      where: {
        id,
        userId,
        targetNodeId: nodeId,
        status: FOUNDER_REMOTE_CLAIMED,
        expiresAt: { gt: this.now() },
      },
    });
    const claimTokenHash = sha256(claimToken);
    const completedAt = this.now();
    if (
      !row ||
      !row.expiresAt ||
      row.expiresAt.getTime() <= completedAt.getTime() ||
      !this.hasIntactEnvelope(row) ||
      !safeHashEqual(row.claimTokenHash, claimTokenHash)
    ) {
      throw this.invalidClaim();
    }

    const cancellationRequested = Boolean(row.cancelRequestedAt);
    const nextStatus = cancellationRequested || completion.error
      ? FOUNDER_REMOTE_FAILED
      : FOUNDER_REMOTE_COMPLETE;
    const completionError = cancellationRequested
      ? `Cancelled by the Founder owner${row.cancelReason ? `: ${row.cancelReason}` : '.'}`
      : completion.error;
    const updated = await this.repository.updateMany({
      where: {
        id: row.id,
        userId: row.userId,
        sessionId: row.sessionId,
        prompt: row.prompt,
        ideProvider: row.ideProvider,
        targetNodeId: row.targetNodeId,
        capabilityVersion: row.capabilityVersion,
        targetCapability: row.targetCapability,
        envelopeHash: row.envelopeHash,
        claimTokenHash: row.claimTokenHash,
        claimedAt: row.claimedAt,
        expiresAt: row.expiresAt,
        status: FOUNDER_REMOTE_CLAIMED,
      },
      data: {
        status: nextStatus,
        result: completion.result,
        error: completionError,
        dispatchedAt: completedAt,
      },
    });
    if (updated.count !== 1) throw this.invalidClaim();

    return this.status(userId, id);
  }

  /** Owner-side cancellation request. A claimed run remains claimed until the
   * desktop acknowledges the abort with a terminal completion receipt. */
  async cancel(userIdValue: string, idValue: string, reasonValue?: unknown): Promise<FounderRemoteStatus> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const id = this.requireIdentifier(idValue, 'dispatch');
    const reason = cleanText(reasonValue ?? 'Cancelled by the Founder owner.', 500).value || 'Cancelled by the Founder owner.';
    const row = await this.repository.findFirst({ where: { id, userId } });
    if (!row) throw this.error('Remote dispatch was not found.', 'NOT_FOUND', 404);
    if (row.status === FOUNDER_REMOTE_PENDING) {
      const updated = await this.repository.updateMany({
        where: { id, userId, status: FOUNDER_REMOTE_PENDING },
        data: {
          status: FOUNDER_REMOTE_FAILED,
          error: `Cancelled before the desktop claimed the request: ${reason}`,
          dispatchedAt: this.now(),
          cancelRequestedAt: this.now(),
          cancelReason: reason,
        },
      });
      if (updated.count !== 1) return this.status(userId, id);
      return this.status(userId, id);
    }
    if (row.status === FOUNDER_REMOTE_CLAIMED && !row.cancelRequestedAt) {
      const updated = await this.repository.updateMany({
        where: {
          id,
          userId,
          targetNodeId: row.targetNodeId,
          status: FOUNDER_REMOTE_CLAIMED,
          claimTokenHash: row.claimTokenHash,
          claimedAt: row.claimedAt,
          expiresAt: row.expiresAt,
          cancelRequestedAt: null,
        },
        data: { cancelRequestedAt: this.now(), cancelReason: reason },
      });
      if (updated.count === 1) return this.status(userId, id);
    }
    return this.status(userId, id);
  }

  /** Node-side, claim-token-bound cancellation poll. */
  async cancelStatus(
    userIdValue: string,
    nodeIdValue: string,
    idValue: string,
    body: unknown,
  ): Promise<FounderRemoteCancelStatus> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const nodeId = this.requireIdentifier(nodeIdValue, 'node');
    const id = this.requireIdentifier(idValue, 'dispatch');
    const payload = this.readCompleteBody(body);
    if (!CLAIM_TOKEN_PATTERN.test(payload.claimToken)) throw this.invalidClaim();
    const node = await this.assertNode(userId, nodeId);
    this.assertCapableNode(node, nodeId);
    const row = await this.repository.findFirst({
      where: { id, userId, targetNodeId: nodeId, status: FOUNDER_REMOTE_CLAIMED, expiresAt: { gt: this.now() } },
    });
    if (!row || !this.hasIntactEnvelope(row) || !safeHashEqual(row.claimTokenHash, sha256(payload.claimToken))) {
      throw this.invalidClaim();
    }
    return {
      id,
      cancellationRequested: Boolean(row.cancelRequestedAt),
      reason: row.cancelReason ?? null,
    };
  }

  async status(userIdValue: string, idValue: string): Promise<FounderRemoteStatus> {
    const userId = this.requireIdentifier(userIdValue, 'user');
    const id = this.requireIdentifier(idValue, 'dispatch');
    const row = await this.repository.findFirst({ where: { id, userId } });
    if (!row) throw this.error('Remote dispatch was not found.', 'NOT_FOUND', 404);
    const expired = (row.status === FOUNDER_REMOTE_PENDING || row.status === FOUNDER_REMOTE_CLAIMED) &&
      (!row.expiresAt || row.expiresAt.getTime() <= this.now().getTime());
    const cancellationRequested = Boolean(row.cancelRequestedAt) && row.status === FOUNDER_REMOTE_CLAIMED;
    return {
      id: row.id,
      status: expired ? 'REMOTE_EXPIRED' : row.status,
      executionStatus: expired ? 'expired' : cancellationRequested ? 'cancellation_requested' : executionStatus(row.status),
      result: expired ? null : row.result,
      error: expired ? (row.status === FOUNDER_REMOTE_PENDING
        ? 'Request expired before a desktop claim. No retry was dispatched.'
        : 'Execution claim expired without a terminal receipt; final outcome is unknown. No retry was dispatched.') : row.error,
      dispatchedAt: row.dispatchedAt?.toISOString() ?? null,
      createdAt: row.createdAt.toISOString(),
      sessionId: row.sessionId,
      delivered: false,
      failed: expired || row.status === FOUNDER_REMOTE_FAILED,
      cancellationRequested,
      cancellationReason: row.cancelReason ?? null,
    };
  }

  private publicEnvelope(row: FounderRemoteDispatchRow): FounderRemoteEnvelope {
    if (!this.hasIntactEnvelope(row)) throw this.conflict();
    return {
      id: row.id,
      sessionId: row.sessionId,
      ideProvider: FOUNDER_REMOTE_PROVIDER,
      targetNodeId: row.targetNodeId,
      capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
      targetCapability: FOUNDER_REMOTE_CAPABILITY,
      prompt: row.prompt,
      status: 'pending',
    };
  }

  private hasIntactEnvelope(row: FounderRemoteDispatchRow): row is FounderRemoteDispatchRow & {
    targetNodeId: string;
    capabilityVersion: typeof FOUNDER_REMOTE_CAPABILITY_VERSION;
    targetCapability: typeof FOUNDER_REMOTE_CAPABILITY;
    envelopeHash: string;
  } {
    if (
      row.ideProvider !== FOUNDER_REMOTE_PROVIDER ||
      row.capabilityVersion !== FOUNDER_REMOTE_CAPABILITY_VERSION ||
      row.targetCapability !== FOUNDER_REMOTE_CAPABILITY ||
      !row.targetNodeId ||
      !row.envelopeHash
    ) return false;
    return safeHashEqual(
      row.envelopeHash,
      founderRemoteEnvelopeHash({
        id: row.id,
        sessionId: row.sessionId,
        ideProvider: row.ideProvider,
        targetNodeId: row.targetNodeId,
        capabilityVersion: row.capabilityVersion,
        targetCapability: row.targetCapability,
        prompt: row.prompt,
      }),
    );
  }

  private assertClaimBody(body: unknown, nodeId: string): asserts body is FounderRemoteClaimBody {
    if (
      !body ||
      typeof body !== 'object' ||
      !('targetNodeId' in body) ||
      !('ideProvider' in body) ||
      !('capabilityVersion' in body) ||
      !('targetCapability' in body) ||
      body.targetNodeId !== nodeId ||
      body.ideProvider !== FOUNDER_REMOTE_PROVIDER ||
      body.capabilityVersion !== FOUNDER_REMOTE_CAPABILITY_VERSION ||
      body.targetCapability !== FOUNDER_REMOTE_CAPABILITY
    ) throw this.error('Remote claim does not match this node capability.', 'INVALID_CLAIM', 400);
  }

  private assertTarget(
    target: FounderRemoteResolvedTarget | null,
    sessionId: string,
    nodeId: string,
  ): asserts target is FounderRemoteResolvedTarget {
    if (
      !target ||
      target.sessionId !== sessionId ||
      target.targetNodeId !== nodeId ||
      target.ideProvider !== FOUNDER_REMOTE_PROVIDER
    ) throw this.error('Remote target is not available.', 'TARGET_NOT_FOUND', 404);
  }

  private assertCapableNode(node: FounderRemoteNodeAssertion, nodeId: string): void {
    if (
      node.nodeId !== nodeId ||
      node.ideProvider !== FOUNDER_REMOTE_PROVIDER ||
      node.capabilityVersion !== FOUNDER_REMOTE_CAPABILITY_VERSION ||
      !node.capabilities.includes(FOUNDER_REMOTE_CAPABILITY)
    ) throw this.error('Founder Node does not support remote builds.', 'CAPABILITY_REQUIRED', 409);
  }

  private cleanCompletion(body: FounderRemoteCompleteBody): { result: string | null; error: string | null } {
    const result = typeof body?.result === 'string' ? cleanText(body.result, MAX_RESULT_CHARS) : null;
    const error = typeof body?.error === 'string' ? cleanText(body.error, MAX_ERROR_CHARS) : null;
    if ((result?.truncated ?? false) || (error?.truncated ?? false)) {
      throw this.error('Completion detail exceeds the remote limit.', 'COMPLETION_TOO_LARGE', 413);
    }
    const resultValue = result?.value || null;
    const errorValue = error?.value || null;
    if ((!resultValue && !errorValue) || (resultValue && errorValue)) {
      throw this.error('Completion requires either result or error.', 'INVALID_COMPLETION', 400);
    }
    return { result: resultValue, error: errorValue };
  }

  private readCompleteBody(body: unknown): FounderRemoteCompleteBody {
    if (!body || typeof body !== 'object' || !('claimToken' in body)) {
      throw this.invalidClaim();
    }
    const value = body as Record<string, unknown>;
    return {
      claimToken: typeof value.claimToken === 'string' ? value.claimToken.trim() : '',
      ...(typeof value.result === 'string' ? { result: value.result } : {}),
      ...(typeof value.error === 'string' ? { error: value.error } : {}),
    };
  }

  private requireIdentifier(value: unknown, label: string): string {
    const identifier = cleanIdentifier(value);
    if (!identifier) throw this.error(`Invalid ${label} identifier.`, 'INVALID_IDENTIFIER', 400);
    return identifier;
  }

  private conflict(): FounderRemoteDispatchError {
    return this.error('Remote dispatch is no longer available to claim.', 'CLAIM_CONFLICT', 409);
  }

  private invalidClaim(): FounderRemoteDispatchError {
    return this.error('Execution claim is invalid or no longer active.', 'INVALID_CLAIM', 409);
  }

  private error(message: string, code: string, status: number): FounderRemoteDispatchError {
    return new FounderRemoteDispatchError(message, code, status);
  }
}
