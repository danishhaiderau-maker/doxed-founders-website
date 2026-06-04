import { Body, Controller, Get, Post } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { VaultCvmService } from './vault-cvm.service';

@Controller('vault')
export class VaultController {
  constructor(private readonly vaultCvm: VaultCvmService) {}

  @Public()
  @Get('cvm-capabilities')
  capabilities() {
    return this.vaultCvm.getCapabilities();
  }

  @Get('cvm-status')
  cvmStatus(@CurrentUser() user: AuthUser) {
    return this.vaultCvm.getStatus(user.id);
  }

  @Post('cvm-backup-request')
  requestBackup(@CurrentUser() user: AuthUser) {
    return this.vaultCvm.requestBackup(user.id);
  }

  @Post('cvm-verify')
  verifyBackup(@CurrentUser() user: AuthUser, @Body() body: { logId?: string }) {
    return this.vaultCvm.verifyBackup(user.id, body.logId);
  }
}
