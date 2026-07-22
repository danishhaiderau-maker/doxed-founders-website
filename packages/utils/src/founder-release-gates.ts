export const FOUNDER_BENCHMARK_PROTOCOL = {
  version: 'founder-benchmark-v1',
  minimumTasks: 50,
  preferredTasks: 100,
  runsPerTask: 5,
  requiredModes: ['baseline', 'founder'] as const,
  requiredMetrics: [
    'raw_input_tokens',
    'cached_input_tokens',
    'output_tokens',
    'reasoning_tokens',
    'weighted_units',
    'provider_cost_usd',
    'latency_ms',
    'task_success',
    'tests_passed',
  ] as const,
} as const;

export const FOUNDER_BETA_EXIT_CRITERIA = {
  minimumFounders: 20,
  minimumCompletedTasks: 200,
  maximumCriticalFailureRate: 0.02,
  minimumWeekTwoRetention: 0.35,
  maximumUnreconciledCostReservations: 0,
} as const;

export const FOUNDER_INVITE_POLICY = {
  maximumRedemptionsPerCode: 1,
  bindToFirstDevice: true,
  revocable: true,
  auditEveryRedemption: true,
} as const;

export type FounderSigningGate =
  | 'not_configured'
  | 'identity_pending'
  | 'ready'
  | 'verified';

export interface FounderReleaseEvidence {
  unitTestsPassed: boolean;
  integrationTestsPassed: boolean;
  installedQaPassed: boolean;
  rollbackPassed: boolean;
  cleanVmPassed: boolean;
  soakHours: number;
  signingGate: FounderSigningGate;
  betaFounders: number;
  betaCompletedTasks: number;
  criticalFailureRate: number;
  weekTwoRetention: number;
  unreconciledCostReservations: number;
}

export function founderPrivateBetaReady(
  evidence: FounderReleaseEvidence,
): boolean {
  return (
    evidence.unitTestsPassed &&
    evidence.integrationTestsPassed &&
    evidence.installedQaPassed &&
    evidence.rollbackPassed &&
    evidence.unreconciledCostReservations === 0
  );
}

export function founderPublicReleaseReady(
  evidence: FounderReleaseEvidence,
): boolean {
  return (
    founderPrivateBetaReady(evidence) &&
    evidence.cleanVmPassed &&
    evidence.soakHours >= 24 &&
    evidence.signingGate === 'verified' &&
    evidence.betaFounders >= FOUNDER_BETA_EXIT_CRITERIA.minimumFounders &&
    evidence.betaCompletedTasks >=
      FOUNDER_BETA_EXIT_CRITERIA.minimumCompletedTasks &&
    evidence.criticalFailureRate <=
      FOUNDER_BETA_EXIT_CRITERIA.maximumCriticalFailureRate &&
    evidence.weekTwoRetention >=
      FOUNDER_BETA_EXIT_CRITERIA.minimumWeekTwoRetention &&
    evidence.unreconciledCostReservations <=
      FOUNDER_BETA_EXIT_CRITERIA.maximumUnreconciledCostReservations
  );
}
