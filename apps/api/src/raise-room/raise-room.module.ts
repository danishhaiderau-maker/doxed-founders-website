import { Module } from '@nestjs/common';
import { RaiseRoomController } from './raise-room.controller';
import { RaiseRoomService } from './raise-room.service';

@Module({
  controllers: [RaiseRoomController],
  providers: [RaiseRoomService],
  exports: [RaiseRoomService],
})
export class RaiseRoomModule {}
