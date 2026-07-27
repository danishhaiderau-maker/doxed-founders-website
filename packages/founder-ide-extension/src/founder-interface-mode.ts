export type FounderInterfaceMode = 'founder' | 'developer';

export interface FounderInterfaceModeDefinition {
  id: FounderInterfaceMode;
  label: string;
  activityBarLocation: 'hidden' | 'default';
  menuBarVisibility: 'hidden' | 'classic';
  commandCenter: boolean;
  layoutControl: boolean;
  statusBarVisible: boolean;
  editorTabs: 'single' | 'multiple';
  advancedIdeTools: boolean;
}

export const FOUNDER_INTERFACE_MODES: readonly FounderInterfaceModeDefinition[] = [
  {
    id: 'founder',
    label: 'Founder mode',
    activityBarLocation: 'hidden',
    menuBarVisibility: 'hidden',
    commandCenter: false,
    layoutControl: false,
    statusBarVisible: false,
    editorTabs: 'single',
    advancedIdeTools: false,
  },
  {
    id: 'developer',
    label: 'Developer mode',
    activityBarLocation: 'default',
    menuBarVisibility: 'classic',
    commandCenter: true,
    layoutControl: true,
    statusBarVisible: true,
    editorTabs: 'multiple',
    advancedIdeTools: true,
  },
] as const;

export function normalizeFounderInterfaceMode(value: unknown): FounderInterfaceMode {
  return value === 'developer' ? 'developer' : 'founder';
}

export function founderInterfaceModeDefinition(
  value: unknown,
): FounderInterfaceModeDefinition {
  const mode = normalizeFounderInterfaceMode(value);
  return FOUNDER_INTERFACE_MODES.find((candidate) => candidate.id === mode)
    ?? FOUNDER_INTERFACE_MODES[0];
}
