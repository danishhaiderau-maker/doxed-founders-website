import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

export type FounderAgentMode = 'focus' | 'team';

export interface FounderAgentModeDefinition {
  id: FounderAgentMode;
  label: string;
  summary: string;
}

export const FOUNDER_AGENT_MODES: readonly FounderAgentModeDefinition[] = [
  {
    id: 'focus',
    label: 'Focus',
    summary: 'One agent owns the task and verifies its work.',
  },
  {
    id: 'team',
    label: 'Team',
    summary: 'Two read-only advisers scout risks while one agent owns edits.',
  },
] as const;

export function normalizeFounderAgentMode(value: unknown): FounderAgentMode {
  return value === 'team' ? 'team' : 'focus';
}

export function founderAgentModeDefinition(mode: FounderAgentMode): FounderAgentModeDefinition {
  return FOUNDER_AGENT_MODES.find((candidate) => candidate.id === mode)
    ?? FOUNDER_AGENT_MODES[0];
}

export function founderPreferencesPath(): string {
  return path.join(os.homedir(), '.founder-ide', 'preferences.json');
}

export function readFounderAgentMode(file = founderPreferencesPath()): FounderAgentMode {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as { agentMode?: unknown };
    return normalizeFounderAgentMode(parsed.agentMode);
  } catch {
    return 'focus';
  }
}

export function writeFounderAgentMode(
  mode: FounderAgentMode,
  file = founderPreferencesPath(),
): void {
  let current: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      current = parsed as Record<string, unknown>;
    }
  } catch {
    // A missing or invalid preference file is replaced atomically below.
  }
  const next = { ...current, version: 1, agentMode: mode };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(next, null, 2), { encoding: 'utf8', mode: 0o600 });
  fs.rmSync(file, { force: true });
  fs.renameSync(temp, file);
}
