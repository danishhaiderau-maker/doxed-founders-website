export const FOUNDER_OS_MEMORY_DIR = '.github/founder-os';

export const FOUNDER_OS_MEMORY_FILES = {
  projectContext: `${FOUNDER_OS_MEMORY_DIR}/project-context.md`,
  roadmap: `${FOUNDER_OS_MEMORY_DIR}/roadmap.md`,
  tasks: `${FOUNDER_OS_MEMORY_DIR}/tasks.json`,
  decisions: `${FOUNDER_OS_MEMORY_DIR}/decisions.md`,
  launchChecklist: `${FOUNDER_OS_MEMORY_DIR}/launch-checklist.md`,
} as const;

export type FounderOsTasksFile = {
  version: 1;
  updatedAt: string;
  currentGoal: string;
  tasks: Array<{
    id: string;
    title: string;
    status: string;
    kind: string;
    done: boolean;
  }>;
};

export type NotificationBuyerMeta = {
  projectSlug?: string;
  projectTicker?: string;
  projectName?: string;
  scoutThesis?: string | null;
  buyers?: Array<{
    userId: string;
    displayName: string;
    amountUsd: number;
    twitterHandle?: string | null;
  }>;
};

export type DeviceMemoryPayload = {
  version: 1;
  updatedAt: string;
  projectName?: string;
  currentGoal: string;
  projectContext?: string;
  roadmap?: string;
  tasksFile?: FounderOsTasksFile;
  deviceLabel?: string;
  /** When true, server stores metadata only (Founder Node / zero-knowledge relay). */
  metadataOnly?: boolean;
  /** AES-256-GCM encrypted vault blob — server cannot decrypt. */
  encryptedVaultBlob?: string;
};

/** Minimal cross-device relay — no full markdown or task bodies. */
export type DeviceMemoryMetadataPayload = {
  version: 1;
  updatedAt: string;
  projectName?: string;
  currentGoal: string;
  deviceLabel?: string;
  tasksRemaining: number;
  metadataOnly: true;
  encryptedVaultBlob?: string;
};

export function stripDeviceMemoryToMetadata(
  payload: DeviceMemoryPayload,
): DeviceMemoryMetadataPayload {
  const tasksRemaining =
    payload.tasksFile?.tasks.filter((t) => !t.done).length ?? 0;
  return {
    version: 1,
    updatedAt: payload.updatedAt,
    projectName: payload.projectName,
    currentGoal: payload.currentGoal.trim(),
    deviceLabel: payload.deviceLabel,
    tasksRemaining,
    metadataOnly: true,
    encryptedVaultBlob: payload.encryptedVaultBlob,
  };
}

export function isMetadataOnlyPayload(
  payload: DeviceMemoryPayload | DeviceMemoryMetadataPayload,
): payload is DeviceMemoryMetadataPayload {
  return 'metadataOnly' in payload && payload.metadataOnly === true;
}

export const MEMORY_STORAGE_MODES = [
  {
    key: 'PLATFORM' as const,
    label: 'Cloud (Founder OS)',
    description: 'Memory lives in Founder OS database. Works on every device when signed in.',
  },
  {
    key: 'GITHUB' as const,
    label: 'GitHub repo files',
    description: 'Syncs to .github/founder-os/ in your repo. You own the files; portable forever.',
  },
  {
    key: 'LOCAL_DEVICE' as const,
    label: 'This device only',
    description: 'Free — stored in this browser only. Lost if you clear data or switch devices.',
  },
  {
    key: 'LOCAL_SYNC' as const,
    label: 'Local + cloud sync (recommended mobile)',
    description:
      'Saves on this device first (no extra storage cost). When online, syncs a small snapshot so other devices can resume.',
  },
  {
    key: 'FOUNDER_NODE' as const,
    label: 'Founder Node (self-custody vault)',
    description:
      'Full project memory on your PC/Mac/Linux via Founder Node. Founder OS stores only metadata — your vault stays on your machine.',
  },
] as const;

export type MemoryStorageModeKey = (typeof MEMORY_STORAGE_MODES)[number]['key'];

export function buildProjectContextMarkdown(input: {
  projectName: string;
  currentGoal: string;
  progressPercent: number;
  lastCommit?: string | null;
  lastActivity?: string | null;
}): string {
  return [
    `# ${input.projectName} — Project Context`,
    '',
    `> Synced by [Founder OS](https://doxxedcrypto.digital). Edit freely — we read this on resume.`,
    '',
    '## Current Goal',
    '',
    input.currentGoal,
    '',
    '## Progress',
    '',
    `${input.progressPercent}% complete`,
    '',
    '## Last Commit',
    '',
    input.lastCommit ?? '_No commits synced yet_',
    '',
    '## Last Activity',
    '',
    input.lastActivity ?? '_No recent activity_',
    '',
  ].join('\n');
}

export function buildRoadmapMarkdown(
  items: Array<{ title: string; status: string; description?: string | null }>,
): string {
  const lines = [
    '# Roadmap',
    '',
    '> Phases managed in Founder OS — synced to your repo for portability.',
    '',
  ];
  if (items.length === 0) {
    lines.push('_No roadmap items yet. Add phases in Founder Den._');
  } else {
    for (const item of items) {
      const box = item.status === 'DONE' ? '[x]' : '[ ]';
      lines.push(`- ${box} **${item.title}** (${item.status})`);
      if (item.description?.trim()) lines.push(`  - ${item.description.trim()}`);
    }
  }
  lines.push('');
  return lines.join('\n');
}

export function buildTasksJsonFile(input: {
  currentGoal: string;
  tasks: FounderOsTasksFile['tasks'];
}): FounderOsTasksFile {
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    currentGoal: input.currentGoal,
    tasks: input.tasks,
  };
}

export function parseTasksJson(raw: string): FounderOsTasksFile | null {
  try {
    const parsed = JSON.parse(raw) as FounderOsTasksFile;
    if (parsed?.version !== 1 || !Array.isArray(parsed.tasks)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function buildLaunchChecklistMarkdown(): string {
  return [
    '# Launch Checklist',
    '',
    '- [ ] Product live / demo ready',
    '- [ ] Founder profile + verification',
    '- [ ] Community feed + updates',
    '- [ ] Demand validation (Raise Room or Scout votes)',
    '- [ ] Token / contract readiness (if applicable)',
    '',
  ].join('\n');
}

export function buildDecisionsMarkdown(): string {
  return [
    '# Decisions Log',
    '',
    '_Record key product and technical decisions here. Founder OS appends during copilot sessions._',
    '',
  ].join('\n');
}
