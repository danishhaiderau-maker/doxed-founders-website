import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { SocialSignalsService } from './social-signals.service';
import { XPostingService } from './x-posting.service';
import { XShareMediaService } from './x-share-media.service';
import { XSocialController } from './x-social.controller';

@Module({
  imports: [NotificationsModule, AuthModule],
  controllers: [XSocialController],
  providers: [XPostingService, SocialSignalsService, XShareMediaService],
  exports: [XPostingService, SocialSignalsService, XShareMediaService],
})
export class XSocialModule {}
