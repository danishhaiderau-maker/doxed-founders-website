import type { VaultMergePatch } from './vault-merge.js';
import {
  type FounderDecisionKind,
  type FounderDecisionRequest,
  type FounderDecisionResolution,
  type FounderGoalContract,
  validateFounderDecisionRequest,
  validateFounderGoal,
} from './founder-goal-control.js';

export const FOUNDER_OS_MEMORY_DIR = '.github/founder-os';
const FOUNDER_GOAL_CONTROL_KEY = '_founderGoalControl';
const MAX_DURABLE_DECISIONS = 25;

export const FOUNDER_OS_MEMORY_FILES = {
  projectContext: `${FOUNDER_OS_MEMORY_DIR}/project-context.md`,
  goalContract: `${FOUNDER_OS_MEMORY_DIR}/goal.json`,
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

export type FounderOsDurableDecision = {
  id: string;
  kind: Extract<FounderDecisionKind, 'goal_amendment' | 'research_preference'>;
  title: string;
  outcome: string;
  resolvedAt: string;
};

export type FounderOsGoalContractFile = {
  version: 1;
  updatedAt: string;
  currentGoal: string;
  goal: FounderGoalContract | null;
  durableDecisions: FounderOsDurableDecision[];
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
  /** Plaintext LWW merge patch for cross-device sync (Phase 4). */
  mergePatch?: VaultMergePatch;
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
    description:
      'Build-in-public memory on Founder OS — goals, tasks, GitHub sync. Best for multi-device without a local vault.',
  },
  {
    key: 'GITHUB' as const,
    label: 'GitHub repo files',
    description:
      'You own the files in .github/founder-os/. Portable forever — no vendor lock-in on memory format.',
  },
  {
    key: 'LOCAL_DEVICE' as const,
    label: 'This browser only',
    description:
      'Memory stays in this browser. Nothing on our servers — but lost if you clear data or switch devices.',
  },
  {
    key: 'LOCAL_SYNC' as const,
    label: 'Local + encrypted relay',
    description:
      'Local-first with a lightweight cloud snapshot so you can resume on another device. Full markdown stays local.',
  },
  {
    key: 'FOUNDER_NODE' as const,
    label: 'Founder Vault (Founder Node) — recommended',
    description:
      'Full company memory on your machine. Founder OS stores metadata only — vault contents are encrypted before relay.',
  },
] as const;

export type VaultRelaySummary = {
  mode: MemoryStorageModeKey;
  hasEncryptedBlob: boolean;
  tasksRemaining: number;
  currentGoal: string | null;
  lastSyncedAt: string | null;
  deviceLabel: string | null;
  nodeOnline: boolean;
  nodeLabel: string | null;
  vaultHealthy: boolean;
};

/** Summarize vault relay state for UI and Copilot — server never decrypts encryptedVaultBlob. */
export function extractVaultRelaySummary(input: {
  memoryStorageMode?: MemoryStorageModeKey | string;
  deviceSync?: {
    updatedAt: string;
    deviceLabel: string | null;
    payload: DeviceMemoryPayload | DeviceMemoryMetadataPayload;
  } | null;
  connectedNodes?: Array<{ status: string; label: string; vaultHealthy?: boolean }>;
}): VaultRelaySummary | null {
  const mode = input.memoryStorageMode;
  if (mode !== 'FOUNDER_NODE' && mode !== 'LOCAL_SYNC') return null;

  const payload = input.deviceSync?.payload;
  let tasksRemaining = 0;
  let currentGoal: string | null = null;
  let hasEncryptedBlob = false;
  let deviceLabel = input.deviceSync?.deviceLabel ?? null;

  if (payload) {
    if (isMetadataOnlyPayload(payload)) {
      tasksRemaining = payload.tasksRemaining;
      currentGoal = payload.currentGoal?.trim() || null;
      hasEncryptedBlob = Boolean(payload.encryptedVaultBlob);
      deviceLabel = deviceLabel ?? payload.deviceLabel ?? null;
    } else {
      const meta = stripDeviceMemoryToMetadata(payload);
      tasksRemaining = meta.tasksRemaining;
      currentGoal = meta.currentGoal?.trim() || null;
      hasEncryptedBlob = Boolean(payload.encryptedVaultBlob);
      deviceLabel = deviceLabel ?? payload.deviceLabel ?? null;
    }
  }

  const nodes = input.connectedNodes ?? [];
  const online = nodes.find((n) => n.status === 'online');

  return {
    mode: mode as MemoryStorageModeKey,
    hasEncryptedBlob,
    tasksRemaining,
    currentGoal,
    lastSyncedAt: input.deviceSync?.updatedAt ?? null,
    deviceLabel,
    nodeOnline: Boolean(online),
    nodeLabel: online?.label ?? nodes[0]?.label ?? null,
    vaultHealthy: nodes.length === 0 ? true : nodes.every((n) => n.vaultHealthy !== false),
  };
}

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
  updatedAt: string;
}): FounderOsTasksFile {
  return {
    version: 1,
    updatedAt: input.updatedAt,
    currentGoal: input.currentGoal,
    tasks: input.tasks,
  };
}

export function buildGoalContractJsonFile(input: {
  memoryGraph: unknown;
  fallbackGoal: string;
  updatedAt: string;
}): FounderOsGoalContractFile {
  const state = latestGoalControlState(input.memoryGraph);
  const storedGoal =
    state?.goal && validateFounderGoal(state.goal).length === 0
      ? structuredClone(state.goal)
      : null;
  const goal = storedGoal ? publicGoalContract(storedGoal) : null;
  const resolutions = new Map(
    (state?.resolutions ?? [])
      .filter(isDecisionResolution)
      .map((resolution) => [resolution.requestId, resolution]),
  );
  const durableDecisions = (state?.decisions ?? [])
    .filter(
      (
        decision,
      ): decision is FounderDecisionRequest & {
        kind: FounderOsDurableDecision['kind'];
      } =>
        isDurableDecision(decision)
        && validateFounderDecisionRequest(decision).length === 0,
    )
    .map((decision) =>
      durableDecisionSummary(decision, resolutions.get(decision.id)))
    .filter(
      (decision): decision is FounderOsDurableDecision => Boolean(decision),
    )
    .sort((left, right) => left.resolvedAt.localeCompare(right.resolvedAt))
    .slice(-MAX_DURABLE_DECISIONS);

  return {
    version: 1,
    updatedAt: latestIsoTimestamp([
      input.updatedAt,
      state?.updatedAt,
      goal?.updatedAt,
      ...durableDecisions.map((decision) => decision.resolvedAt),
    ]) ?? input.updatedAt,
    currentGoal:
      goal?.objective.trim()
      || safePublicText(input.fallbackGoal, 1_200)
      || 'Define your next milestone',
    goal,
    durableDecisions,
  };
}

export function parseGoalContractJson(
  raw: string,
): FounderOsGoalContractFile | null {
  try {
    const parsed = JSON.parse(raw) as FounderOsGoalContractFile;
    if (
      parsed?.version !== 1
      || typeof parsed.currentGoal !== 'string'
      || !Array.isArray(parsed.durableDecisions)
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
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

type StoredGoalControlState = {
  goal?: FounderGoalContract | null;
  decisions?: FounderDecisionRequest[];
  resolutions?: FounderDecisionResolution[];
  updatedAt?: string;
};

function latestGoalControlState(
  memoryGraph: unknown,
): StoredGoalControlState | null {
  if (!isRecord(memoryGraph)) return null;
  const stored = memoryGraph[FOUNDER_GOAL_CONTROL_KEY];
  if (!isRecord(stored)) return null;
  const candidates =
    stored.schemaVersion === 1 && isRecord(stored.workspaces)
      ? Object.values(stored.workspaces).filter(isRecord)
      : [stored];
  return (
    candidates
      .map((candidate) => candidate as StoredGoalControlState)
      .sort((left, right) =>
        validIso(right.updatedAt).localeCompare(validIso(left.updatedAt)))[0]
    ?? null
  );
}

function isDurableDecision(
  decision: unknown,
): decision is FounderDecisionRequest & {
  kind: FounderOsDurableDecision['kind'];
} {
  return (
    isRecord(decision)
    && decision.status === 'resolved'
    && (
      decision.kind === 'goal_amendment'
      || decision.kind === 'research_preference'
    )
  );
}

function durableDecisionSummary(
  decision: FounderDecisionRequest & {
    kind: FounderOsDurableDecision['kind'];
  },
  resolution: FounderDecisionResolution | undefined,
): FounderOsDurableDecision | null {
  if (!resolution) return null;
  const option = decision.options
    .filter((candidate): candidate is NonNullable<typeof candidate> =>
      Boolean(candidate))
    .find((candidate) => candidate.id === resolution.selectedOptionId);
  const outcome =
    decision.kind === 'goal_amendment'
    && decision.proposedGoalObjective?.trim()
      ? decision.proposedGoalObjective.trim()
      : option?.label.trim()
        || (resolution.customAnswer?.trim()
          ? 'Custom founder decision recorded privately'
          : '');
  if (!outcome) return null;
  return {
    id: decision.id,
    kind: decision.kind,
    title: safePublicText(decision.title, 240),
    outcome: safePublicText(outcome, 1_200),
    resolvedAt: resolution.resolvedAt,
  };
}

function publicGoalContract(goal: FounderGoalContract): FounderGoalContract {
  return {
    ...goal,
    id: cleanPublicText(goal.id, 160),
    objective: safePublicText(goal.objective, 1_200),
    constraints: goal.constraints
      .map((constraint) => safePublicText(constraint, 500))
      .filter(Boolean)
      .slice(0, 30),
    successEvidence: goal.successEvidence.slice(0, 30).map((evidence) => ({
      ...evidence,
      id: cleanPublicText(evidence.id, 160),
      label: safePublicText(evidence.label, 500),
    })),
  };
}

function safePublicText(value: string, max: number): string {
  const clean = cleanPublicText(value, max);
  return looksSecretLike(clean) ? '[redacted from repository]' : clean;
}

function cleanPublicText(value: string, max: number): string {
  return value.replace(/\s+/g, ' ').trim().slice(0, max);
}

function looksSecretLike(value: string): boolean {
  return /(bearer\s+[a-z0-9._-]{12,}|(?:api[_ -]?key|secret|token)\s*[:=]\s*\S{8,}|sk-[a-z0-9_-]{12,})/i
    .test(value);
}

function isDecisionResolution(
  value: unknown,
): value is FounderDecisionResolution {
  return (
    isRecord(value)
    && typeof value.requestId === 'string'
    && typeof value.resolvedAt === 'string'
    && Number.isFinite(Date.parse(value.resolvedAt))
    && (value.resolvedBy === 'founder' || value.resolvedBy === 'approved_policy')
  );
}

function latestIsoTimestamp(values: Array<string | undefined>): string | null {
  return values
    .map(validIso)
    .filter(Boolean)
    .sort()
    .at(-1) ?? null;
}

function validIso(value: unknown): string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? value
    : '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}
