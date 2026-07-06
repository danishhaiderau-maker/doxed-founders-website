import { Controller, Get, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { RaiseRoomFilter, RaiseRoomService } from './raise-room.service';

@SkipThrottle()
@Controller('raise-room')
export class RaiseRoomController {
  constructor(private readonly raiseRoom: RaiseRoomService) {}

  @Public()
  @Get('dashboard')
  dashboard() {
    return this.raiseRoom.getDashboard();
  }

  @Public()
  @Get('projects')
  projects(
    @Query('filter') filter?: string,
    @Query('limit') limit?: string,
  ) {
    const allowed: RaiseRoomFilter[] = [
      'trending',
      'newest',
      'almost_qualified',
      'ai_picks',
      'high_conviction',
      'near_graduation',
      'needs_review',
    ];
    const f = allowed.includes(filter as RaiseRoomFilter)
      ? (filter as RaiseRoomFilter)
      : 'trending';
    return this.raiseRoom.getProjects(f, limit ? Number(limit) : 48);
  }
}
