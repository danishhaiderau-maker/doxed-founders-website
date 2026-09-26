import { Controller, Get, Req, UnauthorizedException, UseGuards } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { FounderCopilotService } from '../events/founder-copilot.service';
import { NUCLEUS_AUTH_HELP } from './ide-nucleus-auth';
import {
  IdeNucleusAuthGuard,
  type NucleusAuthedRequest,
} from './ide-nucleus-auth.guard';

/**
 * Founder Graph read for Nucleus.
 *
 * `@Public()` skips the global JWT guard so a Founder Node header is not
 * rejected as a JWT before this guard runs. The route still requires one of:
 *
 * - Browser session JWT: `Authorization: Bearer <accessToken>`
 * - Founder Node: `Authorization: FounderNode {nodeId}:{nodeToken}`
 * - OpenAI-compat node bearer: `Authorization: Bearer fos_{nodeId}:{nodeToken}`
 *
 * `GET /copilot/founder-graph` remains session-JWT only.
 * The response is the stored graph (labels and edges). It does not include
 * node tokens, API keys, or vault file bodies.
 */
@Public()
@UseGuards(IdeNucleusAuthGuard)
@Controller('ide')
export class IdeNucleusController {
  constructor(private readonly copilot: FounderCopilotService) {}

  @Get('nucleus')
  async nucleus(@Req() req: NucleusAuthedRequest) {
    const caller = req.nucleusCaller;
    if (!caller?.userId) {
      throw new UnauthorizedException(NUCLEUS_AUTH_HELP);
    }
    const payload = await this.copilot.getFounderGraphForUser(caller.userId);
    return {
      ...payload,
      auth: caller.auth,
    };
  }
}
