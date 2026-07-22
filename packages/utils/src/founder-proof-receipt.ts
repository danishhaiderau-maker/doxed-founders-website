export const FOUNDER_PROOF_RECEIPT_VERSION = 1 as const;

export type FounderProofOutcome =
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'blocked';

export interface FounderChangedFileProof {
  path: string;
  beforeSha256: string | null;
  afterSha256: string | null;
  additions: number;
  deletions: number;
}

export interface FounderTestProof {
  command: string;
  status: 'passed' | 'failed' | 'skipped';
  exitCode: number | null;
  durationMs: number;
  provenance: 'pre_existing' | 'agent_written' | 'manual';
  summary: string;
}

export interface FounderCommandProof {
  command: string;
  exitCode: number | null;
  durationMs: number;
  outputSha256: string;
  outputPreview: string;
}

export interface FounderCostProof {
  weightsVersion: 'founder-wtu-v1';
  weightedUnits: number;
  rawInputTokens: number;
  rawCachedInputTokens: number;
  rawOutputTokens: number;
  rawReasoningTokens: number;
  providerCostUsd: number | null;
  billingSource: 'platform_managed' | 'personal_byok' | 'local';
}

export interface FounderFailureProof {
  stage: string;
  code: string;
  message: string;
  retryable: boolean;
}

/** Structured evidence returned by local, IDE, and remote Founder tasks. */
export interface FounderProofReceipt {
  version: typeof FOUNDER_PROOF_RECEIPT_VERSION;
  receiptId: string;
  taskId: string;
  workspaceId: string;
  agentId: string;
  startedAt: string;
  finishedAt: string;
  outcome: FounderProofOutcome;
  summary: string;
  changedFiles: FounderChangedFileProof[];
  tests: FounderTestProof[];
  commands: FounderCommandProof[];
  cost: FounderCostProof;
  failures: FounderFailureProof[];
  previousReceiptSha256: string | null;
  receiptSha256: string;
}

const SHA256_PATTERN = /^[a-f0-9]{64}$/i;

export function validateFounderProofReceipt(
  receipt: FounderProofReceipt,
): string[] {
  const errors: string[] = [];
  if (receipt.version !== FOUNDER_PROOF_RECEIPT_VERSION) {
    errors.push('unsupported receipt version');
  }
  for (const [field, value] of [
    ['receiptId', receipt.receiptId],
    ['taskId', receipt.taskId],
    ['workspaceId', receipt.workspaceId],
    ['agentId', receipt.agentId],
    ['summary', receipt.summary],
  ] as const) {
    if (!value.trim()) errors.push(`${field} is required`);
  }
  if (Number.isNaN(Date.parse(receipt.startedAt))) {
    errors.push('startedAt must be an ISO timestamp');
  }
  if (Number.isNaN(Date.parse(receipt.finishedAt))) {
    errors.push('finishedAt must be an ISO timestamp');
  }
  if (Date.parse(receipt.finishedAt) < Date.parse(receipt.startedAt)) {
    errors.push('finishedAt must not precede startedAt');
  }
  if (!SHA256_PATTERN.test(receipt.receiptSha256)) {
    errors.push('receiptSha256 must be a SHA-256 digest');
  }
  if (
    receipt.previousReceiptSha256 !== null &&
    !SHA256_PATTERN.test(receipt.previousReceiptSha256)
  ) {
    errors.push('previousReceiptSha256 must be null or a SHA-256 digest');
  }
  for (const file of receipt.changedFiles) {
    if (!file.path.trim()) errors.push('changed file path is required');
    if (file.beforeSha256 !== null && !SHA256_PATTERN.test(file.beforeSha256)) {
      errors.push(`invalid beforeSha256 for ${file.path}`);
    }
    if (file.afterSha256 !== null && !SHA256_PATTERN.test(file.afterSha256)) {
      errors.push(`invalid afterSha256 for ${file.path}`);
    }
  }
  if (receipt.outcome === 'completed') {
    if (receipt.failures.length > 0) {
      errors.push('completed receipts cannot contain failures');
    }
    if (receipt.tests.some((test) => test.status === 'failed')) {
      errors.push('completed receipts cannot contain failed tests');
    }
  }
  return errors;
}
