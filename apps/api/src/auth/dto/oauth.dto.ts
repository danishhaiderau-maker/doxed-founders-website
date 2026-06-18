import { IsBoolean, IsEmail, IsIn, IsOptional, IsString, MaxLength, MinLength, ValidateIf } from 'class-validator';

export class OAuthLoginDto {
  @ValidateIf((o: OAuthLoginDto) => o.provider === 'google')
  @IsEmail()
  email?: string;

  @IsIn(['google', 'twitter'])
  provider!: 'google' | 'twitter';

  @IsString()
  @MinLength(1)
  providerId!: string;

  @IsOptional()
  @IsString()
  name?: string;

  @IsOptional()
  @IsString()
  avatarUrl?: string;

  /** @handle without @ — set when provider is twitter */
  @IsOptional()
  @IsString()
  @MinLength(1)
  twitterHandle?: string;

  /** OAuth 1.0a user tokens for one-click X posting */
  @IsOptional()
  @IsString()
  oauthAccessToken?: string;

  @IsOptional()
  @IsString()
  oauthAccessTokenSecret?: string;

  @IsOptional()
  @IsBoolean()
  xVerified?: boolean;

  @IsOptional()
  @IsString()
  @MinLength(6)
  @MaxLength(16)
  referralCode?: string;
}
