import { Module } from '@nestjs/common';
import { RegulatoryController } from './regulatory.controller';
import { RegulatoryMetaController } from './regulatory-meta.controller';
import { RegulatoryService } from './regulatory.service';

@Module({
  controllers: [RegulatoryController, RegulatoryMetaController],
  providers: [RegulatoryService],
  exports: [RegulatoryService],
})
export class RegulatoryModule {}
