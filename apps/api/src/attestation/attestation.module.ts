import { Module } from '@nestjs/common';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { AttestationController } from './attestation.controller';
import { AttestationService } from './attestation.service';

@Module({
  imports: [FounderNodeModule],
  controllers: [AttestationController],
  providers: [AttestationService],
  exports: [AttestationService],
})
export class AttestationModule {}
