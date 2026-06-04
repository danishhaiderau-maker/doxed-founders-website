/** Metadata-only IDE context from Founder Node (no file contents). */

export type DesktopBridgeSnapshot = {
  nodeId: string;
  label: string;
  branch?: string | null;
  openFilePaths?: string[];
  taskLabel?: string | null;
  editSummary?: string | null;
  agentStatus?: string | null;
  updatedAt: string;
};

export type DesktopBridgeInput = {
  branch?: string;
  openFilePaths?: string[];
  taskLabel?: string;
  editSummary?: string;
  agentStatus?: string;
};

const MAX_PATHS = 12;
const MAX_PATH_LEN = 120;

export function sanitizeDesktopBridge(
  nodeId: string,
  label: string,
  input: DesktopBridgeInput | null | undefined,
): DesktopBridgeSnapshot | null {
  if (!input || typeof input !== 'object') return null;
  const paths = Array.isArray(input.openFilePaths)
    ? input.openFilePaths
        .filter((p) => typeof p === 'string' && p.trim())
        .map((p) => p.trim().replace(/\\/g, '/').split('/').pop() ?? p.trim())
        .map((p) => p.slice(0, MAX_PATH_LEN))
        .slice(0, MAX_PATHS)
    : undefined;

  return {
    nodeId,
    label: label.slice(0, 80),
    branch: typeof input.branch === 'string' ? input.branch.slice(0, 120) : null,
    openFilePaths: paths?.length ? paths : undefined,
    taskLabel: typeof input.taskLabel === 'string' ? input.taskLabel.slice(0, 200) : null,
    editSummary: typeof input.editSummary === 'string' ? input.editSummary.slice(0, 240) : null,
    agentStatus: typeof input.agentStatus === 'string' ? input.agentStatus.slice(0, 80) : null,
    updatedAt: new Date().toISOString(),
  };
}

export function formatDesktopBridgeForPrompt(snapshot: DesktopBridgeSnapshot | null): string | null {
  if (!snapshot) return null;
  const lines = [
    '## Desktop IDE bridge (metadata only — no file contents)',
    `Device: ${snapshot.label} (${snapshot.nodeId.slice(0, 8)}…)`,
    snapshot.branch ? `Branch: ${snapshot.branch}` : '',
    snapshot.taskLabel ? `Current task: ${snapshot.taskLabel}` : '',
    snapshot.agentStatus ? `Agent status: ${snapshot.agentStatus}` : '',
    snapshot.editSummary ? `Recent edits: ${snapshot.editSummary}` : '',
    snapshot.openFilePaths?.length
      ? `Open files (names): ${snapshot.openFilePaths.join(', ')}`
      : '',
    `Updated: ${snapshot.updatedAt}`,
  ].filter(Boolean);
  return lines.join('\n');
}
