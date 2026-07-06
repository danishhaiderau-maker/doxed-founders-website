import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DdollarModule } from '../ddollar/ddollar.module';
import { FounderDenModule } from '../founder-den/founder-den.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ProjectsModule } from '../projects/projects.module';
import { DemoController } from './demo.controller';
import { DemoModeGuard } from './demo-mode.guard';
import { DemoSeedService } from './demo-seed.service';

@Module({
  imports: [
    AuthModule,
    ProjectsModule,
    FounderDenModule,
    FounderOsModule,
    DdollarModule,
  ],
  controllers: [DemoController],
  providers: [DemoSeedService, DemoModeGuard],
  exports: [DemoSeedService],
})
export class DemoModule {}
