import { Module } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ObservatoryController } from './observatory.controller';
import { ObservatoryService } from './observatory.service';

@Module({
  imports: [FounderOsModule],
  controllers: [ObservatoryController],
  providers: [ObservatoryService],
  exports: [ObservatoryService],
})
export class ObservatoryModule {}
