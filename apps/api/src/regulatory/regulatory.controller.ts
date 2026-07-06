import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { JwtAuthGuard } from '../auth/guards';
import { Public } from '../auth/public.decorator';
import {
  RegulatoryService,
  type RegulatoryQuestionnaireAnswers,
} from './regulatory.service';

@Controller('projects')
export class RegulatoryController {
  constructor(private readonly regulatory: RegulatoryService) {}

  @Public()
  @Get(':slug/regulatory')
  getClassification(@Param('slug') slug: string) {
    return this.regulatory.getBySlug(slug);
  }

  @Post(':slug/regulatory/questionnaire')
  @UseGuards(JwtAuthGuard)
  submitQuestionnaire(
    @Param('slug') slug: string,
    @CurrentUser() user: AuthUser,
    @Body() body: RegulatoryQuestionnaireAnswers,
  ) {
    return this.regulatory.submitQuestionnaire(slug, user.id, body);
  }
}
