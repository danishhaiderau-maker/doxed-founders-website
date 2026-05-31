import { Module } from '@nestjs/common';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { AttestationController } from './attestation.controller';
import { AttestationService } from './attestation.service';

@Module({
  imports: [FounderNodeModule],
  controllers: [AttestationController],
  providers: [AttestationService, CredentialsCryptoService],
  exports: [AttestationService],
})
export class AttestationModule {}
