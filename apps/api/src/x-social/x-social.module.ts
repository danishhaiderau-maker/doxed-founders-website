import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { SocialSignalsService } from './social-signals.service';
import { XPostingService } from './x-posting.service';
import { XShareMediaService } from './x-share-media.service';
import { UserXPostingService } from './user-x-posting.service';
import { XSocialController } from './x-social.controller';

@Module({
  imports: [NotificationsModule, AuthModule],
  controllers: [XSocialController],
  providers: [XPostingService, SocialSignalsService, XShareMediaService, UserXPostingService],
  exports: [XPostingService, SocialSignalsService, XShareMediaService, UserXPostingService],
})
export class XSocialModule {}
