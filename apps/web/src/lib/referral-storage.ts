const STORAGE_KEY = 'dcf_referral_code';
const COOKIE_KEY = 'dcf_ref';

export function normalizeReferralCode(raw?: string | null): string | null {
  const code = raw?.trim().toUpperCase();
  if (!code || code.length < 6) return null;
  return code;
}

export function persistReferralCode(raw?: string | null) {
  const code = normalizeReferralCode(raw);
  if (!code || typeof window === 'undefined') return null;
  window.localStorage.setItem(STORAGE_KEY, code);
  document.cookie = `${COOKIE_KEY}=${encodeURIComponent(code)}; path=/; max-age=${60 * 60 * 24 * 14}; SameSite=Lax`;
  return code;
}

export function readReferralCode(): string | null {
  if (typeof window === 'undefined') return null;
  const fromStorage = normalizeReferralCode(window.localStorage.getItem(STORAGE_KEY));
  if (fromStorage) return fromStorage;
  const match = document.cookie.match(new RegExp(`(?:^|; )${COOKIE_KEY}=([^;]*)`));
  return normalizeReferralCode(match?.[1] ? decodeURIComponent(match[1]) : null);
}

export function clearReferralCode() {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(STORAGE_KEY);
  document.cookie = `${COOKIE_KEY}=; path=/; max-age=0; SameSite=Lax`;
}
