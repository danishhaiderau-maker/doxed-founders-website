const STORAGE_KEY = 'dcf-onboarding-funnel-v1';

export type OnboardingFunnelEvent = {
  step: string;
  action: 'view' | 'complete' | 'skip' | 'dismiss';
  path?: string | null;
  at: string;
};

export function trackOnboardingStep(
  step: string,
  action: OnboardingFunnelEvent['action'],
  path?: string | null,
) {
  if (typeof window === 'undefined') return;
  const entry: OnboardingFunnelEvent = {
    step,
    action,
    path: path ?? null,
    at: new Date().toISOString(),
  };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const list: OnboardingFunnelEvent[] = raw ? (JSON.parse(raw) as OnboardingFunnelEvent[]) : [];
    list.push(entry);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(-80)));
  } catch {
    /* ignore */
  }
}

export function readOnboardingFunnel(): OnboardingFunnelEvent[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as OnboardingFunnelEvent[]) : [];
  } catch {
    return [];
  }
}
