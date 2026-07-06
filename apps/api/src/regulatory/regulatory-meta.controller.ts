import { Controller, Get } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { RegulatoryService } from './regulatory.service';

@Controller('regulatory')
export class RegulatoryMetaController {
  constructor(private readonly regulatory: RegulatoryService) {}

  @Public()
  @Get('questionnaire-template')
  questionnaireTemplate() {
    return this.regulatory.getQuestionnaireTemplate();
  }
}
