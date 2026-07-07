import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { FlightRecorderController } from './flight-recorder.controller';
import { FlightRecorderService } from './flight-recorder.service';

@Module({
  imports: [PrismaModule],
  controllers: [FlightRecorderController],
  providers: [FlightRecorderService],
  exports: [FlightRecorderService],
})
export class FlightRecorderModule {}
