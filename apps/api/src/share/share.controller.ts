import { Body, Controller, Post } from '@nestjs/common';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { ShareService } from './share.service';
import { ParaphraseShareDto } from './dto/paraphrase.dto';

@Controller('share')
export class ShareController {
  constructor(private readonly share: ShareService) {}

  /**
   * Paraphrase a draft tweet into a clean, Twitter-ready founder-onboarding
   * message via the platform DeepSeek key. JWT-auth (global JwtAuthGuard).
   *
   * Request:  { text: string, projectName?: string, ticker?: string, slug?: string }
   * Response: { text: string }
   */
  @Post('paraphrase')
  paraphrase(
    @CurrentUser() user: AuthUser,
    @Body() dto: ParaphraseShareDto,
  ): Promise<{ text: string }> {
    return this.share.paraphraseTweet(user.id, {
      text: dto.text,
      projectName: dto.projectName,
      ticker: dto.ticker,
      slug: dto.slug,
    });
  }
}
