import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { AttestationService } from './attestation.service';

@Controller('attestation')
export class AttestationController {
  constructor(private readonly attestation: AttestationService) {}

  @Get('dashboard')
  dashboard(@CurrentUser() user: AuthUser) {
    return this.attestation.getDashboard(user.id);
  }

  @Post('vault-scan')
  vaultScan(@CurrentUser() user: AuthUser) {
    return this.attestation.scanVaultIntegrity(user.id);
  }

  @Post('phala/verify')
  verifyPhala(@CurrentUser() user: AuthUser, @Body() body: { logId?: string }) {
    return this.attestation.verifyPhalaLog(user.id, body.logId);
  }

  @Get('phala/report')
  liveReport(
    @CurrentUser() user: AuthUser,
    @Query('model') model?: string,
    @Query('signingAddress') signingAddress?: string,
  ) {
    return this.attestation.fetchLiveAttestation(user.id, model, signingAddress);
  }
}
