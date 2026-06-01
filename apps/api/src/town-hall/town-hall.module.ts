import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { TownHallController } from './town-hall.controller';
import { TownHallService } from './town-hall.service';

@Module({
  imports: [AuthModule],
  controllers: [TownHallController],
  providers: [TownHallService],
})
export class TownHallModule {}
