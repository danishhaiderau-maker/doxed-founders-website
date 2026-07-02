import { Global, Module } from '@nestjs/common';
import { WallController } from './wall.controller';
import { WallService } from './wall.service';

@Global()
@Module({
  controllers: [WallController],
  providers: [WallService],
  exports: [WallService],
})
export class WallModule {}
