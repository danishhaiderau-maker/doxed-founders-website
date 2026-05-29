import { Module } from '@nestjs/common';
import { ConvictionShareController } from './conviction-share.controller';
import { ConvictionShareService } from './conviction-share.service';
import { XSocialModule } from '../x-social/x-social.module';

@Module({
  imports: [XSocialModule],
  controllers: [ConvictionShareController],
  providers: [ConvictionShareService],
  exports: [ConvictionShareService],
})
export class ConvictionShareModule {}
