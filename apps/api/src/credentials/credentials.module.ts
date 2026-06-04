import { Global, Module } from '@nestjs/common';
import { CredentialsCryptoService } from './credentials-crypto.service';
import { CvmSealService } from './cvm-seal.service';
import { SealedCredentialsService } from './sealed-credentials.service';

@Global()
@Module({
  providers: [CredentialsCryptoService, CvmSealService, SealedCredentialsService],
  exports: [CredentialsCryptoService, CvmSealService, SealedCredentialsService],
})
export class CredentialsModule {}
