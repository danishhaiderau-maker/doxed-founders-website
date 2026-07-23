export interface DailyReviewActivityRecord {
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'reused';
  editedFiles: string[];
}

export interface DailyReviewAwarenessSummary {
  activeCount: number;
  tasks: Array<{ title: string }>;
}

export interface DailyReviewTaskCandidate {
  name: string;
  group?: string;
  isDefault?: boolean;
}

const REVIEW_INTERVAL_MS = 24 * 60 * 60 * 1_000;

export function shouldRunDailyQualityReview(
  enabled: boolean,
  lastCompletedAt: string | undefined,
  now = Date.now(),
): boolean {
  if (!enabled) return false;
  const completedAt = lastCompletedAt ? Date.parse(lastCompletedAt) : Number.NaN;
  return !Number.isFinite(completedAt) || now - completedAt >= REVIEW_INTERVAL_MS;
}

export function reviewOwnedFiles(
  records: readonly DailyReviewActivityRecord[],
): string[] {
  return [...new Set(
    records
      .filter((record) => record.status !== 'cancelled')
      .flatMap((record) => record.editedFiles)
      .map(normalizeRelativePath)
      .filter(Boolean),
  )].sort();
}

export function activeCoordinationReason(
  summary: DailyReviewAwarenessSummary,
): string | null {
  if (summary.activeCount === 0) return null;
  const named = summary.tasks
    .slice(0, 3)
    .map((task) => task.title)
    .filter(Boolean)
    .join('; ');
  return `${summary.activeCount} active Founder task${summary.activeCount === 1 ? '' : 's'}`
    + `${named ? ` (${named})` : ''}. Daily QA deferred to avoid touching shared build output.`;
}

export function selectDailyReviewTaskNames(
  tasks: readonly DailyReviewTaskCandidate[],
  configuredLabels: readonly string[],
): string[] {
  const requested = new Set(configuredLabels.map((label) => label.trim()).filter(Boolean));
  const selected = requested.size > 0
    ? tasks.filter((task) => requested.has(task.name))
    : tasks.filter((task) =>
      task.isDefault === true
      && (task.group?.toLowerCase() === 'build' || task.group?.toLowerCase() === 'test'));
  return [...new Set(selected.map((task) => task.name))].slice(0, 4);
}

export function normalizeDailyReviewProbeUrl(value: string): string | null {
  try {
    const url = new URL(value.trim());
    const safeProtocol = url.protocol === 'https:'
      || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '::1'].includes(url.hostname));
    if (!safeProtocol || url.username || url.password || url.search || url.hash) return null;
    return url.toString();
  } catch {
    return null;
  }
}

function normalizeRelativePath(value: string): string {
  return value.trim().replace(/^"+|"+$/g, '').replaceAll('\\', '/');
}
