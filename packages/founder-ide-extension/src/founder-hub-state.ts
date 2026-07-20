export type FounderWorkspaceMode = 'local' | 'hybrid' | 'cloud';

export interface FounderWorkspaceModeDefinition {
  id: FounderWorkspaceMode;
  label: string;
  summary: string;
  services: string;
}

export const FOUNDER_WORKSPACE_MODES: readonly FounderWorkspaceModeDefinition[] = [
  {
    id: 'local',
    label: 'Local',
    summary: 'Private on this device',
    services: 'Ollama and your own model keys',
  },
  {
    id: 'hybrid',
    label: 'Hybrid',
    summary: 'Local workspace, selected cloud services',
    services: 'GitHub, Vercel, Railway, Neon and more',
  },
  {
    id: 'cloud',
    label: 'Cloud',
    summary: 'Managed by Founder',
    services: 'Managed models, sync and remote agents',
  },
] as const;

export function normalizeWorkspaceMode(value: unknown): FounderWorkspaceMode {
  return value === 'local' || value === 'cloud' || value === 'hybrid'
    ? value
    : 'hybrid';
}

export function workspaceModeDefinition(
  mode: FounderWorkspaceMode,
): FounderWorkspaceModeDefinition {
  return FOUNDER_WORKSPACE_MODES.find((candidate) => candidate.id === mode)
    ?? FOUNDER_WORKSPACE_MODES[1];
}
