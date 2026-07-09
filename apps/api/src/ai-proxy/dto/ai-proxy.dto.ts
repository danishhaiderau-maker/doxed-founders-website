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

export class ChatCompletionMessageDto {
  @IsString()
  role!: string;

  @IsString()
  content!: string;

  @IsOptional()
  @IsString()
  name?: string;
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
}
