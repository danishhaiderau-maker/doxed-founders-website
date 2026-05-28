import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { SocialSignalsService } from './social-signals.service';
import { XPostingService } from './x-posting.service';
import { XSocialController } from './x-social.controller';

@Module({
  imports: [NotificationsModule, AuthModule],
  controllers: [XSocialController],
  providers: [XPostingService, SocialSignalsService],
  exports: [XPostingService, SocialSignalsService],
})
export class XSocialModule {}
