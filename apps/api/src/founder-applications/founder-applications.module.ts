import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { FounderApplicationsController } from './founder-applications.controller';

@Module({
  imports: [PrismaModule],
  controllers: [FounderApplicationsController],
})
export class FounderApplicationsModule {}
