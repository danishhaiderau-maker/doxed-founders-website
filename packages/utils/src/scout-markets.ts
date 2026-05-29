export type ScoutMarketSide = 'YES' | 'NO';

export function computeScoutConviction(yesPool: number, noPool: number): number {
  const total = yesPool + noPool;
  if (total <= 0) return 50;
  return Math.round((yesPool / total) * 100);
}

export const DEFAULT_SCOUT_QUESTIONS = [
  'Will this project reach launch ready?',
  'Will the Raise Room fill before deadline?',
  'Will this project ship a token in the next 90 days?',
];

export function formatScoutMarketLabel(question: string, conviction: number): string {
  return `${question} — ${conviction}% YES conviction`;
}
