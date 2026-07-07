import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { FlightRecorderService } from './flight-recorder.service';

@Module({
  imports: [PrismaModule],
  providers: [FlightRecorderService],
  exports: [FlightRecorderService],
})
export class FlightRecorderModule {}
