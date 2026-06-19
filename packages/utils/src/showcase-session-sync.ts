/** Showcase bot session epoch — when this changes, copy/relay/paper sessions must reset to $500. */

export type ShowcaseSessionEpochInput = {
  botVersion?: string | null;
  botStartTime?: number | null;
  freshResetTs?: number | null;
};

export type ShowcaseSessionEpoch = {
  key: string;
  botVersion: string;
  botStartTime: number;
  freshResetTs: number;
};

export const SHOWCASE_SESSION_STARTING_BALANCE_USD = 500;

export function buildShowcaseSessionEpoch(input: ShowcaseSessionEpochInput): ShowcaseSessionEpoch {
  const botVersion = String(input.botVersion ?? 'unknown').trim() || 'unknown';
  const botStartTime =
    typeof input.botStartTime === 'number' && Number.isFinite(input.botStartTime)
      ? input.botStartTime
      : 0;
  const freshResetTs =
    typeof input.freshResetTs === 'number' && Number.isFinite(input.freshResetTs)
      ? input.freshResetTs
      : 0;
  return {
    key: `${botVersion}|${botStartTime}|${freshResetTs}`,
    botVersion,
    botStartTime,
    freshResetTs,
  };
}
