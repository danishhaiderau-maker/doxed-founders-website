import { Module, forwardRef } from '@nestjs/common';
import { ListingApplicationsModule } from '../listing-applications/listing-applications.module';
import { PointsModule } from '../points/points.module';
import { TrustCenterController } from './trust-center.controller';
import { TrustCenterService } from './trust-center.service';
import { TrustWeightService } from './trust-weight.service';

@Module({
  imports: [PointsModule, forwardRef(() => ListingApplicationsModule)],
  controllers: [TrustCenterController],
  providers: [TrustCenterService, TrustWeightService],
  exports: [TrustCenterService, TrustWeightService],
})
export class TrustCenterModule {}
