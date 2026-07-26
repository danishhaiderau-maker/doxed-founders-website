export type FounderProjectKind = 'folder' | 'workspace';

export interface FounderProjectRecord {
  id: string;
  name: string;
  nameSource: 'automatic' | 'custom';
  uri: string;
  kind: FounderProjectKind;
  lastOpenedAt: string;
  pinnedAt: string | null;
  archivedAt: string | null;
}

export interface FounderProjectCandidate {
  id: string;
  name: string;
  uri: string;
  kind: FounderProjectKind;
  openedAt: string;
}

const MAX_PROJECTS = 80;

export function parseFounderProjectHistory(value: unknown): FounderProjectRecord[] {
  if (!Array.isArray(value)) return [];
  const records: FounderProjectRecord[] = [];
  const seen = new Set<string>();
  for (const candidate of value) {
    if (!isRecord(candidate)) continue;
    const id = clean(candidate.id, 96);
    const uri = clean(candidate.uri, 2_000);
    const name = clean(candidate.name, 120);
    const kind = candidate.kind === 'workspace' ? 'workspace' : 'folder';
    const lastOpenedAt = isoDate(candidate.lastOpenedAt);
    if (!id || !uri || !name || !lastOpenedAt || seen.has(id)) continue;
    seen.add(id);
    records.push({
      id,
      uri,
      name,
      kind,
      lastOpenedAt,
      nameSource: candidate.nameSource === 'custom' ? 'custom' : 'automatic',
      pinnedAt: nullableIsoDate(candidate.pinnedAt),
      archivedAt: nullableIsoDate(candidate.archivedAt),
    });
  }
  return sortFounderProjects(records).slice(0, MAX_PROJECTS);
}

export function recordFounderProject(
  existing: readonly FounderProjectRecord[],
  candidate: FounderProjectCandidate,
): FounderProjectRecord[] {
  const current = existing.find((record) => record.id === candidate.id);
  const next: FounderProjectRecord = {
    id: candidate.id,
    name: current?.nameSource === 'custom' ? current.name : candidate.name,
    nameSource: current?.nameSource === 'custom' ? 'custom' : 'automatic',
    uri: candidate.uri,
    kind: candidate.kind,
    lastOpenedAt: candidate.openedAt,
    pinnedAt: current?.pinnedAt ?? null,
    archivedAt: null,
  };
  return sortFounderProjects([
    next,
    ...existing.filter((record) => record.id !== candidate.id),
  ]).slice(0, MAX_PROJECTS);
}

export function renameFounderProject(
  existing: readonly FounderProjectRecord[],
  id: string,
  name: string,
): FounderProjectRecord[] {
  const safeName = clean(name, 120);
  if (!safeName) return [...existing];
  return existing.map((record) =>
    record.id === id
      ? { ...record, name: safeName, nameSource: 'custom' }
      : record);
}

export function setFounderProjectPinned(
  existing: readonly FounderProjectRecord[],
  id: string,
  pinned: boolean,
  changedAt: string,
): FounderProjectRecord[] {
  return sortFounderProjects(existing.map((record) =>
    record.id === id
      ? { ...record, pinnedAt: pinned ? changedAt : null }
      : record));
}

export function setFounderProjectArchived(
  existing: readonly FounderProjectRecord[],
  id: string,
  archived: boolean,
  changedAt: string,
): FounderProjectRecord[] {
  return sortFounderProjects(existing.map((record) =>
    record.id === id
      ? {
          ...record,
          archivedAt: archived ? changedAt : null,
          pinnedAt: archived ? null : record.pinnedAt,
        }
      : record));
}

export function visibleFounderProjects(
  existing: readonly FounderProjectRecord[],
  showArchived: boolean,
): FounderProjectRecord[] {
  return sortFounderProjects(existing.filter((record) =>
    showArchived ? record.archivedAt !== null : record.archivedAt === null));
}

export function sortFounderProjects(
  existing: readonly FounderProjectRecord[],
): FounderProjectRecord[] {
  return [...existing].sort((left, right) => {
    const leftArchived = left.archivedAt ? 1 : 0;
    const rightArchived = right.archivedAt ? 1 : 0;
    if (leftArchived !== rightArchived) return leftArchived - rightArchived;
    const leftPinned = left.pinnedAt ? 0 : 1;
    const rightPinned = right.pinnedAt ? 0 : 1;
    if (leftPinned !== rightPinned) return leftPinned - rightPinned;
    if (left.pinnedAt && right.pinnedAt && left.pinnedAt !== right.pinnedAt) {
      return right.pinnedAt.localeCompare(left.pinnedAt);
    }
    return right.lastOpenedAt.localeCompare(left.lastOpenedAt);
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function clean(value: unknown, limit: number): string {
  return typeof value === 'string'
    ? value.replace(/\s+/g, ' ').trim().slice(0, limit)
    : '';
}

function nullableIsoDate(value: unknown): string | null {
  return value === null || value === undefined ? null : isoDate(value);
}

function isoDate(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return '';
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : '';
}
