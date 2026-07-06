import { Module } from '@nestjs/common';
import { LaunchQualificationController } from './launch-qualification.controller';
import {
  LaunchQualificationService,
  Phase15GatesService,
} from './launch-qualification.service';

@Module({
  controllers: [LaunchQualificationController],
  providers: [LaunchQualificationService, Phase15GatesService],
  exports: [LaunchQualificationService, Phase15GatesService],
})
export class LaunchQualificationModule {}
