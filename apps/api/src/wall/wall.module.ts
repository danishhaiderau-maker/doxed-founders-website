import { Global, Module, forwardRef } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ProjectsModule } from '../projects/projects.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { MessagesModule } from '../messages/messages.module';
import { WallController } from './wall.controller';
import { WallService } from './wall.service';

@Global()
@Module({
  imports: [
    FounderOsModule,
    ProjectsModule,
    forwardRef(() => NotificationsModule),
    forwardRef(() => MessagesModule),
  ],
  controllers: [WallController],
  providers: [WallService],
  exports: [WallService],
})
export class WallModule {}
