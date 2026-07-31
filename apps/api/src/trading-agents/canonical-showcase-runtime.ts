export const CANONICAL_SHOWCASE_BOT_URL = 'https://doxed-btc-bot.fly.dev';

export function normalizeShowcaseBotUrl(value: string | null | undefined): string {
  return String(value ?? '').trim().replace(/\/$/, '');
}

export function isCanonicalShowcaseBotUrl(value: string | null | undefined): boolean {
  return normalizeShowcaseBotUrl(value) === CANONICAL_SHOWCASE_BOT_URL;
}
