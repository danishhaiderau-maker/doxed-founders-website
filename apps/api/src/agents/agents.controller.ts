import { Body, Controller, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { AgentCategory } from '@prisma/client';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { CreateAgentDto, RateAgentDto, RunAgentDto } from './dto/agents.dto';
import { AgentsService } from './agents.service';

@Controller('agents')
export class AgentsController {
  constructor(private readonly agents: AgentsService) {}

  @Public()
  @Get()
  list(@Query('category') category?: AgentCategory) {
    return this.agents.listPublic(category);
  }

  @Get('my/list')
  mine(@CurrentUser() user: AuthUser) {
    return this.agents.myAgents(user.id);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get(':slug')
  getBySlug(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.agents.getBySlug(slug, user?.id);
  }

  @Post()
  create(@CurrentUser() user: AuthUser, @Body() dto: CreateAgentDto) {
    return this.agents.createAgent(user.id, dto);
  }

  @Post(':id/install')
  install(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.agents.installAgent(user.id, id);
  }

  @Post(':id/follow')
  follow(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.agents.followAgent(user.id, id);
  }

  @Post(':id/run')
  run(@CurrentUser() user: AuthUser, @Param('id') id: string, @Body() dto: RunAgentDto) {
    return this.agents.runAgent(user.id, id, dto.prompt);
  }

  @Post(':id/rate')
  rate(@CurrentUser() user: AuthUser, @Param('id') id: string, @Body() dto: RateAgentDto) {
    return this.agents.rateAgent(user.id, id, dto.rating);
  }
}
