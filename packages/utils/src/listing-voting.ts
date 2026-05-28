/** Community voting window after a scout submits a listing. */
export const VOTING_WINDOW_HOURS = 48;

export const VOTING_MIN_VOTERS = 3;
export const VOTING_MAX_VOTERS = 50;
export const VOTING_MIN_YES_PERCENT = 65;

export type VotingThreshold = {
  activeUsers: number;
  requiredVoters: number;
  minYesPercent: number;
  votingWindowHours: number;
  formula: string;
};

/**
 * Scales with platform size so early users aren't blocked, but quality bar rises as you grow.
 *
 * requiredVoters = clamp(3, ceil(sqrt(activeUsers) * 1.5), 50)
 * Early pass: total votes >= requiredVoters AND yes% >= 65% → admin inbox sooner
 * After 48h: always moves to admin inbox (you approve or reject — vote is signal only)
 */
export function computeVotingThreshold(activeUsers: number): VotingThreshold {
  const safe = Math.max(1, activeUsers);
  const requiredVoters = Math.min(
    VOTING_MAX_VOTERS,
    Math.max(VOTING_MIN_VOTERS, Math.ceil(Math.sqrt(safe) * 1.5)),
  );

  return {
    activeUsers: safe,
    requiredVoters,
    minYesPercent: VOTING_MIN_YES_PERCENT,
    votingWindowHours: VOTING_WINDOW_HOURS,
    formula: `max(${VOTING_MIN_VOTERS}, min(${VOTING_MAX_VOTERS}, ceil(√activeUsers × 1.5)))`,
  };
}

export type VoteTally = {
  total: number;
  yes: number;
  no: number;
  yesPercent: number;
  requiredVoters: number;
  minYesPercent: number;
  passed: boolean;
  remainingVoters: number;
};

export function tallyListingVotes(
  votes: { vote: 'YES' | 'NO' }[],
  requiredVoters: number,
  minYesPercent: number,
): VoteTally {
  const yes = votes.filter((v) => v.vote === 'YES').length;
  const no = votes.filter((v) => v.vote === 'NO').length;
  const total = yes + no;
  const yesPercent = total > 0 ? Math.round((yes / total) * 100) : 0;
  const passed = total >= requiredVoters && yesPercent >= minYesPercent;

  return {
    total,
    yes,
    no,
    yesPercent,
    requiredVoters,
    minYesPercent,
    passed,
    remainingVoters: Math.max(0, requiredVoters - total),
  };
}
