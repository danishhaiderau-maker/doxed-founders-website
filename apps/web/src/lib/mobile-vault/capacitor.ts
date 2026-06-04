'use client';

export const MOBILE_VAULT_APP_VERSION = '0.4.0';

export function isCapacitorNative(): boolean {
  if (typeof window === 'undefined') return false;
  const w = window as Window & { Capacitor?: { isNativePlatform?: () => boolean } };
  if (w.Capacitor?.isNativePlatform?.()) return true;
  return false;
}

export async function whenCapacitorReady(): Promise<boolean> {
  if (!isCapacitorNative()) return false;
  try {
    const { Capacitor } = await import('@capacitor/core');
    return Capacitor.isNativePlatform();
  } catch {
    return false;
  }
}
