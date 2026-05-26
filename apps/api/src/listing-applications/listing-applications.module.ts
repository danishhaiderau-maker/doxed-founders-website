import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { ListingApplicationsController } from './listing-applications.controller';
import { ListingApplicationsService } from './listing-applications.service';
import { ListingPublishService } from './listing-publish.service';

@Module({
  imports: [DexscreenerModule, AuthModule],
  controllers: [ListingApplicationsController],
  providers: [ListingApplicationsService, ListingPublishService],
})
export class ListingApplicationsModule {}
