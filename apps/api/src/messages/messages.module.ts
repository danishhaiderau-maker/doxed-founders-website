import { Module, forwardRef } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { MessagesController } from './messages.controller';
import { MessagesService } from './messages.service';
import { ChatEventsService } from './chat-events.service';

@Module({
  imports: [forwardRef(() => NotificationsModule)],
  controllers: [MessagesController],
  providers: [MessagesService, ChatEventsService],
  exports: [MessagesService, ChatEventsService],
})
export class MessagesModule {}
