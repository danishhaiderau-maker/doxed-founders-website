/** Referral program — DDollar rewards for onboarding via X. */

export const REFERRAL_REWARD_STANDARD = 5_000;
export const REFERRAL_REWARD_BLUE_VERIFIED = 15_000;
/** One-time bonus for a new user who signs in with a blue-verified X account. */
export const X_BLUE_VERIFIED_BONUS = 15_000;

export type ReferralRuleLine = {
  title: string;
  detail: string;
  amount?: number;
};

export const REFERRAL_RULES: ReferralRuleLine[] = [
  {
    title: 'Share your link',
    detail:
      'Every account gets a unique referral code. Share your link — when someone joins, you earn DDollar.',
  },
  {
    title: 'Referee must sign in with X',
    detail:
      'Referral rewards only count after the new user authenticates with X (Twitter). Email-only signups can still use your code, but your reward is paid once they connect X.',
    amount: REFERRAL_REWARD_STANDARD,
  },
  {
    title: 'Blue-verified X referral',
    detail:
      'If the referred user signs up with a blue-tick (verified) X account, you earn the premium tier instead of the standard amount.',
    amount: REFERRAL_REWARD_BLUE_VERIFIED,
  },
  {
    title: 'Verified newcomer bonus',
    detail:
      'New users who sign in with a blue-verified X account receive a one-time verified welcome bonus (on top of the standard welcome grant).',
    amount: X_BLUE_VERIFIED_BONUS,
  },
  {
    title: 'One reward per referral',
    detail:
      'Each referred account can only trigger one referral payout. Self-referrals and duplicate accounts are blocked.',
  },
];

/** Build a short uppercase referral code from user id (collision-checked server-side). */
export function buildReferralCodeSeed(userId: string, attempt = 0): string {
  let hash = 0;
  const s = `${userId}:ref:${attempt}`;
  for (let i = 0; i < s.length; i++) {
    hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
  }
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  let h = hash;
  for (let i = 0; i < 8; i++) {
    code += alphabet[h % alphabet.length];
    h = Math.imul(h ^ (h >>> 16), 0x45d9f3b) >>> 0;
  }
  return code;
}

export function referralSharePath(code: string): string {
  return `/register?ref=${encodeURIComponent(code)}`;
}
