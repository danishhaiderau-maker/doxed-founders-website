/**
 * Progressive unlock stages 1–6 (Architecture Review v2 #8 / RR-015).
 */

export const LAUNCH_STAGES = [
  'BUILDER',
  'WORKSPACE',
  'PROJECT',
  'RAISE_ROOM',
  'GRADUATION',
  'FOUNDER_EXCHANGE',
] as const;

export type LaunchStageKey = (typeof LAUNCH_STAGES)[number];

export const LAUNCH_STAGE_ORDER: Record<LaunchStageKey, number> = {
  BUILDER: 1,
  WORKSPACE: 2,
  PROJECT: 3,
  RAISE_ROOM: 4,
  GRADUATION: 5,
  FOUNDER_EXCHANGE: 6,
};

export const LAUNCH_STAGE_META: Record<
  LaunchStageKey,
  { label: string; unlocks: string; nextHint: string }
> = {
  BUILDER: {
    label: 'Builder',
    unlocks: 'Discover listings and follow projects',
    nextHint: 'Get listed on the platform to unlock Trust Center',
  },
  WORKSPACE: {
    label: 'Workspace',
    unlocks: 'Trust Center validation and community signals',
    nextHint: 'Ship build proof and founder verification',
  },
  PROJECT: {
    label: 'Project',
    unlocks: 'Raise Room visibility and paper conviction commits',
    nextHint: 'Earn weighted community validation',
  },
  RAISE_ROOM: {
    label: 'Raise Room',
    unlocks: 'Validation signals and conviction ranking',
    nextHint: 'Complete regulatory questionnaire and launch qualification',
  },
  GRADUATION: {
    label: 'Graduation',
    unlocks: 'Launch Qualification score and regulatory clearance',
    nextHint: 'Pass all Phase 1.5 gates for Proof Raise',
  },
  FOUNDER_EXCHANGE: {
    label: 'Founder Exchange',
    unlocks: 'Proof Raise window and Founder Graduation registration',
    nextHint: 'Graduate to curated swap layer after snapshot',
  },
};

export function launchStageNumber(stage: LaunchStageKey): number {
  return LAUNCH_STAGE_ORDER[stage];
}

export function meetsLaunchStage(
  current: LaunchStageKey,
  required: LaunchStageKey,
): boolean {
  return launchStageNumber(current) >= launchStageNumber(required);
}

export function nextLaunchStage(stage: LaunchStageKey): LaunchStageKey | null {
  const idx = LAUNCH_STAGES.indexOf(stage);
  if (idx < 0 || idx >= LAUNCH_STAGES.length - 1) return null;
  return LAUNCH_STAGES[idx + 1]!;
}

export type ProgressiveUnlockProgress = {
  currentStage: LaunchStageKey;
  stageNumber: number;
  nextStage: LaunchStageKey | null;
  nextHint: string | null;
  stages: Array<{
    key: LaunchStageKey;
    number: number;
    label: string;
    unlocks: string;
    status: 'complete' | 'active' | 'locked';
  }>;
};

export function buildProgressiveUnlockProgress(
  currentStage: LaunchStageKey,
): ProgressiveUnlockProgress {
  const currentNum = launchStageNumber(currentStage);
  const next = nextLaunchStage(currentStage);

  return {
    currentStage,
    stageNumber: currentNum,
    nextStage: next,
    nextHint: next ? LAUNCH_STAGE_META[next].nextHint : null,
    stages: LAUNCH_STAGES.map((key) => {
      const number = launchStageNumber(key);
      let status: 'complete' | 'active' | 'locked' = 'locked';
      if (number < currentNum) status = 'complete';
      else if (number === currentNum) status = 'active';
      return {
        key,
        number,
        label: LAUNCH_STAGE_META[key].label,
        unlocks: LAUNCH_STAGE_META[key].unlocks,
        status,
      };
    }),
  };
}
