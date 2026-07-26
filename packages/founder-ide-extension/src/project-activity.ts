import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type { FounderCompletionEvidenceReceipt } from './completion-evidence';

const SCHEMA_VERSION = 1 as const;
const MAX_RECORDS = 300;
const SECRET_PATTERN = /(?:api[_-]?key|authorization|bearer|private[_-]?key|node[_-]?token|secret)\s*[:=]/i;

export type FounderProjectActivityStatus = 'running' | 'completed' | 'failed' | 'cancelled' | 'reused';

export interface FounderProjectActivityRecord {
  id: string;
  workspaceId: string;
  startedAt: string;
  completedAt: string | null;
  goal: string;
  model: string;
  status: FounderProjectActivityStatus;
  summary: string;
  provider: string | null;
  providerModel: string | null;
  editedFiles: string[];
  checks: string[];
  verification: FounderCompletionEvidenceReceipt | null;
  estimatedTokensAvoided: number;
}

interface FounderProjectActivityState {
  version: typeof SCHEMA_VERSION;
  records: FounderProjectActivityRecord[];
}

export interface CompleteFounderProjectActivity {
  status: Exclude<FounderProjectActivityStatus, 'running'>;
  summary?: string;
  provider?: string | null;
  providerModel?: string | null;
  editedFiles?: string[];
  checks?: string[];
  verification?: FounderCompletionEvidenceReceipt | null;
  estimatedTokensAvoided?: number;
  completedAt?: string;
}

export class FounderProjectActivityStore {
  private state: FounderProjectActivityState;

  constructor(private readonly file: string) {
    this.state = readState(file);
  }

  begin(workspaceId: string, goal: string, model: string, startedAt = new Date().toISOString()): string | null {
    const cleanGoal = compactText(goal, 1_000);
    if (!workspaceId || !cleanGoal || SECRET_PATTERN.test(cleanGoal)) return null;
    const id = randomUUID();
    this.state.records.unshift({
      id,
      workspaceId,
      startedAt,
      completedAt: null,
      goal: cleanGoal,
      model: compactText(model, 120),
      status: 'running',
      summary: '',
      provider: null,
      providerModel: null,
      editedFiles: [],
      checks: [],
      verification: null,
      estimatedTokensAvoided: 0,
    });
    this.trimAndWrite();
    return id;
  }

  complete(id: string | null, input: CompleteFounderProjectActivity): boolean {
    if (!id) return false;
    const record = this.state.records.find((candidate) => candidate.id === id);
    if (!record) return false;
    const rawSummary = compactText(input.summary ?? '', 2_000);
    const summary = SECRET_PATTERN.test(rawSummary)
      ? 'Details withheld because the result may contain a credential.'
      : rawSummary;
    record.status = input.status;
    record.completedAt = input.completedAt ?? new Date().toISOString();
    record.summary = summary;
    record.provider = cleanOptional(input.provider, 80);
    record.providerModel = cleanOptional(input.providerModel, 160);
    record.editedFiles = compactList(input.editedFiles, 30, 260);
    record.checks = compactList(input.checks, 20, 300);
    record.verification = sanitizeVerification(input.verification);
    record.estimatedTokensAvoided = boundedInteger(input.estimatedTokensAvoided);
    this.trimAndWrite();
    return true;
  }

  recordsFor(workspaceId: string, since?: Date): FounderProjectActivityRecord[] {
    const cutoff = since?.getTime() ?? 0;
    return this.state.records
      .filter((record) => record.workspaceId === workspaceId)
      .filter((record) => Date.parse(record.startedAt) >= cutoff)
      .map((record) => ({ ...record, editedFiles: [...record.editedFiles], checks: [...record.checks] }));
  }

  dailyBrief(workspaceId: string, workspaceName: string, now = new Date()): string {
    const since = new Date(now.getTime() - 24 * 60 * 60 * 1_000);
    const recent = this.recordsFor(workspaceId, since);
    const completed = recent.filter((record) => record.status === 'completed' || record.status === 'reused');
    const unresolved = recent.filter((record) => record.status === 'failed' || record.status === 'running');
    const files = [...new Set(completed.flatMap((record) => record.editedFiles))].sort();
    const checks = [...new Set(completed.flatMap((record) => record.checks))].sort();
    const passedReceipts = completed.filter((record) => record.verification?.verdict === 'passed').length;
    const incompleteReceipts = recent.filter((record) => record.verification?.verdict === 'incomplete').length;
    const avoided = recent.reduce((total, record) => total + record.estimatedTokensAvoided, 0);
    const lines = [
      `# ${workspaceName} - Founder project brief`,
      '',
      `Generated ${now.toISOString()} from local activity recorded during the last 24 hours.`,
      '',
      '## Completed',
      ...(completed.length > 0
        ? completed.map((record) => briefLine(record))
        : ['- No completed Founder tasks recorded.']),
      '',
      '## Needs attention',
      ...(unresolved.length > 0
        ? unresolved.map((record) => briefLine(record))
        : ['- No failed or unfinished Founder tasks recorded.']),
      '',
      '## Evidence',
      `- Verified checks: ${checks.length > 0 ? checks.join('; ') : 'None recorded'}`,
      `- Completion receipts: ${passedReceipts} passed; ${incompleteReceipts} incomplete`,
      `- Changed files: ${files.length > 0 ? files.join(', ') : 'None recorded'}`,
      `- Estimated tokens avoided: ${avoided.toLocaleString('en-US')}`,
      '',
      '## Resume',
      ...(unresolved.length > 0
        ? unresolved.slice(0, 5).map((record) => `- Continue: ${record.goal}`)
        : ['- Start the next founder-defined goal.']),
      '',
      '_This brief is evidence, not a claim of release readiness. Run the project checks before shipping._',
    ];
    return lines.join('\n');
  }

  private trimAndWrite(): void {
    this.state.records = this.state.records.slice(0, MAX_RECORDS);
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      const temp = `${this.file}.${process.pid}.tmp`;
      fs.writeFileSync(temp, `${JSON.stringify(this.state, null, 2)}\n`, 'utf8');
      fs.renameSync(temp, this.file);
    } catch {
      // Continuity is useful but must never block the founder's active task.
    }
  }
}

export function workspaceActivityId(roots: readonly string[]): string | null {
  const normalized = roots.map((root) => path.resolve(root).toLowerCase()).sort();
  if (normalized.length === 0) return null;
  return createHash('sha256').update(normalized.join('\n')).digest('hex').slice(0, 24);
}

function readState(file: string): FounderProjectActivityState {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8')) as Partial<FounderProjectActivityState>;
    if (value.version !== SCHEMA_VERSION || !Array.isArray(value.records)) throw new Error('schema mismatch');
    return {
      version: SCHEMA_VERSION,
      records: value.records
        .filter(isRecord)
        .map((record) => ({
          ...record,
          verification: sanitizeVerification(record.verification),
        }))
        .slice(0, MAX_RECORDS),
    };
  } catch {
    return { version: SCHEMA_VERSION, records: [] };
  }
}

function isRecord(value: unknown): value is FounderProjectActivityRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Partial<FounderProjectActivityRecord>;
  return typeof record.id === 'string'
    && typeof record.workspaceId === 'string'
    && typeof record.startedAt === 'string'
    && (record.completedAt === null || typeof record.completedAt === 'string')
    && typeof record.goal === 'string'
    && typeof record.model === 'string'
    && ['running', 'completed', 'failed', 'cancelled', 'reused'].includes(record.status ?? '')
    && typeof record.summary === 'string'
    && Array.isArray(record.editedFiles)
    && Array.isArray(record.checks)
    && (
      record.verification === undefined
      || record.verification === null
      || typeof record.verification === 'object'
    )
    && Number.isFinite(record.estimatedTokensAvoided);
}

function compactText(value: string, max: number): string {
  return value.replace(/\s+/g, ' ').trim().slice(0, max);
}

function cleanOptional(value: string | null | undefined, max: number): string | null {
  if (!value) return null;
  const clean = compactText(value, max);
  return clean && !SECRET_PATTERN.test(clean) ? clean : null;
}

function compactList(values: string[] | undefined, maxItems: number, maxLength: number): string[] {
  return [...new Set((values ?? []).map((value) => compactText(value, maxLength)).filter(Boolean))]
    .filter((value) => !SECRET_PATTERN.test(value))
    .slice(0, maxItems);
}

function boundedInteger(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Math.min(1_000_000_000, Math.floor(value))
    : 0;
}

function sanitizeVerification(
  value: FounderCompletionEvidenceReceipt | null | undefined,
): FounderCompletionEvidenceReceipt | null {
  if (!value || typeof value !== 'object') return null;
  if (value.version !== 1 || (value.verdict !== 'passed' && value.verdict !== 'incomplete')) {
    return null;
  }
  if (
    (value.scope !== 'read_only' && value.scope !== 'workspace_change')
    || !['ask', 'plan', 'build', 'debug', 'team'].includes(value.mode)
  ) {
    return null;
  }
  return {
    version: 1,
    verdict: value.verdict,
    scope: value.scope,
    mode: value.mode,
    editedFileCount: boundedInteger(value.editedFileCount),
    passedCheckCount: boundedInteger(value.passedCheckCount),
    visualCheckCount: boundedInteger(value.visualCheckCount),
    requirements: compactList([...value.requirements], 20, 100),
    missing: compactList([...value.missing], 20, 240),
  };
}

function briefLine(record: FounderProjectActivityRecord): string {
  const route = record.provider && record.providerModel
    ? ` (${record.provider}/${record.providerModel})`
    : '';
  const summary = record.summary ? ` - ${record.summary}` : '';
  return `- **${record.status}** ${record.goal}${route}${summary}`;
}
