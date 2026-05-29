import { IsOptional, IsString, MaxLength, MinLength } from 'class-validator';

export class ChangePasswordDto {
  @IsString()
  @MinLength(8)
  @MaxLength(72)
  currentPassword!: string;

  @IsString()
  @MinLength(8)
  @MaxLength(72)
  newPassword!: string;
}

export class VerifyTotpDto {
  @IsString()
  @MinLength(6)
  @MaxLength(8)
  code!: string;
}

export class Verify2FaLoginDto {
  @IsString()
  pendingToken!: string;

  @IsOptional()
  @IsString()
  @MinLength(6)
  @MaxLength(16)
  totpCode?: string;

  @IsOptional()
  @IsString()
  recoveryCode?: string;
}

export class WalletVerifyDto {
  @IsString()
  @MinLength(32)
  @MaxLength(64)
  address!: string;

  @IsString()
  @MinLength(32)
  signature!: string;

  @IsString()
  message!: string;
}

export class PasskeyVerifyDto {
  @IsString()
  passkeyToken!: string;

  response!: Record<string, unknown>;
}

export class RenamePasskeyDto {
  @IsString()
  credentialId!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(64)
  label!: string;
}
