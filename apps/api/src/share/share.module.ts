import { Module } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ShareController } from './share.controller';
import { ShareService } from './share.service';

@Module({
  imports: [FounderOsModule],
  controllers: [ShareController],
  providers: [ShareService],
})
export class ShareModule {}
