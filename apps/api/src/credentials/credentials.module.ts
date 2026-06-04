import { Global, Module } from '@nestjs/common';
import { CredentialsCryptoService } from './credentials-crypto.service';
import { SealedCredentialsService } from './sealed-credentials.service';

@Global()
@Module({
  providers: [CredentialsCryptoService, SealedCredentialsService],
  exports: [CredentialsCryptoService, SealedCredentialsService],
})
export class CredentialsModule {}
