import { DeploymentMode, PublishJobStatus } from '@prisma/client';

/**
 * Phase 7 — Deployment Modes type definitions.
 * See docs/DEPLOYMENT-MODES-UX.md §4 for the per-mode config shapes.
 */

export type GitBackend = 'forgejo' | 'github';
export type DbProvider = 'sqlite' | 'postgresql';
export type HostingType = 'tunnel-on-demand' | 'vercel' | 'custom';
export type PhoneRoute = 'tailscale' | 'public-url';
export type AiGateway = 'founder-os-cloud';

/** The Hybrid-mode publish plan — applied atomically on Publish. Spec §4. */
export interface PublishPlan {
  targetGithubRepo: string;
  targetNeonProject: string;
  targetVercelProject: string;
  targetDomain: string;
}

/** A single step in a publish job's progress (spec §5). */
export interface PublishStep {
  step: number; // 1..4
  label: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  detail?: string;
  startedAt?: string;
  finishedAt?: string;
}

/** PATCH body for updating a project's deployment config. */
export interface UpdateDeploymentConfigInput {
  deploymentMode?: DeploymentMode;
  gitBackend?: GitBackend;
  gitUrl?: string | null;
  dbProvider?: DbProvider;
  dbUrl?: string | null;
  hostingType?: HostingType;
  hostingUrl?: string | null;
  phoneRoute?: PhoneRoute;
  aiGateway?: AiGateway;
  publishPlan?: PublishPlan | null;
}

/** Status snapshot the mode panel renders as "What's running right now". */
export interface RuntimeStatusReport {
  forgejoOnline: boolean;
  sqlitePresent: boolean;
  tunnelActive: boolean;
  tailscaleReady: boolean;
  reportedAt?: string;
}

/** What the controller returns from the publish endpoint. */
export interface PublishResult {
  jobId: string;
  status: PublishJobStatus;
  currentStep: number;
  steps: PublishStep[];
}

export const PUBLISH_STEP_LABELS: Record<number, string> = {
  1: 'Mirroring git history to GitHub',
  2: 'Migrating database to Neon',
  3: 'Deploying to Vercel',
  4: 'Verifying health',
};
