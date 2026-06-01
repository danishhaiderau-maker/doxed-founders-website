import { Module } from '@nestjs/common';
import { CredentialCryptoService } from './credential-crypto.service';
import { ExchangesController } from './exchanges.controller';
import { ExchangesService } from './exchanges.service';

@Module({
  controllers: [ExchangesController],
  providers: [ExchangesService, CredentialCryptoService],
  exports: [ExchangesService, CredentialCryptoService],
})
export class ExchangesModule {}
