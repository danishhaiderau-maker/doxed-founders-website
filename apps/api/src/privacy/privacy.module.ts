import { Module } from '@nestjs/common';
import { CredentialsModule } from '../credentials/credentials.module';
import { DataClassificationService } from './data-classification.service';
import { PrivacyController } from './privacy.controller';

@Module({
  imports: [CredentialsModule],
  controllers: [PrivacyController],
  providers: [DataClassificationService],
  exports: [DataClassificationService],
})
export class PrivacyModule {}
