import { Controller, Get } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { DataClassificationService } from './data-classification.service';

@SkipThrottle()
@Controller('privacy')
export class PrivacyController {
  constructor(private readonly classification: DataClassificationService) {}

  @Public()
  @Get('data-classes')
  dataClasses() {
    return this.classification.getOverview();
  }

  @Public()
  @Get('audit')
  audit() {
    return this.classification.getRuntimeAudit();
  }

  @Get('my-boundaries')
  myBoundaries(@CurrentUser() user: AuthUser) {
    return this.classification.getMyBoundaries(user.id);
  }
}
