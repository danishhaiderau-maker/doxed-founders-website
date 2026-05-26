import { IsEmail, IsIn, IsOptional, IsString, MinLength } from 'class-validator';

export class OAuthLoginDto {
  @IsEmail()
  email!: string;

  @IsIn(['google'])
  provider!: 'google';

  @IsString()
  @MinLength(1)
  providerId!: string;

  @IsOptional()
  @IsString()
  name?: string;

  @IsOptional()
  @IsString()
  avatarUrl?: string;
}
