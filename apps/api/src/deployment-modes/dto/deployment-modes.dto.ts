import { IsIn, IsObject, IsOptional, IsString } from 'class-validator';
import { Transform } from 'class-transformer';
import type { DeploymentModeId } from '@dcf/utils';

function emptyToUndefined({ value }: { value: unknown }) {
  if (value === '' || value === null || value === undefined) return undefined;
  return value;
}

/**
 * PATCH /api/projects/:slug/deployment-mode — change a project's mode and/or
 * its per-project config block. Mode changes are non-destructive: the founder
 * can flip PRIVATE → PUBLIC → HYBRID freely. The config row is created lazily.
 */
export class PatchDeploymentModeDto {
  @Transform(emptyToUndefined)
  @IsOptional()
  @IsIn(['PRIVATE', 'PUBLIC', 'HYBRID'])
  mode?: DeploymentModeId;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  gitBackend?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  gitUrl?: string | null;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  dbProvider?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  dbUrl?: string | null;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  hostingType?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  hostingUrl?: string | null;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  phoneRoute?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsObject()
  publishPlan?: Record<string, unknown> | null;
}

/**
 * POST /api/projects/:slug/deployment-mode/publish — kick off the Hybrid →
 * Public promotion. The body overrides the stored publish plan so the founder
 * can tweak the target repo / domain at launch time. Returns the created job.
 */
export class PublishDeploymentDto {
  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  targetGithubRepo?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  targetNeonProject?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  targetVercelProject?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  targetDomain?: string;
}
