import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { CapabilityRegistryService } from './capability-registry.service';

@Module({
  imports: [PrismaModule],
  providers: [CapabilityRegistryService],
  exports: [CapabilityRegistryService],
})
export class CapabilityRegistryModule {}
