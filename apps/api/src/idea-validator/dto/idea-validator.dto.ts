import {
  IsBoolean,
  IsOptional,
  IsString,
  MaxLength,
  MinLength,
} from 'class-validator';
import { Transform } from 'class-transformer';

function emptyToUndefined({ value }: { value: unknown }) {
  if (value === '' || value === null || value === undefined) return undefined;
  return value;
}

/**
 * POST /idea-validator/check — kick off (or reuse) an idea check.
 * The check is async: the controller returns 202 with the row id, the
 * client polls GET /idea-validator/check/:id.
 */
export class CheckIdeaDto {
  @IsString()
  @MinLength(20)
  @MaxLength(5000)
  ideaText!: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  projectId?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  applicationId?: string;

  /**
   * Force a fresh search even if a recent check exists for the same idea
   * text within the 24h idempotency window.
   */
  @Transform(emptyToUndefined)
  @IsOptional()
  @IsBoolean()
  force?: boolean;
}

/**
 * PATCH /idea-validator/check/:id — dismiss or mark-viewed. Used by the
 * daily pop-up ("dismiss") and the result panel ("I've seen it").
 */
export class PatchIdeaCheckDto {
  @Transform(emptyToUndefined)
  @IsOptional()
  @IsBoolean()
  dismissed?: boolean;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsBoolean()
  viewed?: boolean;
}
