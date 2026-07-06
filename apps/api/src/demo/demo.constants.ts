export const DEMO_EMAIL_DOMAIN = '@doxxed.demo';
export const DEMO_SLUG_PREFIX = 'demo-';
export const DEMO_HANDLE_PREFIX = 'demo_';

export type DemoSeedScale = 'small' | 'medium' | 'large' | 'xlarge';

export const DEMO_SCALE_PRESETS: Record<
  DemoSeedScale,
  { users: number; projects: number; founders: number }
> = {
  small: { users: 20, projects: 10, founders: 8 },
  medium: { users: 50, projects: 48, founders: 40 },
  large: { users: 50, projects: 15, founders: 12 },
  xlarge: { users: 2500, projects: 150, founders: 500 },
};

export function parseDemoSeedScale(raw?: string): DemoSeedScale {
  const v = raw?.trim().toLowerCase();
  if (v === 'small' || v === 'medium' || v === 'large' || v === 'xlarge') return v;
  return 'medium';
}

export function isDemoEmail(email: string): boolean {
  return email.endsWith(DEMO_EMAIL_DOMAIN);
}

export function demoUserEmail(index: number, role: 'founder' | 'builder' | 'scout'): string {
  const pad = String(index).padStart(3, '0');
  return `demo-${role}-${pad}${DEMO_EMAIL_DOMAIN}`;
}

export function demoUserWhere() {
  return { email: { endsWith: DEMO_EMAIL_DOMAIN } };
}

export function demoProjectWhere() {
  return { slug: { startsWith: DEMO_SLUG_PREFIX } };
}

export function demoFounderWhere() {
  return { slug: { startsWith: DEMO_SLUG_PREFIX } };
}

export function isDemoModeEnabled(): boolean {
  return process.env.DEMO_MODE_ENABLED === 'true';
}
