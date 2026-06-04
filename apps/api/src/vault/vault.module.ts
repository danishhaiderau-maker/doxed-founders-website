import { Module } from '@nestjs/common';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { VaultController } from './vault.controller';
import { VaultCvmService } from './vault-cvm.service';

@Module({
  imports: [FounderNodeModule],
  controllers: [VaultController],
  providers: [VaultCvmService],
  exports: [VaultCvmService],
})
export class VaultModule {}
