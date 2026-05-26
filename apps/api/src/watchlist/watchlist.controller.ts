import {
  Controller,
  Delete,
  Get,
  Param,
  Post,
  Req,
} from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AuthUser } from '../auth/auth.types';
import { WatchlistService } from './watchlist.service';

@SkipThrottle()
@Controller('watchlist')
export class WatchlistController {
  constructor(private readonly watchlist: WatchlistService) {}

  @Get()
  list(@Req() req: { user: AuthUser }) {
    return this.watchlist.list(req.user.id);
  }

  @Get('slugs')
  slugs(@Req() req: { user: AuthUser }) {
    return this.watchlist.listSlugs(req.user.id);
  }

  @Post(':slug')
  add(@Req() req: { user: AuthUser }, @Param('slug') slug: string) {
    return this.watchlist.add(req.user.id, slug);
  }

  @Delete(':slug')
  remove(@Req() req: { user: AuthUser }, @Param('slug') slug: string) {
    return this.watchlist.remove(req.user.id, slug);
  }
}
