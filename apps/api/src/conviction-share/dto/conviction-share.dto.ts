import { IsNumber, IsString, MaxLength, MinLength } from 'class-validator';
import { Type } from 'class-transformer';

export class PostConvictionShareDto {
  @IsString()
  projectId!: string;

  @IsString()
  @MinLength(10)
  @MaxLength(280)
  text!: string;

  @Type(() => Number)
  @IsNumber()
  pnlPercent!: number;
}
