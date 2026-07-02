import { Global, Module } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ProjectsModule } from '../projects/projects.module';
import { WallController } from './wall.controller';
import { WallService } from './wall.service';

@Global()
@Module({
  imports: [FounderOsModule, ProjectsModule],
  controllers: [WallController],
  providers: [WallService],
  exports: [WallService],
})
export class WallModule {}
