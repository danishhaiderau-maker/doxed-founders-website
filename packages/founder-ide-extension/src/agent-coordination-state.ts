export const AGENT_PRESENCE_TTL_MS = 3 * 60_000;

export interface FounderAgentPresence {
  version: 1;
  id: string;
  workspacePath: string;
  workspaceName: string;
  branch?: string;
  title: string;
  provider: string;
  status: 'working' | 'waiting';
  ownedFiles: string[];
  startedAt: string;
  heartbeatAt: string;
}

export interface FounderAgentRisk {
  peer: FounderAgentPresence;
  reason: string;
  overlappingFiles: string[];
  intentSimilarity: number;
}

const STOP_WORDS = new Set([
  'about', 'after', 'again', 'also', 'and', 'are', 'build', 'can', 'change',
  'code', 'do', 'for', 'from', 'have', 'into', 'make', 'need', 'please', 'the',
  'this', 'to', 'with', 'work', 'working', 'you', 'your',
]);

function normalizedPath(value: string): string {
  return value.trim().replaceAll('\\', '/').replace(/\/$/, '').toLowerCase();
}

function intentTokens(value: string): Set<string> {
  return new Set(
    value
      .toLowerCase()
      .split(/[^a-z0-9_-]+/)
      .map((token) => token.trim())
      .filter((token) => token.length >= 3 && !STOP_WORDS.has(token)),
  );
}

export function intentSimilarity(left: string, right: string): number {
  const a = intentTokens(left);
  const b = intentTokens(right);
  if (a.size === 0 || b.size === 0) return 0;
  let intersection = 0;
  for (const token of a) if (b.has(token)) intersection += 1;
  return intersection / new Set([...a, ...b]).size;
}

export function isFreshPresence(
  presence: FounderAgentPresence,
  now = Date.now(),
): boolean {
  const heartbeat = Date.parse(presence.heartbeatAt);
  return Number.isFinite(heartbeat) && now - heartbeat <= AGENT_PRESENCE_TTL_MS;
}

export function findAgentRisks(
  self: FounderAgentPresence,
  presences: readonly FounderAgentPresence[],
  now = Date.now(),
): FounderAgentRisk[] {
  const workspace = normalizedPath(self.workspacePath);
  const selfFiles = new Set(self.ownedFiles.map(normalizedPath));
  const risks: FounderAgentRisk[] = [];

  for (const peer of presences) {
    if (peer.id === self.id || !isFreshPresence(peer, now)) continue;
    if (normalizedPath(peer.workspacePath) !== workspace) continue;

    const overlappingFiles = peer.ownedFiles
      .map(normalizedPath)
      .filter((file) => selfFiles.has(file));
    const similarity = intentSimilarity(self.title, peer.title);
    if (overlappingFiles.length === 0 && similarity < 0.34) continue;

    const reason = overlappingFiles.length > 0
      ? `Both tasks are touching ${overlappingFiles.length} file${overlappingFiles.length === 1 ? '' : 's'}`
      : 'The two task goals substantially overlap';
    risks.push({
      peer,
      reason,
      overlappingFiles,
      intentSimilarity: similarity,
    });
  }

  return risks.sort((a, b) =>
    b.overlappingFiles.length - a.overlappingFiles.length
    || b.intentSimilarity - a.intentSimilarity,
  );
}

export function coordinationPrompt(
  self: FounderAgentPresence,
  presences: readonly FounderAgentPresence[],
  now = Date.now(),
): string {
  const activePeers = presences.filter(
    (peer) =>
      peer.id !== self.id
      && isFreshPresence(peer, now)
      && normalizedPath(peer.workspacePath) === normalizedPath(self.workspacePath),
  );
  if (activePeers.length === 0) return '';

  const risks = findAgentRisks(self, activePeers, now);
  const lines = [
    '## Live agent coordination',
    `${activePeers.length} other active task${activePeers.length === 1 ? '' : 's'} share this workspace.`,
  ];
  for (const peer of activePeers.slice(0, 6)) {
    const risk = risks.find((item) => item.peer.id === peer.id);
    const files = peer.ownedFiles.slice(0, 5).join(', ') || 'no files claimed yet';
    lines.push(`- ${peer.title} [${peer.branch ?? 'no branch'}]: ${files}${risk ? `; COORDINATE: ${risk.reason}` : ''}`);
  }
  lines.push(
    'Before mutating an overlapping file, declare ownership or choose a non-overlapping task. Re-read this coordination block between tool turns. Do not duplicate work already in progress.',
  );
  return lines.join('\n');
}

export function parsePresence(value: unknown): FounderAgentPresence | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Partial<FounderAgentPresence>;
  if (
    input.version !== 1
    || typeof input.id !== 'string'
    || typeof input.workspacePath !== 'string'
    || typeof input.workspaceName !== 'string'
    || typeof input.title !== 'string'
    || typeof input.provider !== 'string'
    || (input.status !== 'working' && input.status !== 'waiting')
    || !Array.isArray(input.ownedFiles)
    || typeof input.startedAt !== 'string'
    || typeof input.heartbeatAt !== 'string'
  ) return null;
  return {
    ...input,
    version: 1,
    ownedFiles: input.ownedFiles.filter((file): file is string => typeof file === 'string').slice(0, 80),
  } as FounderAgentPresence;
}
