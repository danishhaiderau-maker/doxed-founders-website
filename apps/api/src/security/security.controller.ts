import { Body, Controller, Delete, Get, Param, Post } from '@nestjs/common';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import {
  ChangePasswordDto,
  PasskeyVerifyDto,
  RenamePasskeyDto,
  Verify2FaLoginDto,
  VerifyTotpDto,
  WalletVerifyDto,
} from './dto/security.dto';
import { SecurityService } from './security.service';

@Controller('security')
export class SecurityController {
  constructor(private readonly security: SecurityService) {}

  @Get('profile')
  profile(@CurrentUser() user: AuthUser) {
    return this.security.getSecurityProfile(user.id);
  }

  @Post('password')
  changePassword(@CurrentUser() user: AuthUser, @Body() dto: ChangePasswordDto) {
    return this.security.changePassword(user.id, dto);
  }

  @Post('totp/setup')
  setupTotp(@CurrentUser() user: AuthUser) {
    return this.security.setupTotp(user.id);
  }

  @Post('totp/enable')
  enableTotp(@CurrentUser() user: AuthUser, @Body() dto: VerifyTotpDto) {
    return this.security.enableTotp(user.id, dto.code);
  }

  @Post('totp/disable')
  disableTotp(@CurrentUser() user: AuthUser, @Body() dto: VerifyTotpDto) {
    return this.security.disableTotp(user.id, dto.code);
  }

  @Post('recovery-codes')
  recoveryCodes(@CurrentUser() user: AuthUser, @Body() dto: VerifyTotpDto) {
    return this.security.generateRecoveryCodes(user.id, dto.code);
  }

  @Post('passkey/register-options')
  passkeyRegisterOptions(@CurrentUser() user: AuthUser) {
    return this.security.passkeyRegisterOptions(user.id);
  }

  @Post('passkey/register-verify')
  passkeyRegisterVerify(
    @CurrentUser() user: AuthUser,
    @Body() body: { registerToken: string; response: Record<string, unknown>; label?: string },
  ) {
    return this.security.passkeyRegisterVerify(
      user.id,
      body.registerToken,
      body.response as never,
      body.label,
    );
  }

  @Public()
  @Post('passkey/login-options')
  passkeyLoginOptions(@Body() body: { pendingToken: string }) {
    return this.security.passkeyLoginOptions(body.pendingToken);
  }

  @Public()
  @Post('passkey/login-verify')
  passkeyLoginVerify(@Body() dto: PasskeyVerifyDto) {
    return this.security.passkeyLoginVerify(dto.passkeyToken, dto.response as never);
  }

  @Delete('passkey/:credentialId')
  deletePasskey(@CurrentUser() user: AuthUser, @Param('credentialId') credentialId: string) {
    return this.security.deletePasskey(user.id, credentialId);
  }

  @Post('passkey/rename')
  renamePasskey(@CurrentUser() user: AuthUser, @Body() dto: RenamePasskeyDto) {
    return this.security.renamePasskey(user.id, dto.credentialId, dto.label);
  }

  @Post('wallet/challenge')
  walletChallenge(@CurrentUser() user: AuthUser) {
    return this.security.walletChallenge(user.id);
  }

  @Post('wallet/verify')
  walletVerify(
    @CurrentUser() user: AuthUser,
    @Body() body: WalletVerifyDto & { challengeToken: string },
  ) {
    return this.security.walletVerify(
      user.id,
      body.challengeToken,
      body.address,
      body.signature,
      body.message,
    );
  }

  @Delete('wallet')
  disconnectWallet(@CurrentUser() user: AuthUser) {
    return this.security.disconnectWallet(user.id);
  }
}
