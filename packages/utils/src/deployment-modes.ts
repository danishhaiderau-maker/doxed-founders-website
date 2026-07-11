/**
 * Phase 7 — Deployment Modes shared helpers.
 *
 * Used by the NestJS API (`apps/api/src/deployment-modes/`) and the Next.js
 * frontend (`apps/web/src/components/deployment-modes/`) so the two sides never
 * drift on what each mode means. The concrete wiring per mode is authoritative
 * in docs/DEPLOYMENT-MODES-UX.md §4 — this file is the code mirror of that spec.
 *
 * Core principle (doc §1): the AI Gateway is ALWAYS cloud-side in every mode.
 * Private mode does NOT mean local AI. The moat holds.
 */

export type DeploymentModeId = 'PRIVATE' | 'PUBLIC' | 'HYBRID';

/** Git backend slug — where the canonical remote lives. */
export type GitBackend = 'forgejo' | 'github';

/** Prisma provider slug for the project's runtime database. */
export type DbProvider = 'sqlite' | 'postgresql';

/** Hosting slug — how the project is exposed to the web. */
export type HostingType = 'tunnel-on-demand' | 'vercel' | 'custom';

/** Phone remote routing — how the phone reaches the runtime. */
export type PhoneRoute = 'tailscale' | 'public-url';

/** Always cloud-side. Included for completeness; never set to anything else. */
export type AiGateway = 'founder-os-cloud';

/**
 * The per-project runtime config block. Mirrors the shape of
 * `ProjectDeploymentConfig` in `prisma/schema.prisma`, minus the housekeeping
 * fields (id, timestamps, runtime status flags). The service applies these
 * defaults when a founder picks a mode.
 */
export interface DeploymentModeConfig {
  gitBackend: GitBackend;
  gitUrl: string | null;
  dbProvider: DbProvider;
  dbUrl: string | null;
  hostingType: HostingType;
  hostingUrl: string | null;
  phoneRoute: PhoneRoute;
  aiGateway: AiGateway;
}

/**
 * The Hybrid "publish plan" — applied when the founder clicks Publish. Kept on
 * the config row so there are zero decisions to make at launch time. Null in
 * pure Private mode.
 */
export interface DeploymentPublishPlan {
  targetGithubRepo: string;
  targetNeonProject: string;
  targetVercelProject: string;
  targetDomain: string;
}

/**
 * Full mode descriptor — the default config + (for Hybrid) the default publish
 * plan template. Returned by {@link getDeploymentModeDefaults}.
 */
export interface DeploymentModeDefaults {
  mode: DeploymentModeId;
  config: DeploymentModeConfig;
  /** Only present for HYBRID. PRIVATE and PUBLIC have no publish plan. */
  publishPlan?: DeploymentPublishPlan | null;
}

/**
 * Human-facing metadata for each mode — used by the setup-wizard cards and the
 * dashboard badge. Matches the wording in docs/DEPLOYMENT-MODES-UX.md §2.
 */
export interface DeploymentModeMeta {
  id: DeploymentModeId;
  label: string;
  emoji: string;
  /** One-line pitch for the wizard card. */
  tagline: string;
  /** Tailwind color token for the badge: gray/blue, green, purple. */
  accent: 'slate' | 'emerald' | 'violet';
  /** Whether this is the recommended default (drives the ⭐ badge). */
  recommended?: boolean;
  /** Honest monthly cost line for the wizard card footer. */
  cost: string;
}

export const DEPLOYMENT_MODES: DeploymentModeMeta[] = [
  {
    id: 'PRIVATE',
    label: 'Private',
    emoji: '🖥',
    tagline: 'On your laptop. Free forever. $0/month.',
    accent: 'slate',
    cost: '$0/month',
  },
  {
    id: 'PUBLIC',
    label: 'Public',
    emoji: '☁️',
    tagline: 'On GitHub + Vercel + Neon. Ready for users.',
    accent: 'emerald',
    cost: 'Free tier → usage-based',
  },
  {
    id: 'HYBRID',
    label: 'Hybrid',
    emoji: '🔀',
    tagline: 'Build private → publish to cloud when ready.',
    accent: 'violet',
    recommended: true,
    cost: '$0 until you publish',
  },
];

export function getDeploymentModeMeta(mode: DeploymentModeId): DeploymentModeMeta {
  return DEPLOYMENT_MODES.find((m) => m.id === mode) ?? DEPLOYMENT_MODES[2]!;
}

/**
 * The default config block for a mode. Matches the JSON shapes in
 * docs/DEPLOYMENT-MODES-UX.md §4 verbatim.
 *
 * - PRIVATE: everything on the laptop, Forgejo + SQLite + tunnel-on-demand.
 * - PUBLIC:  GitHub + Postgres + Vercel + public URL.
 * - HYBRID:  starts as Private, carries a publish plan for the Public switch.
 *
 * `projectSlug` is interpolated into the local paths/URLs so each project gets
 * its own Forgejo repo + SQLite file. If omitted, a `<slug>` placeholder is
 * left in the path (the service fills it in once the project exists).
 */
export function getDeploymentModeDefaults(
  mode: DeploymentModeId,
  projectSlug: string = '<slug>',
): DeploymentModeDefaults {
  switch (mode) {
    case 'PRIVATE':
      return {
        mode: 'PRIVATE',
        config: {
          gitBackend: 'forgejo',
          gitUrl: `http://localhost:3000/founder/${projectSlug}.git`,
          dbProvider: 'sqlite',
          dbUrl: `file:./projects/${projectSlug}/dev.db`,
          hostingType: 'tunnel-on-demand',
          hostingUrl: null,
          phoneRoute: 'tailscale',
          aiGateway: 'founder-os-cloud',
        },
        publishPlan: null,
      };

    case 'PUBLIC':
      return {
        mode: 'PUBLIC',
        config: {
          gitBackend: 'github',
          gitUrl: `git@github.com:founder/${projectSlug}.git`,
          dbProvider: 'postgresql',
          // Neon connection string is a secret — left null until the founder
          // connects Neon. The dashboard never renders this raw.
          dbUrl: null,
          hostingType: 'vercel',
          hostingUrl: `https://${projectSlug}.vercel.app`,
          phoneRoute: 'public-url',
          aiGateway: 'founder-os-cloud',
        },
        publishPlan: null,
      };

    case 'HYBRID':
    default:
      return {
        mode: 'HYBRID',
        // Current runtime = Private config (build privately first).
        config: {
          gitBackend: 'forgejo',
          gitUrl: `http://localhost:3000/founder/${projectSlug}.git`,
          dbProvider: 'sqlite',
          dbUrl: `file:./projects/${projectSlug}/dev.db`,
          hostingType: 'tunnel-on-demand',
          hostingUrl: null,
          phoneRoute: 'tailscale',
          aiGateway: 'founder-os-cloud',
        },
        // Publish plan = Public config, applied on Publish click.
        publishPlan: {
          targetGithubRepo: `founder/${projectSlug}`,
          targetNeonProject: `${projectSlug}-prod`,
          targetVercelProject: projectSlug,
          targetDomain: `${projectSlug}.foundersdomain.com`,
        },
      };
  }
}

/**
 * The 4-step publish flow, in order. Used by the API publish stub and the
 * frontend `publish-progress.tsx` so the step labels never drift.
 * See docs/DEPLOYMENT-MODES-UX.md §5.
 */
export const PUBLISH_STEPS = [
  { step: 1, key: 'git-mirror', label: 'Mirroring git history to GitHub' },
  { step: 2, key: 'db-migrate', label: 'Migrating database to Neon' },
  { step: 3, key: 'vercel-deploy', label: 'Deploying to Vercel' },
  { step: 4, key: 'health-verify', label: 'Verifying health' },
] as const;

export type PublishStepKey = (typeof PUBLISH_STEPS)[number]['key'];

/** Normalize an arbitrary string into a valid DeploymentModeId, defaulting to HYBRID. */
export function parseDeploymentMode(value: string | null | undefined): DeploymentModeId {
  if (!value) return 'HYBRID';
  const upper = value.toUpperCase();
  if (upper === 'PRIVATE' || upper === 'PUBLIC' || upper === 'HYBRID') return upper;
  return 'HYBRID';
}
