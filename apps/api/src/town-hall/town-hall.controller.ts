import { Body, Controller, Delete, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { TownHallCategory } from '@prisma/client';
import { AdminGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { TownHallService } from './town-hall.service';

@SkipThrottle()
@Controller('town-hall')
export class TownHallController {
  constructor(private readonly townHall: TownHallService) {}

  @Public()
  @Get()
  list(@Query('limit') limit?: string) {
    return this.townHall.listPublic(limit ? Number(limit) : 30);
  }

  @UseGuards(AdminGuard)
  @Post()
  create(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      title: string;
      body: string;
      category?: TownHallCategory;
      pinned?: boolean;
      featured?: boolean;
    },
  ) {
    return this.townHall.create(user.id, body);
  }

  @UseGuards(AdminGuard)
  @Delete(':id')
  remove(@Param('id') id: string) {
    return this.townHall.remove(id);
  }
}
