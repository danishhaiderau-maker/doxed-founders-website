/** Query flag set by the Android APK (Capacitor) entry URL. */
export const MOBILE_APP_QUERY = 'app';
export const MOBILE_APP_VALUE = 'android';
export const MOBILE_APP_STORAGE_KEY = 'dcf_mobile_app_android';

export function isMobileAppQuery(search: string): boolean {
  if (!search) return false;
  const params = new URLSearchParams(search.startsWith('?') ? search : `?${search}`);
  return params.get(MOBILE_APP_QUERY) === MOBILE_APP_VALUE;
}

export function persistMobileAppMode(): void {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(MOBILE_APP_STORAGE_KEY, '1');
  } catch {
    /* ignore */
  }
}

export function readMobileAppMode(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return sessionStorage.getItem(MOBILE_APP_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

/** Default home for the Android shell — discover + rankings hub. */
export const MOBILE_APP_HOME_PATH = '/discover';

export function mobileAppHomeUrl(siteOrigin = 'https://doxxedcrypto.digital'): string {
  const base = siteOrigin.replace(/\/$/, '');
  return `${base}${MOBILE_APP_HOME_PATH}?${MOBILE_APP_QUERY}=${MOBILE_APP_VALUE}`;
}
