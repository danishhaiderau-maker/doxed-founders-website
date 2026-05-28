import { Global, Module } from '@nestjs/common';
import { PointsService } from './points.service';

@Global()
@Module({
  providers: [PointsService],
  exports: [PointsService],
})
export class PointsModule {}
