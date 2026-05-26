import { Module } from '@nestjs/common';
import { GeckoterminalService } from './geckoterminal.service';

@Module({
  providers: [GeckoterminalService],
  exports: [GeckoterminalService],
})
export class GeckoterminalModule {}
