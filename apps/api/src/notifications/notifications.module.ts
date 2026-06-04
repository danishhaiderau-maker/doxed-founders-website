import { Module, forwardRef } from '@nestjs/common';
import { MessagesModule } from '../messages/messages.module';
import { NotificationsController } from './notifications.controller';
import { HighValueInsightsService } from './high-value-insights.service';
import { NotificationsService } from './notifications.service';

@Module({
  imports: [forwardRef(() => MessagesModule)],
  controllers: [NotificationsController],
  providers: [NotificationsService, HighValueInsightsService],
  exports: [NotificationsService, HighValueInsightsService],
})
export class NotificationsModule {}
