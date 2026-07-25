export type FounderInterfaceMode = 'founder' | 'developer';

export interface FounderInterfaceModeDefinition {
  id: FounderInterfaceMode;
  label: string;
  activityBarLocation: 'hidden' | 'default';
  menuBarVisibility: 'toggle' | 'classic';
  advancedIdeTools: boolean;
}

export const FOUNDER_INTERFACE_MODES: readonly FounderInterfaceModeDefinition[] = [
  {
    id: 'founder',
    label: 'Founder mode',
    activityBarLocation: 'hidden',
    menuBarVisibility: 'toggle',
    advancedIdeTools: false,
  },
  {
    id: 'developer',
    label: 'Developer mode',
    activityBarLocation: 'default',
    menuBarVisibility: 'classic',
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
