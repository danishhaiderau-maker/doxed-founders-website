/**
 * Shared types for the demo harness scorecard + smoke checks.
 * Kept in a side file so it can be imported by both the scorecard service
 * and the extended smoke checks without a circular dependency.
 */
export type CheckResult = {
  name: string;
  passed: boolean;
  detail: string;
  durationMs?: number;
};

export type PillarReport = {
  score: number;
  checks: CheckResult[];
  /** Free-form pillar-specific numbers (extracted from check details). */
  numbers?: Record<string, number | string | boolean | null>;
};

export type SwitchStates = {
  liveTrading: boolean;
  fundingSim: boolean;
  demoMode: boolean;
  cassetteMode: 'replay' | 'capture';
  laneSizePatch: boolean;
  labShadowTiles: boolean;
  executionPaused: boolean;
};

export type FakeCounts = {
  fakeUsers: number;
  fakeProjects: number;
  fakeFounders: number;
  fakeRaises: number;
  fakeAllocations: number;
  fakeTrades: number;
  fakeAiCalls: number;
  fakeMessages: number;
  fakeNotifications: number;
};

export type ReadinessScorecard = {
  overall: 'PASS' | 'FAIL' | 'DEGRADED';
  readinessScore: number;
  generatedAt: string;
  durationMs: number;
  pillars: {
    platform: PillarReport;
    bot: PillarReport;
    analyzer: PillarReport;
    genome: PillarReport;
    relay: PillarReport;
    ai: PillarReport;
    founder: PillarReport;
    stress: PillarReport;
  };
  switches: SwitchStates;
  numbers: FakeCounts;
  totals: {
    checksRun: number;
    checksPassed: number;
    checksFailed: number;
  };
};

export const READINESS_PASS_THRESHOLD = 80;
export const READINESS_DEGRADED_THRESHOLD = 60;
