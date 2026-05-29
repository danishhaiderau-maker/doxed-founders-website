import { IsEnum, IsInt, IsOptional, IsString, Max, MaxLength, Min, MinLength } from 'class-validator';
import { AgentCategory, AgentWorkforceTemplate } from '@prisma/client';

export class CreateAgentDto {
  @IsString()
  @MinLength(2)
  @MaxLength(80)
  name!: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  description?: string;

  @IsEnum(AgentCategory)
  category!: AgentCategory;

  @IsOptional()
  @IsEnum(AgentWorkforceTemplate)
  template?: AgentWorkforceTemplate;

  @IsOptional()
  @IsString()
  projectId?: string;
}

export class RunAgentDto {
  @IsString()
  @MinLength(3)
  @MaxLength(4000)
  prompt!: string;
}

export class RateAgentDto {
  @IsInt()
  @Min(1)
  @Max(5)
  rating!: number;
}
