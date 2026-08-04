import {
  IsArray,
  IsBoolean,
  IsNumber,
  IsObject,
  IsOptional,
  IsString,
  Max,
  Min,
  ValidateNested,
} from 'class-validator';
import { Type } from 'class-transformer';

export type ChatCompletionMessageContentPart =
  | { type: 'text'; text: string }
  | { type: 'image_url'; image_url: { url: string; detail?: 'auto' | 'low' | 'high' } };

export class ChatCompletionMessageDto {
  @IsString()
  role!: string;

  /**
   * OpenAI-compatible message content. May be a plain string (the common
   * case) OR an array of content parts for multimodal requests:
   *   [{ type: 'text', text }, { type: 'image_url', image_url: { url } }]
   *
   * When image parts are present and the resolved route's model has no
   * vision capability, the runtime routes each image through GLM-4V first
   * and replaces the image parts with a text description before invoking the
   * coding model. See VisionPreprocessorService + docs/PRODUCTION-AI-KEYS.md §3.
   *
   * Validation is intentionally permissive — the runtime handles both shapes
   * and coerces bad input to a safe fallback. class-validator's @IsString
   * would reject multimodal arrays; @IsObject would reject plain strings.
   */
  @IsOptional()
  content!: string | ChatCompletionMessageContentPart[];

  @IsOptional()
  @IsString()
  name?: string;
}

export class FimCompletionRequestDto {
  @IsString()
  prefix!: string;

  @IsString()
  suffix!: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  stop?: string[];

  @IsOptional()
  @IsNumber()
  @Min(1)
  @Max(8192)
  max_tokens?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(2)
  temperature?: number;

  @IsOptional()
  @IsString()
  model?: string;
}

export class ChatCompletionRequestDto {
  @IsString()
  model!: string;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => ChatCompletionMessageDto)
  messages!: ChatCompletionMessageDto[];

  @IsOptional()
  @IsBoolean()
  stream?: boolean;

  @IsOptional()
  @IsNumber()
  @Min(1)
  @Max(8192)
  max_tokens?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  @Max(2)
  temperature?: number;

  @IsOptional()
  @IsObject()
  response_format?: { type: 'text' | 'json_object' };

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  stop?: string[];

  @IsOptional()
  @IsObject()
  metadata?: Record<string, unknown>;

  /**
   * Non-standard, opt-in. When true, the streaming response emits a leading
   * `data: {"founderOs":{requestId,tier,provider,model,ddollarCost}}\n\n` line
   * before the OpenAI SSE chunks so the Founder IDE extension's status bar can
   * show the route + cost. Ignored by standard OpenAI clients. See
   * docs/FOUNDER-IDE-FORK-PLAN.md §5.3 / §8.2.
   */
  @IsOptional()
  @IsBoolean()
  founder_os_metadata?: boolean;

  /**
   * Fill-In-the-Middle context for code completion.
   * When present, the runtime constructs a FIM-style prompt from prefix/suffix
   * instead of using the messages array for routing.
   */
  @IsOptional()
  @ValidateNested()
  @Type(() => FimCompletionRequestDto)
  fim?: FimCompletionRequestDto;
}
